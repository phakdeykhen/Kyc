"""Restricted-role integration transactions and concurrent tenant quotas on disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import unittest
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.db.models import Application, ExternalSubject, VerificationBinding, VerificationDecision
from kyc.db.models import Organization
from kyc.db.privileges import grant_statements
from kyc.db.session import set_tenant
from kyc.integrations.schemas import VerificationCreate
from kyc.integrations.service import create_verification, snapshot
from kyc.services.sessions import apply_event
from kyc.domain.state_machine import Event
from tests.test_api import configuration

URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(URL, "TEST_DATABASE_URL must point to a disposable database ending _test.")
class IntegrationPostgreSQLTests(unittest.TestCase):
    def test_rls_composite_links_atomic_results_and_concurrent_quota(self):
        if not sa.engine.make_url(URL).database.endswith("_test"):
            self.fail("A disposable database ending _test is required")
        admin = sa.create_engine(URL)
        suffix = uuid4().hex[:12]
        schema, role = "integration_test_" + suffix, "integration_role_" + suffix
        runtime = None
        tenants, apps = [uuid4(), uuid4()], [uuid4(), uuid4(), uuid4()]
        try:
            with admin.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.attributes.update(connection=connection, database_url=URL)
                command.upgrade(config, "head")
                connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                for sql in grant_statements(role, schema):
                    connection.execute(sa.text(sql))
                with Session(connection, join_transaction_mode="create_savepoint") as db, db.begin():
                    for tenant in tenants:
                        set_tenant(db, tenant)
                        db.add(Organization(id=tenant, name="Synthetic integration tenant", daily_session_quota=1))
                        db.flush()
                        db.add(ExternalSubject(organization_id=tenant, external_id="EMP-00123"))
                    for identifier, tenant in zip(apps, [tenants[0], tenants[0], tenants[1]]):
                        set_tenant(db, tenant)
                        db.add(Application(id=identifier, organization_id=tenant, name="Synthetic app", mode="sandbox",
                            workflows={"test": {"country": "KH", "expected_document_type": "KH_NATIONAL_ID", "verification_level": "DOCUMENT_FACE_LIVENESS"}},
                            return_urls=["https://1.1.1.1/return"], branding={"display_name": "Synthetic"}))
                        db.flush()
            runtime = sa.create_engine(URL, connect_args={"options": f"-csearch_path={schema} -crole={role}"})
            body = VerificationCreate(external_subject_id="EMP-00123", workflow_id="test", return_url="https://1.1.1.1/return")

            def create(app_id):
                try:
                    with Session(runtime) as db, db.begin():
                        set_tenant(db, tenants[0])
                        response, _ = create_verification(db, TenantContext(tenants[0], application_id=app_id), body,
                                                          configuration(tenants[0]), uuid4(), "concurrent-attempt")
                        return response["session_id"]
                except HTTPException as error:
                    return error.status_code

            with ThreadPoolExecutor(max_workers=2) as workers:
                responses = list(workers.map(create, apps[:2]))
            self.assertEqual(responses.count(429), 1, responses)
            session_id = next(value for value in responses if value != 429)
            with Session(runtime) as db, db.begin():
                for tenant in tenants:
                    set_tenant(db, tenant)
                    self.assertEqual(len(list(db.scalars(sa.select(ExternalSubject)))), 1)
                set_tenant(db, tenants[0])
                from kyc.db.models import KYCSession
                record = db.get(KYCSession, session_id)
                apply_event(db, record, TenantContext(tenants[0]), Event.EXPIRE, uuid4())
            with Session(runtime) as db, db.begin():
                set_tenant(db, tenants[0])
                self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(VerificationDecision)), 1)
                link = db.scalar(sa.select(VerificationBinding))
                with self.assertRaises(sa.exc.IntegrityError), db.begin_nested():
                    link.application_id = apps[2]
                    db.flush()
                set_tenant(db, tenants[1])
                self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(VerificationDecision)), 0)
                db.execute(sa.text("SELECT set_config('app.organization_id', '', true)"))
                for table in ("applications", "external_subjects", "verification_bindings", "verification_decisions", "integration_usage",
                              "registered_devices", "integration_face_sessions", "webhook_attempts"):
                    self.assertEqual(db.scalar(sa.text(f'SELECT count(*) FROM "{table}"')), 0, table)
        finally:
            if runtime:
                runtime.dispose()
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
            admin.dispose()
