from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import unittest
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import Base, DocumentField, IdentityDocument, KYCSession, Organization
from kyc.db.session import build_engine
from tests.test_api import configuration

ROOT = Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.org = uuid4()
        self.other_org = uuid4()
        self.engine = build_engine(configuration(self.org))
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def seed_session(self, organization_id):
        now = datetime.now(timezone.utc)
        session_id = uuid4()
        record = KYCSession(id=session_id, organization_id=organization_id, user_id="schema-test", country="KH",
                            expected_document_type="KH_NATIONAL_ID", verification_level="DOCUMENT_ONLY",
                            status="CREATED", created_at=now, updated_at=now, expires_at=now + timedelta(minutes=10))
        with Session(self.engine) as db, db.begin():
            if not db.get(Organization, organization_id):
                db.add(Organization(id=organization_id, name="Schema test organization"))
                db.flush()
            db.add(record)
        return session_id

    def test_all_requested_tables_exist(self):
        self.assertEqual(set(Base.metadata.tables), {
            "organizations", "kyc_sessions", "identity_documents", "document_images", "document_fields",
            "document_checks", "mrz_results", "barcode_results", "nfc_results", "biometric_templates",
            "face_comparisons", "liveness_checks", "fraud_signals", "risk_assessments", "manual_reviews",
            "consents", "audit_logs", "liveness_challenges", "selfie_captures", "face_quality_checks",
        })

    def test_cross_tenant_artifact_link_fails_at_database_layer(self):
        session_id = self.seed_session(self.org)
        self.seed_session(self.other_org)
        with self.assertRaises(sa.exc.IntegrityError), Session(self.engine) as db, db.begin():
            db.add(IdentityDocument(organization_id=self.other_org, session_id=session_id,
                                   document_type="KH_NATIONAL_ID", delete_after=datetime.now(timezone.utc) + timedelta(days=1)))
            db.flush()

    def test_cross_session_document_link_fails(self):
        session_a = self.seed_session(self.org)
        session_b = self.seed_session(self.org)
        doc_id = uuid4()
        with Session(self.engine) as db, db.begin():
            db.add(IdentityDocument(id=doc_id, organization_id=self.org, session_id=session_b,
                                   document_type="KH_NATIONAL_ID", delete_after=datetime.now(timezone.utc) + timedelta(days=1)))
        with self.assertRaises(sa.exc.IntegrityError), Session(self.engine) as db, db.begin():
            db.add(DocumentField(organization_id=self.org, session_id=session_a, document_id=doc_id,
                                 field_name="full_name", confidence=0.9))
            db.flush()

    def test_ciphertext_requires_a_key_version(self):
        session_id = self.seed_session(self.org)
        doc_id = uuid4()
        with Session(self.engine) as db, db.begin():
            db.add(IdentityDocument(id=doc_id, organization_id=self.org, session_id=session_id,
                                   document_type="KH_NATIONAL_ID", delete_after=datetime.now(timezone.utc) + timedelta(days=1)))
        with self.assertRaises(sa.exc.IntegrityError), Session(self.engine) as db, db.begin():
            db.add(DocumentField(organization_id=self.org, session_id=session_id, document_id=doc_id,
                                 field_name="full_name", raw_value_ciphertext=b"test-only-ciphertext", confidence=0.9))
            db.flush()

    def test_frozen_migration_upgrade_downgrade_and_model_alignment(self):
        Base.metadata.drop_all(self.engine)
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["database_url"] = "sqlite://"
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            self.assertEqual(set(sa.inspect(connection).get_table_names()), set(Base.metadata.tables) | {"alembic_version"})
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
            command.downgrade(config, "base")
            self.assertEqual(set(sa.inspect(connection).get_table_names()), {"alembic_version"})
            command.upgrade(config, "head")

    def test_postgres_sql_contains_tenant_policies_and_native_types(self):
        output = StringIO()
        config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
        config.attributes["database_url"] = "postgresql+psycopg2://unused@localhost/kyc_test"
        command.upgrade(config, "head", sql=True)
        sql = output.getvalue()
        self.assertEqual(sql.count("CREATE POLICY tenant_isolation"), 20)
        self.assertEqual(sql.count("FORCE ROW LEVEL SECURITY"), 20)
        self.assertIn("TIMESTAMP WITH TIME ZONE", sql)
        self.assertIn("JSONB", sql)
        self.assertIn("UUID", sql)
        self.assertIn("WITH CHECK", sql)
        self.assertIn("NULLIF(current_setting('app.organization_id', true), '')::uuid", sql)
