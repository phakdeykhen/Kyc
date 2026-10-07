"""Phase 15: provisioned API keys, scopes, masking, idempotency, session client tokens and rate limits."""

from datetime import datetime, timedelta, timezone
import unittest
from uuid import UUID, uuid4

from pydantic import ValidationError
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.core.config import Settings
from kyc.db.models import ApiKey, AuditLog, KYCSession, Organization
from kyc.services import tenancy
from kyc.tenancy.ratelimit import RateLimiter
from tests.helpers import call
from tests import test_api
from tests.test_api import TEST_KEY
from tests.test_review import ReviewCase


class TenantCase(unittest.IsolatedAsyncioTestCase):
    """Two provisioned organizations; the development key stays bound to the first."""

    asyncSetUp = test_api.SessionAPITests.asyncSetUp
    asyncTearDown = test_api.SessionAPITests.asyncTearDown
    create = test_api.SessionAPITests.create

    def make_key(self, scopes=("sessions:write", "sessions:read"), organization=None, **options):
        organization = organization or self.org
        with Session(self.engine) as db, db.begin():
            row, token = tenancy.create_key(db, organization, "test key", list(scopes), "test", uuid4(), **options)
            key_id = row.id
        return {"X-API-Key": token, "X-Organization-ID": str(organization)}, key_id

    async def request(self, path, headers, method="GET", body=None, **extra):
        return await call(self.app, path, method, body, headers, **extra)


class ApiKeyAuthenticationTests(TenantCase):
    async def test_provisioned_key_works_only_in_its_own_organization(self):
        headers, key_id = self.make_key()
        code, body, _ = await self.request("/v1/kyc/sessions", headers, "POST", self.payload)
        self.assertEqual(code, 201, body)
        self.assertEqual(body["organization_id"], str(self.org))
        # The same key presented for another organization is not found under that tenant's RLS context.
        code, _, _ = await self.request("/v1/kyc/sessions", {**headers, "X-Organization-ID": str(self.other_org)},
                                        "POST", self.payload)
        self.assertEqual(code, 401)
        foreign, _ = self.make_key(organization=self.other_org)
        code, _, _ = await self.request(f"/v1/kyc/{body['session_id']}", foreign)
        self.assertEqual(code, 404)
        with Session(self.engine) as db:
            row = db.get(ApiKey, key_id)
            self.assertEqual(len(row.key_sha256), 64)
            self.assertNotIn(headers["X-API-Key"], (row.key_sha256, row.key_prefix))
            self.assertTrue(headers["X-API-Key"].startswith(row.key_prefix))
            self.assertIsNotNone(row.last_used_at)
            session = db.get(KYCSession, UUID(body["session_id"]))
            self.assertEqual(session.created_by, f"api_key:{key_id}")
            actors = set(db.scalars(sa.select(AuditLog.actor_id).where(AuditLog.session_id == session.id)))
            self.assertEqual(actors, {f"api_key:{key_id}"})

    async def test_revoked_expired_and_unknown_keys_are_refused(self):
        revoked, revoked_id = self.make_key()
        expired, expired_id = self.make_key()
        with Session(self.engine) as db, db.begin():
            db.get(ApiKey, revoked_id).revoked_at = datetime.now(timezone.utc)
            db.get(ApiKey, expired_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        for headers in (revoked, expired, {**revoked, "X-API-Key": "kyc_" + "x" * 43},
                        {**revoked, "X-API-Key": "kyc_" + "x" * 300}):
            code, body, _ = await self.request("/v1/document-types", headers)
            self.assertEqual(code, 401, body)

    async def test_suspended_organization_is_refused(self):
        headers, _ = self.make_key()
        with Session(self.engine) as db, db.begin():
            db.get(Organization, self.org).active = False
        code, body, _ = await self.request("/v1/document-types", headers)
        self.assertEqual((code, body["detail"]), (403, "This organization is suspended."))

    async def test_scopes_are_enforced_per_route(self):
        reader, _ = self.make_key(("sessions:read",))
        writer, _ = self.make_key(("sessions:write",))
        session_id = (await self.create())["session_id"]
        self.assertEqual((await self.request("/v1/kyc/sessions", reader, "POST", self.payload))[0], 403)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}/verify", reader, "POST", {}))[0], 403)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}", reader))[0], 200)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}/result", reader))[0], 200)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}/result", writer))[0], 403)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}/client-token", writer, "POST", {}))[0], 201)
        code, body, _ = await self.request("/v1/api-keys", writer)
        self.assertEqual(code, 403)
        self.assertIn("keys:manage", body["detail"])

    async def test_reviewer_tokens_are_not_client_credentials(self):
        code, _, _ = await self.request("/v1/document-types", {"Authorization": "Bearer rvw_" + "x" * 43,
                                                               "X-Organization-ID": str(self.org)})
        self.assertEqual(code, 401)

    async def test_organization_profile_reports_the_credential(self):
        headers, key_id = self.make_key(("sessions:read",))
        code, body, _ = await self.request("/v1/organization", headers)
        self.assertEqual(code, 200)
        self.assertEqual(body["organization_id"], str(self.org))
        self.assertEqual(body["credential"], {"type": "api_key", "key_id": str(key_id), "scopes": ["sessions:read"],
                                              "rate_limit_per_minute": 600})
        self.assertIn("results:identity", body["available_scopes"])
        code, body, _ = await self.request("/v1/organization", self.headers)
        self.assertEqual(body["credential"]["type"], "development_key")


class SelfServiceKeyTests(TenantCase):
    async def test_create_list_use_and_revoke_a_key(self):
        code, created, _ = await self.request("/v1/api-keys", self.headers, "POST",
                                              {"name": "Backend", "scopes": ["sessions:write", "sessions:read"],
                                               "expires_in_days": 90})
        self.assertEqual(code, 201, created)
        self.assertTrue(created["api_key"].startswith("kyc_"))
        self.assertEqual(created["status"], "ACTIVE")
        new_headers = {"X-API-Key": created["api_key"], "X-Organization-ID": str(self.org)}
        self.assertEqual((await self.request("/v1/kyc/sessions", new_headers, "POST", self.payload))[0], 201)
        code, listed, _ = await self.request("/v1/api-keys", self.headers)
        self.assertEqual([item["id"] for item in listed], [created["id"]])
        self.assertNotIn("api_key", listed[0])
        self.assertNotIn("key_sha256", listed[0])
        self.assertIsNotNone(listed[0]["last_used_at"])
        code, revoked, _ = await self.request(f"/v1/api-keys/{created['id']}", self.headers, "DELETE")
        self.assertEqual((code, revoked["status"]), (200, "REVOKED"))
        self.assertEqual((await self.request("/v1/kyc/sessions", new_headers, "POST", self.payload))[0], 401)
        with Session(self.engine) as db:
            actions = list(db.scalars(sa.select(AuditLog.action).where(AuditLog.session_id.is_(None))
                                      .order_by(AuditLog.created_at)))
        self.assertEqual(actions, ["API_KEY_CREATED", "API_KEY_REVOKED"])

    async def test_keys_cannot_escalate_scopes_or_limits(self):
        manager, _ = self.make_key(("keys:manage", "sessions:read"), rate_limit_per_minute=100)
        code, body, _ = await self.request("/v1/api-keys", manager, "POST", {"name": "x", "scopes": ["sessions:write"]})
        self.assertEqual(code, 403, body)
        code, body, _ = await self.request("/v1/api-keys", manager, "POST",
                                           {"name": "x", "scopes": ["sessions:read"], "rate_limit_per_minute": 101})
        self.assertEqual(code, 422, body)
        code, body, _ = await self.request("/v1/api-keys", manager, "POST", {"name": "x", "scopes": ["session:capture"]})
        self.assertEqual(code, 403, body)  # the client-token scope can never be granted to a key
        code, body, _ = await self.request("/v1/api-keys", manager, "POST", {"name": "x", "scopes": ["sessions:read"]})
        self.assertEqual((code, body["rate_limit_per_minute"]), (201, 100))

    async def test_keys_of_another_organization_cannot_be_revoked(self):
        _, foreign_id = self.make_key(organization=self.other_org)
        code, _, _ = await self.request(f"/v1/api-keys/{foreign_id}", self.headers, "DELETE")
        self.assertEqual(code, 404)


class IdempotencyTests(TenantCase):
    async def test_retried_creation_returns_the_same_session(self):
        headers = {**self.headers, "Idempotency-Key": "order-12345-attempt"}
        code, first, _ = await self.request("/v1/kyc/sessions", headers, "POST", self.payload)
        self.assertEqual(code, 201)
        code, second, response_headers = await self.request("/v1/kyc/sessions", headers, "POST", self.payload)
        self.assertEqual((code, second["session_id"]), (200, first["session_id"]))
        self.assertEqual(response_headers["idempotent-replayed"], "true")
        code, body, _ = await self.request("/v1/kyc/sessions", headers, "POST", {**self.payload, "user_id": "someone-else"})
        self.assertEqual(code, 409, body)
        code, third, _ = await self.request("/v1/kyc/sessions", self.headers, "POST", self.payload)
        self.assertNotEqual(third["session_id"], first["session_id"])  # no key, no deduplication
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(KYCSession)
                                       .where(KYCSession.idempotency_key == "order-12345-attempt")), 1)
            self.assertIn("SESSION_CREATE_REPLAYED", set(db.scalars(sa.select(AuditLog.action))))

    async def test_keys_are_scoped_per_organization_and_validated(self):
        foreign, _ = self.make_key(organization=self.other_org)
        key = {"Idempotency-Key": "shared-key-0001"}
        code, mine, _ = await self.request("/v1/kyc/sessions", {**self.headers, **key}, "POST", self.payload)
        code, theirs, _ = await self.request("/v1/kyc/sessions", {**foreign, **key}, "POST", self.payload)
        self.assertEqual(code, 201)
        self.assertNotEqual(mine["session_id"], theirs["session_id"])
        for bad in ("short", "has spaces in it", "x" * 129):
            code, _, _ = await self.request("/v1/kyc/sessions", {**self.headers, "Idempotency-Key": bad}, "POST", self.payload)
            self.assertEqual(code, 422, bad)


class ClientTokenTests(TenantCase):
    async def issue(self, session_id, headers=None):
        code, body, _ = await self.request(f"/v1/kyc/{session_id}/client-token", headers or self.headers, "POST", {})
        self.assertEqual(code, 201, body)
        return body

    async def test_token_is_limited_to_its_session_and_to_capture_and_status(self):
        session_id = (await self.create())["session_id"]
        other_session = (await self.create())["session_id"]
        issued = await self.issue(session_id)
        self.assertTrue(issued["client_token"].startswith("kst_"))
        self.assertEqual(issued["scope"], "session:capture")
        device = {"Authorization": f"Bearer {issued['client_token']}", "X-Organization-ID": str(self.org)}
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}", device))[0], 200)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id.upper()}", device))[0], 200)
        code, _, _ = await self.request(f"/v1/kyc/{session_id}/liveness/challenge", device, "POST", {})
        self.assertEqual(code, 409)  # authorized; the session is simply not at the liveness step
        for path, method in ((f"/v1/kyc/{session_id}/result", "GET"), (f"/v1/kyc/{session_id}/verify", "POST"),
                             (f"/v1/kyc/{session_id}/client-token", "POST"), ("/v1/kyc/sessions", "POST"),
                             ("/v1/document-types", "GET"), ("/v1/api-keys", "GET")):
            code, _, _ = await self.request(path, device, method, {} if method == "POST" else None)
            self.assertEqual(code, 403, path)
        self.assertEqual((await self.request(f"/v1/kyc/{other_session}", device))[0], 401)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}", {**device, "X-Organization-ID": str(self.other_org)}))[0], 401)
        with Session(self.engine) as db:
            record = db.get(KYCSession, UUID(session_id))
            self.assertEqual(len(record.client_token_sha256), 64)
            self.assertIn("CLIENT_TOKEN_ISSUED", set(db.scalars(sa.select(AuditLog.action).where(AuditLog.session_id == record.id))))

    async def test_reissue_revokes_and_expiry_ends_the_token(self):
        session_id = (await self.create())["session_id"]
        first = await self.issue(session_id)
        second = await self.issue(session_id)
        old = {"Authorization": f"Bearer {first['client_token']}", "X-Organization-ID": str(self.org)}
        new = {"Authorization": f"Bearer {second['client_token']}", "X-Organization-ID": str(self.org)}
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}", old))[0], 401)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}", new))[0], 200)
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, UUID(session_id))
            record.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual((await self.request(f"/v1/kyc/{session_id}", new))[0], 401)
        await self.request(f"/v1/kyc/{session_id}", self.headers)  # records EXPIRED
        code, _, _ = await self.request(f"/v1/kyc/{session_id}/client-token", self.headers, "POST", {})
        self.assertEqual(code, 409)


class RateLimitTests(TenantCase):
    async def test_each_credential_has_its_own_window(self):
        limited, _ = self.make_key(rate_limit_per_minute=2)
        other, _ = self.make_key(rate_limit_per_minute=2)
        for remaining in ("1", "0"):
            code, _, headers = await self.request("/v1/document-types", limited)
            self.assertEqual(code, 200)
            self.assertEqual((headers["x-ratelimit-limit"], headers["x-ratelimit-remaining"]), ("2", remaining))
        code, body, headers = await self.request("/v1/document-types", limited)
        self.assertEqual(code, 429, body)
        self.assertGreaterEqual(int(headers["retry-after"]), 1)
        self.assertEqual(headers["x-ratelimit-remaining"], "0")
        self.assertEqual((await self.request("/v1/document-types", other))[0], 200)

    def test_window_resets(self):
        now = [0.0]
        limiter = RateLimiter(window_seconds=60, clock=lambda: now[0])
        self.assertIsNone(limiter.hit("k", 1))
        self.assertAlmostEqual(limiter.hit("k", 1), 60)
        self.assertEqual(limiter.remaining("k", 1), 0)
        now[0] = 60.0
        self.assertEqual(limiter.remaining("k", 1), 1)
        self.assertIsNone(limiter.hit("k", 1))


class MaskingTests(ReviewCase):
    async def test_identity_is_masked_without_the_identity_scope(self):
        session_id = await self.in_review()
        with Session(self.engine) as db, db.begin():
            _, reader_token = tenancy.create_key(db, self.org, "reader", ["sessions:read"], "test", uuid4())
            _, full_token = tenancy.create_key(db, self.org, "full", ["sessions:read", "results:identity"], "test", uuid4())
        for token, name, masked in ((reader_token, "S** S*****", True), (full_token, "SOK SOPHEA", False)):
            code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/result",
                                       headers={"X-API-Key": token, "X-Organization-ID": str(self.org)})
            self.assertEqual(code, 200, body)
            self.assertEqual((body["identity"]["full_name"], body["identity_masked"]), (name, masked))

    async def test_suspended_organization_blocks_reviewers(self):
        with Session(self.engine) as db, db.begin():
            db.get(Organization, self.org).active = False
        code, body, _ = await call(self.app, "/v1/review/me", headers=self.reviewer)
        self.assertEqual(code, 403, body)


class ConfigurationTests(unittest.TestCase):
    def test_development_key_is_optional_and_cannot_mimic_provisioned_credentials(self):
        settings = Settings(_env_file=None, environment="test", database_url="sqlite://")
        self.assertIsNone(settings.development_api_key)
        for key in ("kyc_" + "a" * 40, "kst_" + "a" * 40):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, environment="test", database_url="sqlite://",
                         development_api_key=key, development_organization_id=uuid4())
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, environment="test", database_url="sqlite://", development_api_key=TEST_KEY)
