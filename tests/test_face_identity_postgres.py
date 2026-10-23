"""Live restricted-role regression in a disposable schema/database ending _test."""
from datetime import timedelta
import os
from pathlib import Path
import unittest
from uuid import uuid4

from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import (Organization, KYCSession, FaceSubject, FaceActor, FaceConsent, FaceEnrollment, FaceTemplate)
from kyc.db.privileges import grant_statements, face_grant_statements, FACE_TABLE_PRIVILEGES
from kyc.db.session import set_tenant
from kyc.face_identity.service import now
from scripts.security_check import catalog_checks

TEST_URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URL is not set; isolated PostgreSQL is required.")
class FacePostgreSQLTests(unittest.TestCase):
    def test_rls_restricted_biometric_role_cross_tenant_fks_and_scoped_erasure(self):
        if not sa.engine.make_url(TEST_URL).database.endswith("_test"):
            self.fail("Use a disposable database ending _test")
        engine = sa.create_engine(TEST_URL)
        schema = "face_test_" + uuid4().hex[:12]
        api_role, bio_role = "api_" + uuid4().hex[:12], "bio_" + uuid4().hex[:12]
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.attributes.update(connection=connection, database_url=TEST_URL)
                command.upgrade(config, "head")
                for role in (api_role, bio_role):
                    connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
                for statement in grant_statements(api_role, schema) + face_grant_statements(bio_role, schema):
                    connection.execute(sa.text(statement))
                for role, options in [(api_role, {}), (bio_role, {
                    "table_privileges": {table: frozenset(value.split()) for table, value in FACE_TABLE_PRIVILEGES.items()},
                    "column_updates": {}, "definer_functions": ()})]:
                    checks = catalog_checks(connection, role=role, schema=schema, **options)
                    self.assertEqual([row for row in checks if row["result"] == "FAIL"], [])
                organizations, sessions, enrollments = [], [], []
                # Seed only synthetic metadata and opaque ciphertext; no faces/documents.
                for _ in range(2):
                    tenant, session, subject, actor, consent, enrollment = [uuid4() for _ in range(6)]
                    organizations.append(tenant); sessions.append(session); enrollments.append(enrollment)
                    with Session(connection, join_transaction_mode="create_savepoint") as db, db.begin():
                        set_tenant(db, tenant)
                        db.add(Organization(id=tenant, name="Synthetic role test")); db.flush()
                        db.add(KYCSession(id=session, organization_id=tenant, user_id="test", country="KH",
                            expected_document_type="KH_PASSPORT", verification_level="DOCUMENT_FACE_LIVENESS", status="VERIFIED",
                            expires_at=now() + timedelta(hours=1))); db.flush()
                        db.add(FaceSubject(id=subject, organization_id=tenant, kyc_session_id=session)); db.flush()
                        db.add(FaceActor(id=actor, organization_id=tenant, subject_id=subject, kind="PERSON", token_hash="a"*64,
                            purposes=["self_profile"], profile_fields=[], expires_at=now() + timedelta(hours=1))); db.flush()
                        db.add(FaceConsent(id=consent, organization_id=tenant, subject_id=subject, actor_id=actor,
                            notice_version="TEST", expires_at=now() + timedelta(days=1))); db.flush()
                        db.add(FaceEnrollment(id=enrollment, organization_id=tenant, subject_id=subject, consent_id=consent,
                            status="ACTIVE", model_version="TEST", policy_version="TEST", expires_at=now() + timedelta(days=1))); db.flush()
                        db.add(FaceTemplate(organization_id=tenant, enrollment_id=enrollment, ciphertext=b"opaque-test-ciphertext",
                            key_version="TEST", quality_metadata={})); db.flush()
                connection.exec_driver_sql(f'SET LOCAL ROLE "{bio_role}"')
                connection.execute(sa.text("SELECT set_config('app.organization_id', '', true)"))
                self.assertEqual(connection.scalar(sa.text("SELECT count(*) FROM face_templates")), 0)
                connection.execute(sa.text("SELECT set_config('app.organization_id', :tenant, true)"), {"tenant": str(organizations[0])})
                self.assertEqual(connection.scalar(sa.text("SELECT enrollment_id FROM face_templates")), enrollments[0])
                with self.assertRaises(sa.exc.IntegrityError), connection.begin_nested():
                    connection.execute(FaceTemplate.__table__.insert().values(organization_id=organizations[0],
                        enrollment_id=enrollments[1], ciphertext=b"opaque", key_version="TEST", quality_metadata={}))
                connection.exec_driver_sql(f'SET LOCAL ROLE "{api_role}"')
                with self.assertRaises(sa.exc.ProgrammingError), connection.begin_nested():
                    connection.execute(sa.text("SELECT ciphertext FROM face_templates"))
                with self.assertRaises(sa.exc.ProgrammingError), connection.begin_nested():
                    connection.execute(sa.text("SELECT sample_ciphertext FROM face_capture_sessions"))
                self.assertEqual(connection.scalar(sa.text("SELECT erase_face_identity(:session)"), {"session": sessions[0]}), 0)
                connection.exec_driver_sql('RESET ROLE')
                connection.execute(sa.text("UPDATE kyc_sessions SET erased_at = now() WHERE id = :session"), {"session": sessions[0]})
                connection.exec_driver_sql(f'SET LOCAL ROLE "{api_role}"')
                self.assertEqual(connection.scalar(sa.text("SELECT erase_face_identity(:session)"), {"session": sessions[1]}), 0)
                self.assertEqual(connection.scalar(sa.text("SELECT erase_face_identity(:session)"), {"session": sessions[0]}), 1)
                connection.exec_driver_sql('RESET ROLE')
                self.assertEqual(connection.scalar(sa.text("SELECT count(*) FROM face_templates")), 1)
                self.assertEqual(connection.scalar(sa.text("SELECT count(*) FROM face_deletions")), 1)
                self.assertEqual(connection.scalar(sa.text("SELECT count(*) FROM face_subjects WHERE active = true")), 1)
        finally:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                for role in (api_role, bio_role):
                    connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
            engine.dispose()
