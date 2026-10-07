"""Phase 17 security hardening: production gate, HTTP controls, security events, network allow-lists,
reviewer token expiry, the webhook keyring and the API role's privilege matrix."""

import asyncio
import base64
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks
from pydantic import ValidationError
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import Database
from kyc.core.config import Settings
from kyc.core.crypto import FieldCipher
from kyc.core.hardening import database_uses_api_role, database_uses_tls
from kyc.core.observability import JSONFormatter, RedactingFilter, SECURITY_LOGGER, TextFormatter, configure_logging, redact
from kyc.db.models import AuditLog, Base, Organization, Reviewer
from kyc.db.privileges import APPEND_ONLY, COLUMN_UPDATES, TABLE_PRIVILEGES, grant_statements
from kyc.main import host_allowed
from kyc.review.access import token_hash
from kyc.services import tenancy
from kyc.tenancy.network import address_allowed, networks_within, parse_networks
from kyc.webhooks.secrets import WebhookSecretCipher
from tests.helpers import call, multipart
from tests.test_api import TEST_KEY
from tests.test_capture_store import keyring
from tests.test_review import ReviewCase
from tests.test_tenancy import TenantCase


def key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def production(**overrides) -> Settings:
    values = dict(_env_file=None, environment="production",
                  database_url="postgresql+psycopg2://kyc_app:test-only-password@db.internal/kyc?sslmode=verify-full",
                  capture_encryption_keys=keyring("cap-v1"), pii_encryption_keys=keyring("pii-v1"), pii_hmac_key=key(),
                  biometric_encryption_keys=keyring("bio-v1"), webhook_secret_keys=keyring("wh-v1"),
                  allowed_hosts="kyc.example.com", require_document_consent=True, development_api_key=None,
                  development_organization_id=None)
    return Settings(**(values | overrides))


class ProductionGateTests(unittest.TestCase):
    def test_a_fully_configured_production_starts_with_safe_defaults(self):
        settings = production()
        self.assertEqual((settings.expose_api_docs, settings.enable_capture_client, settings.log_format), (False, False, "json"))
        production(database_url="postgresql+psycopg2://kyc_app:test-only-password@/kyc?host=/cloudsql/project:region:db")
        self.assertTrue(production(expose_api_docs=True).expose_api_docs, "Documentation exposure is an explicit operator choice")

    def test_every_missing_control_is_named_without_secret_values(self):
        password = "test-only-private-password"
        with self.assertRaises(ValidationError) as raised:
            Settings(_env_file=None, environment="production", database_url=f"postgresql+psycopg2://kyc:{password}@db/kyc",
                     development_api_key=TEST_KEY, development_organization_id=uuid4(), webhook_allow_private_targets=True,
                     enable_capture_client=True)
        message = str(raised.exception)
        for expected in ("DEVELOPMENT_API_KEY", "sslmode", "CAPTURE_ENCRYPTION_KEYS", "PII_ENCRYPTION_KEYS",
                         "BIOMETRIC_ENCRYPTION_KEYS", "WEBHOOK_SECRET_KEYS", "PII_HMAC_KEY", "WEBHOOK_ALLOW_PRIVATE_TARGETS",
                         "ALLOWED_HOSTS", "REQUIRE_DOCUMENT_CONSENT", "ENABLE_CAPTURE_CLIENT"):
            self.assertIn(expected, message)
        self.assertNotIn(password, message)
        self.assertNotIn(TEST_KEY, message)

    def test_single_controls_are_enforced(self):
        for overrides in ({"allowed_hosts": "*"}, {"allowed_hosts": "kyc.example.com,*"}, {"require_document_consent": False},
                          {"database_url": "postgresql+psycopg2://kyc_app:x@db.internal/kyc?sslmode=prefer"},
                          {"development_api_key": TEST_KEY, "development_organization_id": uuid4()},
                          {"webhook_secret_keys": None}):
            with self.subTest(overrides=list(overrides)), self.assertRaises(ValidationError):
                production(**overrides)

    def test_keyrings_never_share_key_material(self):
        shared = keyring("pii-v1")
        reused = "wh-v1:" + shared.split(":", 1)[1]
        for environment in ("test", "production"):
            with self.subTest(environment=environment), self.assertRaises(ValidationError) as raised:
                (production if environment == "production" else
                 lambda **values: Settings(_env_file=None, environment="test", database_url="sqlite://", **values))(
                    pii_encryption_keys=shared, pii_hmac_key=key(), webhook_secret_keys=reused)
            self.assertIn("pii_encryption_keys and webhook_secret_keys", str(raised.exception))

    def test_plaintext_tcp_cannot_hide_behind_a_unix_socket(self):
        base = "postgresql+psycopg2://kyc_app:test-only-password@/kyc?"
        with patch.dict(os.environ, {"PGHOSTADDR": "", "PGSERVICE": ""}):
            for mode in ("require", "verify-ca", "verify-full"):
                self.assertTrue(database_uses_tls(base + f"host=db.internal&sslmode={mode}"))
            for query in ("host=/cloudsql/local", "host=/socket/one,/socket/two",
                          "host=/socket/one&host=/socket/two"):
                self.assertTrue(database_uses_tls(base + query), query)
            for query in ("host=/cloudsql/local&hostaddr=203.0.113.7", "host=/socket,db.internal",
                          "host=db.internal,/socket", "host=db.internal&host=/socket",
                          "host=/socket&host=db.internal", "host=/socket&service=network",
                          "host=db.internal&sslmode=disable", "host=db.internal&sslmode=prefer"):
                self.assertFalse(database_uses_tls(base + query), query)
            for variable in ("PGHOSTADDR", "PGSERVICE"):
                with patch.dict(os.environ, {variable: "network"}):
                    self.assertFalse(database_uses_tls(base + "host=/socket"), variable)
                    self.assertTrue(database_uses_tls(base + "host=/socket&sslmode=verify-full"), variable)

    def test_production_refuses_administrative_users_and_query_overrides(self):
        for suffix in ("", "&user=kyc_app"):
            self.assertTrue(database_uses_api_role("postgresql+psycopg2://kyc_app:test-password@db/kyc?sslmode=require" + suffix))
        for url in ("postgresql+psycopg2://admin:PRIVATE-PASSWORD@db/kyc?sslmode=require",
                    "postgresql+psycopg2://kyc_app:PRIVATE-PASSWORD@db/kyc?sslmode=require&user=admin",
                    "postgresql+psycopg2://kyc_app:PRIVATE-PASSWORD@db/kyc?sslmode=require&user=admin&user=kyc_app"):
            with self.subTest(url_kind=url.split("?", 1)[1]), self.assertRaises(ValidationError) as raised:
                production(database_url=url)
            self.assertIn("kyc_app runtime role", str(raised.exception))
            self.assertNotIn("PRIVATE-PASSWORD", str(raised.exception))


class HostCheckTests(unittest.TestCase):
    def test_host_patterns(self):
        allowed = ["kyc.example.com", "*.api.example.com"]
        for host, expected in (("kyc.example.com", True), ("KYC.example.com:443", True), ("eu.api.example.com", True),
                               ("api.example.com", False), ("kyc.example.com.evil.test", False), (None, False),
                               ("[::1]:8080", False)):
            self.assertEqual(host_allowed(host, allowed), expected, host)
        self.assertTrue(host_allowed("anything", ["*"]))
        for host in ("kyc.example.com:0", "kyc.example.com:65536", "bad..host", "bad_host", "user@host", "[broken]", "a\r\nX:1"):
            self.assertFalse(host_allowed(host, ["*"]), host)


class HTTPHardeningTests(TenantCase):
    async def test_large_malformed_liveness_file_never_spools_plaintext_to_disk(self):
        self.app.state.settings.max_liveness_bytes = 32 * 1024 * 1024
        payload, content_type = multipart(
            {"challenge_id": str(uuid4()), "nonce": "test-nonce", "frame_steps": "0"},
            [("frames", "frame.jpg", "image/jpeg", b"x" * (26 * 1024 * 1024))])
        with patch.object(tempfile.SpooledTemporaryFile, "rollover", side_effect=AssertionError("Plaintext disk spool")) as rollover, \
                patch("starlette.formparsers.SpooledTemporaryFile", wraps=tempfile.SpooledTemporaryFile) as parser_file:
            code, _, _ = await self.request(f"/v1/kyc/{uuid4()}/liveness", self.headers, "POST",
                                            raw=payload, content_type=content_type)
        self.assertEqual(code, 404, "The real multipart parser must finish before the unknown session is rejected")
        parser_file.assert_called_once()
        rollover.assert_not_called()

    async def test_every_response_carries_security_headers(self):
        code, _, headers = await call(self.app, "/v1/document-types", headers=self.headers)
        self.assertEqual(code, 200)
        self.assertEqual(headers["content-security-policy"], "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        for name, value in (("x-frame-options", "DENY"), ("referrer-policy", "no-referrer"), ("x-content-type-options", "nosniff"),
                            ("cross-origin-resource-policy", "same-origin"), ("cache-control", "no-store")):
            self.assertEqual(headers[name], value)
        self.assertNotIn("strict-transport-security", headers, "HSTS only in production")
        self.app.state.settings.environment = "production"
        try:
            code, _, headers = await call(self.app, "/health/live")
            self.assertEqual(headers["strict-transport-security"], "max-age=63072000; includeSubDomains")
        finally:
            self.app.state.settings.environment = "test"

    async def test_unknown_hosts_are_refused_except_for_health_probes(self):
        self.app.state.settings.allowed_hosts = "kyc.example.com"
        code, body, _ = await call(self.app, "/v1/document-types", headers=self.headers)
        self.assertEqual((code, body["detail"]), (400, "Invalid host header."))
        code, _, _ = await call(self.app, "/v1/document-types", headers={**self.headers, "host": "kyc.example.com"})
        self.assertEqual(code, 200)
        code, _, _ = await call(self.app, "/health/live")
        self.assertEqual(code, 200)

    async def test_docs_and_the_development_capture_page_can_be_switched_off(self):
        code, document, _ = await call(self.app, "/openapi.json")
        self.assertEqual(code, 200)
        self.assertIn("/v1/kyc/{session_id}/erase", document["paths"])
        self.assertIn("/v1/kyc/{session_id}/consent", document["paths"])
        code, _, headers = await call(self.app, "/docs")
        self.assertEqual(code, 200)
        self.assertNotIn("content-security-policy", headers, "The docs page loads its own assets")
        self.app.state.settings.expose_api_docs = False
        self.app.state.settings.enable_capture_client = False
        for path in ("/openapi.json", "/docs", "/redoc", "/capture/", "/capture/capture.js"):
            code, _, _ = await call(self.app, path)
            self.assertEqual(code, 404, path)

    async def test_unexpected_errors_reveal_nothing(self):
        secret = "kyc_" + "S" * 43

        def explode():
            raise RuntimeError(f"failed with {secret}")
        self.app.add_api_route("/v1/explode", explode)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": "/v1/explode", "raw_path": b"/v1/explode", "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"test")], "client": ("127.0.0.1", 1), "server": ("test", 80)}
        with self.assertLogs("kyc.api", logging.ERROR) as logs, self.assertRaises(RuntimeError):
            await asyncio.wait_for(self.app(scope, receive, send), 10)  # Starlette re-raises after responding
        start = next(message for message in sent if message["type"] == "http.response.start")
        body = json.loads(b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"))
        self.assertEqual(start["status"], 500)
        self.assertEqual(body["detail"], "Internal server error.")
        self.assertNotIn(secret, json.dumps(body))
        formatted = JSONFormatter().format(logs.records[0])
        self.assertIn("RuntimeError", formatted)
        self.assertNotIn(secret, formatted)

    async def test_sql_errors_never_log_bound_identity_values(self):
        identity = "PRIVATE-NAME-AND-DOCUMENT-NUMBER"

        def explode():
            raise sa.exc.StatementError(identity, "INSERT INTO document_fields (value) VALUES (:value)",
                                        {"value": identity}, RuntimeError(identity))
        self.app.add_api_route("/v1/database-failure", explode)
        with self.assertLogs("kyc.api", logging.ERROR) as logs:
            code, body, headers = await call(self.app, "/v1/database-failure")
        self.assertEqual((code, body["detail"]), (503, "Database unavailable."))
        self.assertEqual(headers["cache-control"], "no-store")
        output = "\n".join(JSONFormatter().format(record) for record in logs.records)
        self.assertIn("StatementError", output)
        self.assertNotIn(identity, output)
        self.assertNotIn("INSERT INTO", output)

    async def test_development_credentials_are_also_refused_at_runtime_in_production(self):
        key_headers, _ = self.make_key()
        self.app.state.settings.environment = "production"
        try:
            self.assertEqual((await self.request("/v1/document-types", self.headers))[0], 401)
            self.assertEqual((await self.request("/v1/document-types", key_headers))[0], 200)
        finally:
            self.app.state.settings.environment = "test"

    async def test_transaction_commits_before_response_background_work(self):
        marker = uuid4().hex
        order = []

        def committed(db):
            if db.info.get("phase17_test_commit") == marker:
                order.append("commit")

        def write(db: Database, background_tasks: BackgroundTasks):
            db.info["phase17_test_commit"] = marker
            db.add(AuditLog(organization_id=self.org, actor_id="test", action="TEST_TRANSACTION_ORDER",
                            request_id=uuid4(), reason_codes=["TEST_TRANSACTION_ORDER"]))
            background_tasks.add_task(order.append, "background")
            return {"status": "accepted"}

        self.app.add_api_route("/v1/transaction-order", write, methods=["POST"])
        sa.event.listen(Session, "after_commit", committed)
        try:
            code, body, _ = await self.request("/v1/transaction-order", self.headers, "POST", {})
        finally:
            sa.event.remove(Session, "after_commit", committed)
        self.assertEqual((code, body["status"]), (200, "accepted"))
        self.assertEqual(order, ["commit", "background"])

    async def test_a_commit_failure_returns_an_error_and_runs_no_background_work(self):
        marker = uuid4().hex
        background = []

        def refuse_commit(db):
            if db.info.get("phase17_test_commit") == marker:
                raise sa.exc.StatementError("PRIVATE-SUBJECT", "INSERT PRIVATE-SQL", {"name": "PRIVATE-SUBJECT"}, RuntimeError())

        def write(db: Database, background_tasks: BackgroundTasks):
            db.info["phase17_test_commit"] = marker
            db.add(AuditLog(organization_id=self.org, actor_id="test", action="TEST_FAILED_COMMIT",
                            request_id=uuid4(), reason_codes=["TEST_FAILED_COMMIT"]))
            background_tasks.add_task(background.append, "ran")
            return {"status": "accepted"}

        self.app.add_api_route("/v1/commit-failure", write, methods=["POST"])
        sa.event.listen(Session, "before_commit", refuse_commit)
        try:
            with self.assertLogs("kyc.api", logging.ERROR) as logs:
                code, body, _ = await self.request("/v1/commit-failure", self.headers, "POST", {})
        finally:
            sa.event.remove(Session, "before_commit", refuse_commit)
        self.assertEqual((code, body["detail"]), (503, "Database unavailable."))
        self.assertEqual(background, [])
        self.assertNotIn("PRIVATE-SUBJECT", "\n".join(JSONFormatter().format(record) for record in logs.records))
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(AuditLog)
                                       .where(AuditLog.action == "TEST_FAILED_COMMIT")), 0)


class SecurityEventTests(TenantCase):
    def test_redaction(self):
        samples = {"kyc_" + "a" * 43: "kyc_[REDACTED]", "kst_" + "b" * 43: "kst_[REDACTED]", "rvw_" + "c" * 43: "rvw_[REDACTED]",
                   "whsec_" + "d" * 43: "whsec_[REDACTED]", "Bearer eyJhbGciOiJIUzI1NiJ9.x": "Bearer [REDACTED]",
                   "postgresql+psycopg2://kyc_app:hunter2-password@db/kyc": "postgresql+psycopg2://kyc_app:[REDACTED]@db/kyc",
                   key(): "[REDACTED_KEY]"}
        for raw, expected in samples.items():
            self.assertEqual(redact(f"value {raw} end"), f"value {expected} end")
        record = logging.LogRecord("kyc.test", logging.INFO, __file__, 1, "token %s", ("kst_" + "e" * 43,), None)
        RedactingFilter().filter(record)
        self.assertEqual(record.getMessage(), "token kst_[REDACTED]")

    async def test_refusals_become_redacted_security_events(self):
        headers, _ = self.make_key(scopes=("sessions:read",))
        unknown = {"X-API-Key": "kyc_" + "Z" * 43, "X-Organization-ID": str(self.org)}
        with self.assertLogs(SECURITY_LOGGER, logging.WARNING) as logs:
            self.assertEqual((await self.request("/v1/document-types", unknown))[0], 401)
            self.assertEqual((await self.request("/v1/kyc/sessions", headers, "POST", self.payload))[0], 403)
            self.assertEqual((await self.request("/v1/document-types", {**headers, "X-Organization-ID": "not-a-uuid"}))[0], 400)
        events = [record.security for record in logs.records]
        self.assertEqual([event["event"] for event in events], ["CREDENTIAL_INVALID", "SCOPE_MISSING", "ORGANIZATION_HEADER_INVALID"])
        self.assertEqual(events[0]["credential"], "api_key")
        self.assertIsNone(events[2]["organization_id"], "Invalid headers are never written verbatim")
        self.assertEqual(events[1]["status"], 403)
        output = "\n".join(JSONFormatter().format(record) for record in logs.records)
        self.assertNotIn(unknown["X-API-Key"], output)
        self.assertNotIn(headers["X-API-Key"], output)

    async def test_route_and_organization_labels_never_include_submitted_identity(self):
        identity = "PRIVATE-SUBJECT-NAME"
        unknown = {"X-API-Key": "kyc_" + "Z" * 43, "X-Organization-ID": self.org.hex.upper()}
        with self.assertLogs(SECURITY_LOGGER, logging.WARNING) as logs:
            self.assertEqual((await self.request(f"/v1/kyc/{identity}?token=PRIVATE-QUERY-TOKEN", unknown))[0], 401)
            self.app.state.settings.allowed_hosts = "kyc.example.com"
            self.assertEqual((await self.request(f"/unknown/{identity}", unknown | {"X-Organization-ID": identity}))[0], 400)
        events = [record.security for record in logs.records]
        self.assertEqual(events[0]["path"], "/v1/kyc/{session_id}")
        self.assertEqual(events[0]["organization_id"], str(self.org))
        self.assertEqual(events[1]["path"], "<unmatched>")
        self.assertIsNone(events[1]["organization_id"])
        output = "\n".join(JSONFormatter().format(record) for record in logs.records)
        for value in (identity, "PRIVATE-QUERY-TOKEN", unknown["X-API-Key"], "kyc_ZZZZZZZZ"):
            self.assertNotIn(value, output)

    async def test_rate_limits_are_security_events(self):
        headers, _ = self.make_key(rate_limit_per_minute=1)
        await self.request("/v1/document-types", headers)
        with self.assertLogs(SECURITY_LOGGER, logging.WARNING) as logs:
            code, _, response = await self.request("/v1/document-types", headers)
        self.assertEqual(code, 429)
        self.assertIn("retry-after", response)
        self.assertEqual(logs.records[0].security["event"], "RATE_LIMITED")


class PrivacyLoggingTests(unittest.TestCase):
    def test_exception_messages_cached_tracebacks_and_sql_parameters_are_omitted(self):
        identity = "PRIVATE-SUBJECT-IDENTITY"
        try:
            raise sa.exc.StatementError(identity, "SELECT :name", {"name": identity}, RuntimeError(identity))
        except sa.exc.StatementError:
            exception = sys.exc_info()
        for formatter in (JSONFormatter(), TextFormatter(), logging.Formatter("%(message)s")):
            with self.subTest(formatter=type(formatter).__name__):
                record = logging.LogRecord("kyc.api", logging.ERROR, __file__, 1, "database exception", (), exception)
                record.exc_text = identity
                record.stack_info = identity
                RedactingFilter().filter(record)
                output = formatter.format(record)
                self.assertNotIn(identity, output)
                self.assertNotIn("SELECT :name", output)
                self.assertIn("StatementError", output)
                self.assertIsNone(record.exc_info, "Downstream handlers cannot recover the original exception")
        for formatter in (JSONFormatter(), TextFormatter()):
            record = logging.LogRecord("kyc.api", logging.ERROR, __file__, 1, "database exception", (), exception)
            self.assertNotIn(identity, formatter.format(record), "Formatters are safe even without their filter")

    def test_uvicorn_errors_are_sanitized_and_url_access_logs_are_disabled(self):
        names = ("kyc", "uvicorn", "uvicorn.error", "uvicorn.access")
        saved = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).level,
                        logging.getLogger(name).propagate, logging.getLogger(name).disabled) for name in names}
        output = io.StringIO()
        try:
            with patch("kyc.core.observability._handler", None), patch("kyc.core.observability.sys.stderr", output):
                configure_logging("json")
                logging.getLogger("uvicorn.access").info("GET /PRIVATE-SUBJECT?token=PRIVATE-TOKEN")
                try:
                    raise RuntimeError("PRIVATE-SUBJECT AND PRIVATE-TOKEN")
                except RuntimeError:
                    logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
            self.assertIn("RuntimeError", output.getvalue())
            self.assertNotIn("PRIVATE-SUBJECT", output.getvalue())
            self.assertNotIn("PRIVATE-TOKEN", output.getvalue())
        finally:
            for name, (handlers, level, propagate, disabled) in saved.items():
                logger = logging.getLogger(name)
                logger.handlers, logger.level, logger.propagate, logger.disabled = handlers, level, propagate, disabled


class NetworkAllowListTests(TenantCase):
    def test_parsing_and_matching(self):
        self.assertEqual(parse_networks(["10.1.2.3", "10.0.0.0/8", "2001:db8::/32", "10.0.0.0/8"]),
                         ["10.0.0.0/8", "10.1.2.3/32", "2001:db8::/32"])
        for bad in (["0.0.0.0/0"], ["::/0"], ["not-an-ip"], [f"10.0.{i}.0/24" for i in range(21)]):
            with self.assertRaises(ValueError):
                parse_networks(bad)
        self.assertTrue(address_allowed("10.9.9.9", ["10.0.0.0/8"]))
        self.assertTrue(address_allowed("::ffff:10.9.9.9", ["10.0.0.0/8"]), "IPv4-mapped IPv6 clients match IPv4 rules")
        self.assertEqual(parse_networks(["::ffff:10.0.0.0/104", "::ffff:10.9.9.9"]), ["10.0.0.0/8", "10.9.9.9/32"])
        self.assertTrue(address_allowed("10.9.9.9", ["::ffff:10.0.0.0/104"]))
        self.assertFalse(address_allowed("::ffff:192.0.2.1", ["10.0.0.0/8"]))
        self.assertFalse(address_allowed("10.9.9.9", ["corrupt-policy"]))
        self.assertFalse(address_allowed("192.0.2.1", ["10.0.0.0/8"]))
        self.assertFalse(address_allowed(None, ["10.0.0.0/8"]))
        self.assertTrue(address_allowed(None, []))
        self.assertTrue(networks_within(["10.1.0.0/16"], ["10.0.0.0/8"]))
        self.assertFalse(networks_within(["10.0.0.0/8"], ["10.1.0.0/16"]))
        self.assertFalse(networks_within(["2001:db8::/48"], ["10.0.0.0/8"]))
        self.assertTrue(networks_within(["10.1.0.0/16"], ["::ffff:10.0.0.0/104"]))
        self.assertFalse(networks_within(["::ffff:10.0.0.0/104"], ["10.1.0.0/16"]))

    async def test_a_key_works_only_from_its_networks(self):
        elsewhere, _ = self.make_key(allowed_cidrs=["203.0.113.0/24"])
        here, _ = self.make_key(allowed_cidrs=["127.0.0.0/8"])   # the test transport's client address is 127.0.0.1
        with self.assertLogs(SECURITY_LOGGER, logging.WARNING) as logs:
            code, body, _ = await self.request("/v1/document-types", elsewhere)
        self.assertEqual((code, body["detail"]), (403, "This API key is not allowed from this network."))
        self.assertEqual(logs.records[0].security["event"], "API_KEY_NETWORK_DENIED")
        self.assertEqual((await self.request("/v1/document-types", here))[0], 200)
        with self.assertRaises(Exception) as raised, Session(self.engine) as db, db.begin():
            tenancy.create_key(db, self.org, "bad", ["sessions:read"], "test", uuid4(), allowed_cidrs=["0.0.0.0/0"])
        self.assertEqual(getattr(raised.exception, "status_code", None), 422)

    async def test_created_keys_cannot_widen_their_creators_networks(self):
        admin, _ = self.make_key(scopes=("keys:manage", "sessions:read"), allowed_cidrs=["127.0.0.0/8"])
        new = {"name": "child", "scopes": ["sessions:read"]}
        code, body, _ = await self.request("/v1/api-keys", admin, "POST", new)
        self.assertEqual((code, body["allowed_cidrs"]), (201, ["127.0.0.0/8"]), "Inherits the creator's networks")
        code, body, _ = await self.request("/v1/api-keys", admin, "POST", new | {"allowed_cidrs": ["127.0.0.0/24"]})
        self.assertEqual((code, body["allowed_cidrs"]), (201, ["127.0.0.0/24"]))
        for widened in (["10.0.0.0/8"], [], ["127.0.0.0/7"]):
            code, _, _ = await self.request("/v1/api-keys", admin, "POST", new | {"allowed_cidrs": widened})
            self.assertEqual(code, 403, widened)
        code, _, _ = await self.request("/v1/api-keys", admin, "POST", new | {"allowed_cidrs": ["nonsense"]})
        self.assertEqual(code, 422)

    async def test_child_keys_inherit_exact_expiry_and_cannot_extend_it(self):
        expiry = datetime.now(timezone.utc) + timedelta(days=2, minutes=7, microseconds=12345)
        admin, _ = self.make_key(scopes=("keys:manage", "sessions:read"), expires_at=expiry)
        new = {"name": "child", "scopes": ["keys:manage", "sessions:read"]}
        code, child, _ = await self.request("/v1/api-keys", admin, "POST", new)
        self.assertEqual(code, 201, child)
        self.assertEqual(datetime.fromisoformat(child["expires_at"].replace("Z", "+00:00")), expiry,
                         "Omitting a lifetime copies the exact timestamp; no day rounding")
        child_headers = {"X-API-Key": child["api_key"], "X-Organization-ID": str(self.org)}
        code, grandchild, _ = await self.request("/v1/api-keys", child_headers, "POST", new)
        self.assertEqual((code, grandchild["expires_at"]), (201, child["expires_at"]))
        code, shorter, _ = await self.request("/v1/api-keys", admin, "POST", new | {"expires_in_days": 1})
        self.assertEqual(code, 201, shorter)
        self.assertLess(datetime.fromisoformat(shorter["expires_at"].replace("Z", "+00:00")), expiry)
        with self.assertLogs(SECURITY_LOGGER, logging.WARNING) as logs:
            self.assertEqual((await self.request("/v1/api-keys", admin, "POST", new | {"expires_in_days": 3}))[0], 403)
            self.assertEqual((await self.request("/v1/api-keys", admin, "POST", new | {"scopes": ["data:erase"]}))[0], 403)
            self.assertEqual((await self.request("/v1/api-keys", admin, "POST", new | {"rate_limit_per_minute": 601}))[0], 422)
        self.assertEqual([record.security["event"] for record in logs.records],
                         ["API_KEY_EXPIRY_ESCALATION", "API_KEY_SCOPE_ESCALATION", "API_KEY_RATE_LIMIT_ESCALATION"])


class ReviewerHardeningTests(ReviewCase):
    async def test_legacy_reviewer_tokens_require_an_expiry_in_production(self):
        self.app.state.settings.environment = "production"
        try:
            self.assertEqual((await call(self.app, "/v1/review/me", headers=self.reviewer))[0], 401)
            with Session(self.engine) as db, db.begin():
                db.get(Reviewer, self.reviewer_id).expires_at = datetime.now(timezone.utc) + timedelta(days=1)
            self.assertEqual((await call(self.app, "/v1/review/me", headers=self.reviewer))[0], 200)
        finally:
            self.app.state.settings.environment = "test"

    async def test_expired_reviewer_tokens_are_refused(self):
        with Session(self.engine) as db, db.begin():
            db.get(Reviewer, self.reviewer_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.assertLogs(SECURITY_LOGGER, logging.WARNING) as logs:
            code, body, _ = await call(self.app, "/v1/review/me", headers=self.reviewer)
        self.assertEqual((code, body["detail"]), (401, "This reviewer token has expired; ask for a new one."))
        self.assertEqual(logs.records[0].security["event"], "REVIEWER_TOKEN_EXPIRED")
        self.assertEqual(logs.records[0].security["credential"], "reviewer_token")
        with Session(self.engine) as db, db.begin():
            db.get(Reviewer, self.reviewer_id).expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertEqual((await call(self.app, "/v1/review/me", headers=self.reviewer))[0], 200)

    async def test_refused_reviewer_actions_are_audited(self):
        session_id = await self.in_review()
        code, _, _ = await self.decide(session_id, headers=self.auditor)
        self.assertEqual(code, 403)
        with Session(self.engine) as db:
            denied = db.scalars(sa.select(AuditLog).where(AuditLog.action == "REVIEW_ACCESS_DENIED")).all()
        self.assertEqual(len(denied), 1, "Recorded although the request's own transaction rolled back")
        self.assertEqual(denied[0].session_id, session_id)
        self.assertEqual(denied[0].event_metadata["permission"], "DECIDE")
        self.assertTrue(denied[0].actor_id.startswith("reviewer:"))


class ReviewerAdministrationTests(unittest.TestCase):
    def test_rotation_replaces_the_hash_and_expiry_and_is_audited(self):
        organization = uuid4()
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite:///" + str(Path(directory) / "reviewers.sqlite")
            settings = Settings(_env_file=None, environment="test", database_url=url, migration_database_url=url,
                                reviewer_token_max_days=7)
            engine = sa.create_engine(url)
            try:
                Base.metadata.create_all(engine)
                with Session(engine) as db, db.begin():
                    db.add(Organization(id=organization, name="test organization"))
                script = Path(__file__).resolve().parents[1] / "scripts" / "create_reviewer.py"

                def run(*arguments):
                    output = io.StringIO()
                    with patch("kyc.core.config.get_settings", return_value=settings), \
                            patch.object(sys, "argv", [str(script), *arguments, "--organization", str(organization)]), \
                            patch.object(sys, "path", sys.path[:]), redirect_stdout(output):
                        runpy.run_path(str(script))
                    return output.getvalue()

                created = run("create", "Test reviewer")
                old_token = created.split("Token (shown once): ", 1)[1].strip()
                with Session(engine) as db:
                    reviewer = db.scalar(sa.select(Reviewer))
                    reviewer_id, old_expiry = reviewer.id, reviewer.expires_at
                    self.assertEqual(reviewer.token_sha256, token_hash(old_token))
                rotated = run("rotate", str(reviewer_id), "--expires-in-days", "1")
                new_token = rotated.split("Token (shown once): ", 1)[1].strip()
                self.assertNotEqual(new_token, old_token)
                with Session(engine) as db:
                    reviewer = db.get(Reviewer, reviewer_id)
                    self.assertEqual(reviewer.token_sha256, token_hash(new_token))
                    self.assertNotEqual(reviewer.token_sha256, token_hash(old_token))
                    self.assertLess(reviewer.expires_at, old_expiry)
                    self.assertGreater(reviewer.expires_at, datetime.now(timezone.utc).replace(tzinfo=None))
                    self.assertEqual(set(db.scalars(sa.select(AuditLog.action))), {"REVIEWER_CREATED", "REVIEWER_TOKEN_ROTATED"})
                for days in ("0", "8"):
                    with self.assertRaises(SystemExit):
                        run("rotate", str(reviewer_id), "--expires-in-days", days)
                with Session(engine) as db:
                    self.assertEqual(db.get(Reviewer, reviewer_id).token_sha256, token_hash(new_token),
                                     "Invalid rotation lifetimes leave the current credential intact")
            finally:
                engine.dispose()


class WebhookKeyringTests(unittest.TestCase):
    def test_dedicated_keyring_with_legacy_fallback(self):
        legacy = FieldCipher(keyring("pii-v1"), os.urandom(32))
        old_sealed, old_version = WebhookSecretCipher(None, legacy).seal("whsec_old", "webhook-secret/a/b")
        self.assertEqual(old_version, "pii-v1")
        cipher = WebhookSecretCipher(keyring("wh-v1"), legacy)
        sealed, version = cipher.seal("whsec_new", "webhook-secret/a/b")
        self.assertEqual((version, cipher.active_version), ("wh:wh-v1", "wh:wh-v1"))
        self.assertEqual(cipher.open(sealed, version, "webhook-secret/a/b"), "whsec_new")
        self.assertEqual(cipher.open(old_sealed, old_version, "webhook-secret/a/b"), "whsec_old")
        with self.assertRaises(Exception):
            cipher.open(sealed, version, "webhook-secret/a/OTHER")
        with self.assertRaises(RuntimeError):
            WebhookSecretCipher(None, legacy).open(sealed, version, "webhook-secret/a/b")


class PrivilegeMatrixTests(unittest.TestCase):
    def test_every_table_is_declared_and_append_only_tables_stay_append_only(self):
        self.assertEqual(set(Base.metadata.tables), set(TABLE_PRIVILEGES) - {"alembic_version"})
        for table in APPEND_ONLY:
            self.assertFalse({"UPDATE", "DELETE", "TRUNCATE"} & TABLE_PRIVILEGES[table], table)
            self.assertNotIn(table, COLUMN_UPDATES)
        self.assertEqual(TABLE_PRIVILEGES["organizations"], frozenset({"SELECT"}))
        self.assertEqual(TABLE_PRIVILEGES["reviewers"], frozenset({"SELECT"}))
        statements = grant_statements()
        self.assertTrue(statements[0].startswith("REVOKE ALL ON ALL TABLES"), "Grants start from nothing")
        self.assertFalse(any("TRUNCATE" in item or "REFERENCES" in item or "TRIGGER" in item for item in statements))
