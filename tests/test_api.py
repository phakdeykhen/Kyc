from datetime import datetime, timedelta, timezone
import json
import unittest
from uuid import UUID, uuid4

from pydantic import ValidationError
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.core.config import Settings
from kyc.db.models import AuditLog, Base, KYCSession, Organization
from kyc.db.session import build_engine
from kyc.domain.enums import SessionStatus
from kyc.main import create_app
from tests.helpers import call

TEST_KEY = "test-only-key-" + "a" * 40


def configuration(organization_id, **extra):
    # Explicit values beat environment variables, so tests never pick up a developer's real keys.
    hermetic = {"capture_encryption_keys": None, "pii_encryption_keys": None, "pii_hmac_key": None,
                "biometric_encryption_keys": None, "face_models_dir": "/tmp/kyc-test-missing-models",
                "face_match_calibrated": False, "face_match_calibration_reference": None,
                "face_match_policy_version": "SFACE-COSINE-UNCALIBRATED-2026.10.1",
                "face_match_pass_threshold": 0.363, "face_match_fail_threshold": 0.20,
                "document_processing_mode": "inline", "tesseract_cmd": "tesseract", "ocr_languages": "khm,eng"}
    return Settings(_env_file=None, environment="test", database_url="sqlite://",
                    development_api_key=TEST_KEY, development_organization_id=organization_id, **(hermetic | extra))


class SessionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.org = uuid4()
        self.other_org = uuid4()
        self.settings = configuration(self.org)
        self.engine = build_engine(self.settings)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add_all([Organization(id=self.org, name="Tenant A"), Organization(id=self.other_org, name="Tenant B")])
        self.app = create_app(self.settings, self.engine)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.headers = {"X-API-Key": TEST_KEY, "X-Organization-ID": str(self.org)}
        self.payload = {"user_id": "external-customer", "country": "KH", "expected_document_type": "KH_NATIONAL_ID", "verification_level": "DOCUMENT_FACE_LIVENESS"}

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.engine.dispose()

    async def create(self, payload=None):
        code, body, headers = await call(self.app, "/v1/kyc/sessions", "POST", payload or self.payload, self.headers)
        self.assertEqual(code, 201, body)
        return body

    async def test_create_fetch_and_pending_result(self):
        body = await self.create()
        session_id = UUID(body["session_id"])
        self.assertEqual(session_id.version, 4)
        self.assertEqual(body["organization_id"], str(self.org))
        self.assertEqual(body["status"], "CREATED")
        self.assertGreater(datetime.fromisoformat(body["expires_at"]), datetime.fromisoformat(body["created_at"]))
        code, stored, headers = await call(self.app, f"/v1/kyc/{session_id}", headers=self.headers)
        self.assertEqual(code, 200)
        self.assertEqual(stored["session_id"], str(session_id))
        self.assertEqual(headers["cache-control"], "no-store")
        UUID(headers["x-request-id"])
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(code, 200)
        self.assertIsNone(result["decision"])
        self.assertIsNone(result["identity"])
        self.assertEqual(result["checks"], {})
        self.assertNotIn("biometric_templates", result)

    async def test_credentials_and_organization_binding(self):
        for headers, expected in [({}, 401), ({"X-API-Key": "invalid", "X-Organization-ID": str(self.org)}, 401),
                                  ({"X-API-Key": TEST_KEY}, 400),
                                  ({"X-API-Key": TEST_KEY, "X-Organization-ID": str(self.other_org)}, 403)]:
            code, _, _ = await call(self.app, "/v1/kyc/sessions", "POST", self.payload, headers)
            self.assertEqual(code, expected)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(KYCSession)), 0)

    async def test_cross_tenant_session_is_not_visible(self):
        foreign_id = uuid4()
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            db.add(KYCSession(id=foreign_id, organization_id=self.other_org, user_id="foreign-user", country="KH",
                              expected_document_type="KH_PASSPORT", verification_level="DOCUMENT_ONLY",
                              status="CREATED", created_at=now, updated_at=now, expires_at=now + timedelta(minutes=10)))
        for suffix in ["", "/result"]:
            code, body, _ = await call(self.app, f"/v1/kyc/{foreign_id}{suffix}", headers=self.headers)
            self.assertEqual(code, 404)
            self.assertNotIn("foreign-user", json.dumps(body))

    async def test_expiry_is_persisted_and_audited_once(self):
        body = await self.create()
        identity = UUID(body["session_id"])
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, identity)
            record.created_at = now - timedelta(hours=1)
            record.expires_at = now - timedelta(minutes=1)
        for _ in range(2):
            code, result, _ = await call(self.app, f"/v1/kyc/{identity}", headers=self.headers)
            self.assertEqual(code, 200)
            self.assertEqual(result["status"], "EXPIRED")
            self.assertEqual(result["version"], 2)
        with Session(self.engine) as db:
            entries = db.scalars(sa.select(AuditLog).where(AuditLog.action == "SESSION_EXPIRED")).all()
            self.assertEqual(len(entries), 1)

    async def test_verified_terminal_session_does_not_expire(self):
        identity = UUID((await self.create())["session_id"])
        now = datetime.now(timezone.utc)
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, identity)
            record.status = SessionStatus.VERIFIED
            record.created_at = now - timedelta(hours=1)
            record.expires_at = now - timedelta(minutes=1)
        code, result, _ = await call(self.app, f"/v1/kyc/{identity}", headers=self.headers)
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "VERIFIED")

    async def test_invalid_payload_and_client_status_assertions(self):
        for extra in [{"country": "ZZ"}, {"country": "TH"}, {"status": "VERIFIED"},
                      {"organization_id": str(self.other_org)}, {"user_id": ""}, {"verification_level": "FAKE"}]:
            code, body, _ = await call(self.app, "/v1/kyc/sessions", "POST", self.payload | extra, self.headers)
            self.assertEqual(code, 422, body)
            self.assertNotIn("external-customer", json.dumps(body))

    async def test_audit_metadata_omits_identity_fields(self):
        await self.create()
        with Session(self.engine) as db:
            entry = db.scalar(sa.select(AuditLog))
            self.assertEqual(entry.action, "SESSION_CREATED")
            self.assertEqual(entry.organization_id, self.org)
            self.assertNotIn("external-customer", json.dumps(entry.event_metadata))
            self.assertEqual(entry.event_metadata, {"version": 1})

    async def test_health_and_inventory_are_honest(self):
        code, body, _ = await call(self.app, "/health/live")
        self.assertEqual(code, 200)
        self.assertEqual(body["phase"], 10)
        self.assertEqual(body["implemented_phases"], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        code, body, _ = await call(self.app, "/health/ready")
        self.assertEqual(code, 503)  # create_all is not a migration deployment.
        code, body, _ = await call(self.app, "/v1/document-types", headers=self.headers)
        self.assertEqual(code, 200)
        statuses = {row["type"]: row["adapter_status"] for row in body["document_types"]}
        self.assertEqual(statuses.pop("KH_NATIONAL_ID"), "AVAILABLE")
        self.assertEqual(statuses.pop("KH_NSSF"), "AVAILABLE")
        self.assertEqual(statuses.pop("KH_PASSPORT"), "AVAILABLE")
        self.assertEqual(statuses.pop("PASSPORT"), "AVAILABLE")
        self.assertEqual(statuses.pop("NATIONAL_ID"), "AVAILABLE")
        self.assertEqual(statuses.pop("RESIDENCE_CARD"), "AVAILABLE")
        self.assertTrue(all(value == "PLANNED" for value in statuses.values()))
        code, body, _ = await call(self.app, "/v1/countries", headers=self.headers)
        self.assertEqual(code, 200)
        self.assertEqual(len(body["countries"]), 249)
        self.assertEqual(body["verification_adapters_available"], ["KH"])
        self.assertEqual(body["any_country_document_types"], ["PASSPORT"])

    async def test_no_public_transition_endpoint(self):
        identity = (await self.create())["session_id"]
        code, _, _ = await call(self.app, f"/v1/kyc/{identity}", "POST", {"status": "VERIFIED"}, self.headers)
        self.assertEqual(code, 405)

    async def test_readiness_requires_the_expected_migration(self):
        with self.engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
            connection.execute(sa.text("INSERT INTO alembic_version VALUES ('wrong_revision')"))
        code, _, _ = await call(self.app, "/health/ready")
        self.assertEqual(code, 503)
        with self.engine.begin() as connection:
            connection.execute(sa.text("UPDATE alembic_version SET version_num = '0005_phase10'"))
        code, body, _ = await call(self.app, "/health/ready")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ready")


class ConfigurationTests(unittest.TestCase):
    def test_production_and_sqlite_development_are_rejected(self):
        for environment in ["production", "development"]:
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, environment=environment, database_url="sqlite://",
                         development_api_key=TEST_KEY, development_organization_id=uuid4())

    def test_short_credential_is_rejected(self):
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, environment="test", database_url="sqlite://",
                     development_api_key="short", development_organization_id=uuid4())

    def test_configuration_errors_omit_secret_values(self):
        password = "test-only-private-database-password"
        with self.assertRaises(ValidationError) as raised:
            Settings(_env_file=None, environment="production",
                     database_url=f"postgresql+psycopg2://test:{password}@localhost/kyc",
                     development_api_key=TEST_KEY, development_organization_id=uuid4())
        self.assertNotIn(password, str(raised.exception))
        self.assertNotIn(TEST_KEY, str(raised.exception))
