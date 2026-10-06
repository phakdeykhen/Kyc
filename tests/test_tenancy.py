"""Phase 15: per-organization API keys, scopes, rotation, suspension, rate limits and idempotency."""

from datetime import timedelta
import json
import unittest
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import ApiKey, AuditLog, Base, IdempotencyKey, KYCSession, Organization
from kyc.db.session import build_engine
from kyc.main import create_app
from kyc.tenancy import keys
from kyc.tenancy.ratelimit import RateLimiter
from tests.helpers import call
from tests.test_api import TEST_KEY, configuration

PAYLOAD = {"user_id": "customer-42", "country": "KH", "expected_document_type": "KH_NATIONAL_ID",
           "verification_level": "DOCUMENT_ONLY"}


class TenancyCase(unittest.IsolatedAsyncioTestCase):
    settings_overrides = {}

    async def asyncSetUp(self):
        self.org, self.other_org = uuid4(), uuid4()
        self.settings = configuration(self.org, **self.settings_overrides)
        self.engine = build_engine(self.settings)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db, db.begin():
            db.add_all([Organization(id=self.org, name="Bank A"), Organization(id=self.other_org, name="Fintech B")])
        self.app = create_app(self.settings, self.engine)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)
        self.engine.dispose()

    def issue(self, organization=None, scopes=keys.DEFAULT_SCOPES, expires_at=None, name="Backend"):
        organization = organization or self.org
        with Session(self.engine) as db, db.begin():
            result = keys.issue_key(db, organization, name, scopes, "administrator", uuid4(), expires_at)
            key_id = result.record.id
        return {"X-API-Key": result.key, "X-Organization-ID": str(organization)}, key_id

    async def create(self, headers, payload=PAYLOAD, extra=None):
        return await call(self.app, "/v1/kyc/sessions", "POST", payload, headers | (extra or {}))


class ApiKeyAuthenticationTests(TenancyCase):
    async def test_issued_key_creates_and_reads_sessions_in_its_own_organization_only(self):
        headers, key_id = self.issue()
        code, body, response_headers = await self.create(headers)
        self.assertEqual(code, 201, body)
        self.assertEqual(body["organization_id"], str(self.org))
        self.assertEqual(response_headers["x-ratelimit-limit"], "120")
        code, _, _ = await call(self.app, f"/v1/kyc/{body['session_id']}", headers=headers)
        self.assertEqual(code, 200)
        with Session(self.engine) as db:
            actors = set(db.scalars(sa.select(AuditLog.actor_id).where(AuditLog.session_id == UUID(body["session_id"]))))
            self.assertEqual(actors, {f"api_key:{key_id}"})
        # Same key presented for another organization: the key is not found there.
        code, _, _ = await self.create({**headers, "X-Organization-ID": str(self.other_org)})
        self.assertEqual(code, 401)
        # A key of organization B cannot read organization A's session.
        foreign, _ = self.issue(self.other_org)
        code, _, _ = await call(self.app, f"/v1/kyc/{body['session_id']}", headers=foreign)
        self.assertEqual(code, 404)

    async def test_wrong_revoked_expired_and_malformed_keys_are_refused(self):
        headers, key_id = self.issue()
        tampered = headers["X-API-Key"][:-1] + ("A" if headers["X-API-Key"][-1] != "A" else "B")
        for key in [tampered, "kyc_0000000000000000_" + "a" * 43, "kyc_short", "x" * 300, ""]:
            code, _, _ = await self.create({**headers, "X-API-Key": key})
            self.assertEqual(code, 401, key)
        with Session(self.engine) as db, db.begin():
            db.get(ApiKey, key_id).expires_at = keys.now() - timedelta(seconds=1)
        self.assertEqual((await self.create(headers))[0], 401)
        revoked, revoked_id = self.issue()
        with Session(self.engine) as db, db.begin():
            keys.revoke_key(db, db.get(ApiKey, revoked_id), "administrator", uuid4())
        self.assertEqual((await self.create(revoked))[0], 401)

    async def test_suspended_organization_is_refused_with_a_valid_key(self):
        headers, _ = self.issue()
        with Session(self.engine) as db, db.begin():
            db.get(Organization, self.org).active = False
        code, body, _ = await self.create(headers)
        self.assertEqual((code, body["detail"]), (403, "This organization is suspended."))

    async def test_only_a_hash_of_the_secret_is_stored(self):
        headers, key_id = self.issue()
        key = headers["X-API-Key"]
        with Session(self.engine) as db:
            record = db.get(ApiKey, key_id)
            self.assertEqual(record.secret_sha256, keys.secret_hash(key))
            self.assertTrue(key.startswith(record.key_prefix + "_"))
            dumped = json.dumps([[str(value) for value in row] for table in Base.metadata.sorted_tables
                                 for row in db.execute(sa.select(table)).all()])
        self.assertNotIn(key, dumped)
        self.assertNotIn(key.split("_", 2)[2], dumped)

    async def test_last_used_is_recorded(self):
        headers, key_id = self.issue()
        await call(self.app, "/v1/me", headers=headers)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(ApiKey, key_id).last_used_at)


class ScopeTests(TenancyCase):
    async def test_each_endpoint_requires_its_scope(self):
        backend, _ = self.issue()
        code, session, _ = await self.create(backend)
        session_id = session["session_id"]
        capture_only, _ = self.issue(scopes=["captures:write", "sessions:read"], name="Mobile capture")
        self.assertEqual((await self.create(capture_only))[0], 403)
        self.assertEqual((await call(self.app, f"/v1/kyc/{session_id}", headers=capture_only))[0], 200)
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=capture_only)
        self.assertEqual((code, body["detail"]), (403, "This API key does not have the results:read scope."))
        self.assertEqual((await call(self.app, f"/v1/kyc/{session_id}/verify", "POST", {}, capture_only))[0], 403)
        read_only, _ = self.issue(scopes=["sessions:read", "results:read"])
        self.assertEqual((await call(self.app, f"/v1/kyc/{session_id}/result", headers=read_only))[0], 200)
        for path in ["documents/front", "selfie", "liveness/challenge", "nfc/challenge"]:
            code, _, _ = await call(self.app, f"/v1/kyc/{session_id}/{path}", "POST", {}, read_only)
            self.assertEqual(code, 403, path)
        # Reference data needs any valid key.
        self.assertEqual((await call(self.app, "/v1/countries", headers=read_only))[0], 200)
        self.assertEqual((await call(self.app, "/v1/api-keys", headers=backend))[0], 403)

    async def test_me_describes_the_caller(self):
        headers, key_id = self.issue(scopes=["sessions:read"])
        code, body, _ = await call(self.app, "/v1/me", headers=headers)
        self.assertEqual(code, 200)
        self.assertEqual(body, {"organization_id": str(self.org), "organization_name": "Bank A",
                                "api_key_id": str(key_id), "actor_id": f"api_key:{key_id}",
                                "scopes": ["sessions:read"], "rate_limit_per_minute": 120})

    def test_unknown_scopes_are_rejected(self):
        with self.assertRaises(ValueError):
            keys.normalize_scopes(["sessions:create", "admin:everything"])
        with self.assertRaises(ValueError):
            keys.normalize_scopes([])


class KeyManagementTests(TenancyCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.admin, self.admin_id = self.issue(scopes=[*keys.DEFAULT_SCOPES, "keys:manage"], name="Admin")

    async def test_issue_list_and_revoke(self):
        code, issued, _ = await call(self.app, "/v1/api-keys", "POST",
                                     {"name": "POS terminal", "scopes": ["captures:write", "sessions:read"],
                                      "expires_in_days": 30}, self.admin)
        self.assertEqual(code, 201, issued)
        self.assertTrue(issued["key"].startswith(issued["key_prefix"] + "_"))
        new = {"X-API-Key": issued["key"], "X-Organization-ID": str(self.org)}
        self.assertEqual((await call(self.app, "/v1/me", headers=new))[0], 200)
        code, listed, _ = await call(self.app, "/v1/api-keys", headers=self.admin)
        self.assertEqual({row["name"] for row in listed}, {"Admin", "POS terminal"})
        self.assertTrue(all("key" not in row and "secret_sha256" not in row for row in listed))
        code, revoked, _ = await call(self.app, f"/v1/api-keys/{issued['id']}", "DELETE", headers=self.admin)
        self.assertEqual((code, revoked["active"]), (200, False))
        self.assertEqual((await call(self.app, "/v1/me", headers=new))[0], 401)
        code, listed, _ = await call(self.app, "/v1/api-keys", headers=self.admin)
        self.assertEqual([row["name"] for row in listed], ["Admin"])
        with Session(self.engine) as db:
            actions = list(db.scalars(sa.select(AuditLog.action).where(AuditLog.actor_id == f"api_key:{self.admin_id}")))
        self.assertEqual(sorted(actions), ["API_KEY_CREATED", "API_KEY_REVOKED"])

    async def test_a_key_cannot_grant_scopes_it_lacks(self):
        limited, _ = self.issue(scopes=["keys:manage", "sessions:read"])
        code, _, _ = await call(self.app, "/v1/api-keys", "POST", {"name": "Escalation", "scopes": ["results:read"]}, limited)
        self.assertEqual(code, 403)
        code, _, _ = await call(self.app, f"/v1/api-keys/{self.admin_id}/rotate", "POST", {}, limited)
        self.assertEqual(code, 403)
        code, _, _ = await call(self.app, "/v1/api-keys", "POST", {"name": "Bad", "scopes": ["root"]}, self.admin)
        self.assertEqual(code, 422)

    async def test_keys_of_another_organization_are_invisible(self):
        _, foreign_id = self.issue(self.other_org)
        self.assertEqual((await call(self.app, f"/v1/api-keys/{foreign_id}", "DELETE", headers=self.admin))[0], 404)
        self.assertEqual((await call(self.app, f"/v1/api-keys/{foreign_id}/rotate", "POST", {}, self.admin))[0], 404)
        with Session(self.engine) as db:
            self.assertIsNone(db.get(ApiKey, foreign_id).revoked_at)

    async def test_rotation_keeps_the_old_key_for_the_grace_period_only(self):
        old, old_id = self.issue(name="Backend")
        code, replacement, _ = await call(self.app, f"/v1/api-keys/{old_id}/rotate", "POST", {"grace_hours": 2}, self.admin)
        self.assertEqual(code, 201, replacement)
        self.assertEqual((replacement["name"], replacement["scopes"]), ("Backend", sorted(keys.DEFAULT_SCOPES)))
        self.assertEqual((await call(self.app, "/v1/me", headers=old))[0], 200)
        with Session(self.engine) as db:
            remaining = keys.aware(db.get(ApiKey, old_id).expires_at) - keys.now()
        self.assertTrue(timedelta(hours=1, minutes=59) < remaining <= timedelta(hours=2))
        new = {"X-API-Key": replacement["key"], "X-Organization-ID": str(self.org)}
        self.assertEqual((await call(self.app, "/v1/me", headers=new))[0], 200)
        # Immediate rotation revokes the old key at once.
        code, _, _ = await call(self.app, f"/v1/api-keys/{replacement['id']}/rotate", "POST", {"grace_hours": 0}, self.admin)
        self.assertEqual(code, 201)
        self.assertEqual((await call(self.app, "/v1/me", headers=new))[0], 401)
        code, _, _ = await call(self.app, f"/v1/api-keys/{replacement['id']}/rotate", "POST", {}, self.admin)
        self.assertEqual(code, 409)


class RateLimitTests(TenancyCase):
    async def test_per_key_limit_from_the_organization(self):
        with Session(self.engine) as db, db.begin():
            db.get(Organization, self.org).api_rate_limit_per_minute = 3
        headers, _ = self.issue()
        other, _ = self.issue()
        remaining = []
        for _ in range(3):
            code, _, response_headers = await call(self.app, "/v1/me", headers=headers)
            self.assertEqual(code, 200)
            remaining.append(response_headers["x-ratelimit-remaining"])
        self.assertEqual(remaining, ["2", "1", "0"])
        code, body, response_headers = await call(self.app, "/v1/me", headers=headers)
        self.assertEqual(code, 429)
        self.assertGreaterEqual(int(response_headers["retry-after"]), 1)
        self.assertEqual((await call(self.app, "/v1/me", headers=other))[0], 200)   # each key has its own budget

    def test_window_resets(self):
        clock = [0.0]
        limiter = RateLimiter(clock=lambda: clock[0])
        self.assertTrue(limiter.hit("k", 1).allowed)
        denied = limiter.hit("k", 1)
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.retry_after, 60)
        clock[0] = 60.0
        self.assertTrue(limiter.hit("k", 1).allowed)


class IdempotencyTests(TenancyCase):
    async def test_retry_with_the_same_key_returns_the_same_session(self):
        headers, _ = self.issue()
        retry = {"Idempotency-Key": "order-7781"}
        code, first, first_headers = await self.create(headers, extra=retry)
        self.assertEqual(code, 201)
        self.assertNotIn("idempotent-replayed", first_headers)
        code, second, second_headers = await self.create(headers, extra=retry)
        self.assertEqual((code, second["session_id"]), (201, first["session_id"]))
        self.assertEqual(second_headers["idempotent-replayed"], "true")
        code, _, _ = await self.create(headers, {**PAYLOAD, "user_id": "someone-else"}, retry)
        self.assertEqual(code, 422)
        # Other organizations have their own key space.
        foreign, _ = self.issue(self.other_org)
        code, other, _ = await self.create(foreign, extra=retry)
        self.assertEqual(code, 201)
        self.assertNotEqual(other["session_id"], first["session_id"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(KYCSession)), 2)

    async def test_expired_idempotency_record_allows_a_new_session(self):
        headers, _ = self.issue()
        retry = {"Idempotency-Key": "batch-1"}
        _, first, _ = await self.create(headers, extra=retry)
        with Session(self.engine) as db, db.begin():
            db.scalar(sa.select(IdempotencyKey)).created_at = keys.now() - timedelta(hours=25)
        code, second, headers_out = await self.create(headers, extra=retry)
        self.assertEqual(code, 201)
        self.assertNotEqual(second["session_id"], first["session_id"])
        self.assertNotIn("idempotent-replayed", headers_out)

    async def test_invalid_idempotency_key_is_rejected(self):
        headers, _ = self.issue()
        for value in ["has space", "x" * 129]:
            self.assertEqual((await self.create(headers, extra={"Idempotency-Key": value}))[0], 422)


class DevelopmentKeyTests(TenancyCase):
    def test_empty_development_key_means_disabled(self):
        self.assertIsNone(configuration(uuid4(), development_api_key="").development_api_key)

    settings_overrides = {"development_api_key": None}

    async def test_development_key_can_be_disabled(self):
        code, _, _ = await self.create({"X-API-Key": TEST_KEY, "X-Organization-ID": str(self.org)})
        self.assertEqual(code, 401)
        headers, _ = self.issue()
        self.assertEqual((await self.create(headers))[0], 201)
