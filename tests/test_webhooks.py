"""Phase 16: webhook endpoints, transactional outbox, signing, SSRF-safe delivery and retries."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import AuditLog, KYCSession, WebhookDelivery, WebhookEndpoint
from kyc.domain.enums import SessionStatus
from kyc.domain.state_machine import Event
from kyc.api.dependencies import TenantContext
from kyc.services import tenancy
from kyc.services.retention import purge_organization
from kyc.services.sessions import apply_event
from kyc.webhooks import delivery, signing
from kyc.webhooks.delivery import HTTPSender, SendResult, TargetNotAllowed
from kyc.webhooks.events import events_for
from kyc.webhooks.outbox import PENDING_KEY
from tests.helpers import call
from tests.test_review import ReviewCase

PUBLIC_URL = "https://1.1.1.1/kyc/webhooks"   # a public IP literal: no DNS lookup in tests


class RecordingSender:
    def __init__(self, *results):
        self.results = list(results)
        self.sent = []

    def send(self, url, body, headers):
        self.sent.append((url, body, headers))
        return self.results.pop(0) if self.results else SendResult(200, None)


class WebhookCase(ReviewCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.sender = RecordingSender()
        self.dispatcher = self.app.state.webhook_dispatcher
        self.dispatcher.cipher, self.dispatcher.sender = self.cipher, self.sender

    async def endpoint(self, url=PUBLIC_URL, event_types=None, headers=None):
        code, body, _ = await call(self.app, "/v1/webhooks", "POST", {"url": url, "event_types": event_types or []},
                                   headers or self.headers)
        self.assertEqual(code, 201, body)
        return body

    def deliveries(self, **filters):
        with Session(self.engine) as db:
            query = sa.select(WebhookDelivery).order_by(WebhookDelivery.created_at)
            for name, value in filters.items():
                query = query.where(getattr(WebhookDelivery, name) == value)
            return db.scalars(query).all()


class EndpointManagementTests(WebhookCase):
    async def test_create_list_update_and_delete(self):
        created = await self.endpoint(event_types=["kyc.verified", "kyc.rejected"])
        self.assertTrue(created["secret"].startswith("whsec_"))
        self.assertEqual(created["event_types"], ["kyc.rejected", "kyc.verified"])
        with Session(self.engine) as db:
            row = db.get(WebhookEndpoint, UUID(created["id"]))
            self.assertNotIn(created["secret"].encode(), row.secret_ciphertext)
        code, listed, _ = await call(self.app, "/v1/webhooks", headers=self.headers)
        self.assertEqual([item["id"] for item in listed], [created["id"]])
        self.assertNotIn("secret", listed[0])
        code, updated, _ = await call(self.app, f"/v1/webhooks/{created['id']}", "PATCH",
                                      {"event_types": [], "description": "all events"}, self.headers)
        self.assertEqual((code, updated["event_types"], updated["description"]), (200, [], "all events"))
        code, deleted, _ = await call(self.app, f"/v1/webhooks/{created['id']}", "DELETE", headers=self.headers)
        self.assertEqual((code, deleted["active"]), (200, False))
        self.assertEqual((await call(self.app, "/v1/webhooks", headers=self.headers))[1], [])
        with Session(self.engine) as db:
            actions = set(db.scalars(sa.select(AuditLog.action)))
        self.assertTrue({"WEBHOOK_ENDPOINT_CREATED", "WEBHOOK_ENDPOINT_UPDATED", "WEBHOOK_ENDPOINT_DELETED"} <= actions)

    async def test_targets_must_be_public_https(self):
        for url in ("http://1.1.1.1/hook", "https://10.0.0.5/hook", "https://127.0.0.1/hook",
                    "https://169.254.169.254/latest/meta-data", "https://[::1]/hook", "https://[::ffff:10.0.0.1]/hook",
                    "https://user:password@1.1.1.1/hook", "ftp://1.1.1.1/hook", "https:///nohost"):
            code, body, _ = await call(self.app, "/v1/webhooks", "POST", {"url": url}, self.headers)
            self.assertEqual(code, 422, (url, body))
        code, body, _ = await call(self.app, "/v1/webhooks", "POST", {"url": PUBLIC_URL, "event_types": ["kyc.nope"]},
                                   self.headers)
        self.assertEqual(code, 422, body)

    async def test_scope_tenant_and_count_limits(self):
        with Session(self.engine) as db, db.begin():
            _, token = tenancy.create_key(db, self.org, "no webhooks", ["sessions:read"], "test", uuid4())
        code, _, _ = await call(self.app, "/v1/webhooks", headers={"X-API-Key": token, "X-Organization-ID": str(self.org)})
        self.assertEqual(code, 403)
        created = await self.endpoint()
        with Session(self.engine) as db, db.begin():
            _, foreign = tenancy.create_key(db, self.other_org, "b", ["webhooks:manage"], "test", uuid4())
        foreign_headers = {"X-API-Key": foreign, "X-Organization-ID": str(self.other_org)}
        self.assertEqual((await call(self.app, f"/v1/webhooks/{created['id']}", headers=foreign_headers))[0], 404)
        for _ in range(9):
            await self.endpoint()
        code, _, _ = await call(self.app, "/v1/webhooks", "POST", {"url": PUBLIC_URL}, self.headers)
        self.assertEqual(code, 409)

    async def test_secrets_need_the_pii_keyring(self):
        self.app.state.field_cipher = None
        code, _, _ = await call(self.app, "/v1/webhooks", "POST", {"url": PUBLIC_URL}, self.headers)
        self.assertEqual(code, 503)


class OutboxTests(WebhookCase):
    async def test_state_changes_become_signed_deliveries_without_identity_data(self):
        everything = await self.endpoint()
        verified_only = await self.endpoint(event_types=["kyc.verified"])
        session_id = await self.in_review()
        rows = self.deliveries(endpoint_id=UUID(everything["id"]))
        self.assertEqual([row.event_type for row in rows], ["kyc.review.required"])
        self.assertEqual(self.deliveries(endpoint_id=UUID(verified_only["id"])), [])
        payload = rows[0].payload
        self.assertEqual(payload["data"]["session_id"], str(session_id))
        self.assertEqual(payload["data"]["status"], "MANUAL_REVIEW")
        with Session(self.engine) as db:
            self.assertEqual(payload["data"]["session_version"], db.get(KYCSession, session_id).version)
        self.assertEqual(payload["data"]["decision"]["result"], "REVIEW")
        self.assertIn("DOCUMENT_AUTHENTICITY_UNVERIFIED", payload["data"]["decision"]["reason_codes"])
        self.assertNotIn("SOK", json.dumps(payload))  # identity never leaves through webhooks

        code, body, _ = await self.decide(session_id)
        self.assertEqual(body["status"], "VERIFIED", body)
        verified = self.deliveries(event_type="kyc.verified")
        self.assertEqual({str(row.endpoint_id) for row in verified}, {everything["id"], verified_only["id"]})
        self.assertEqual(len({row.event_id for row in verified}), 1)  # one event, fanned out
        self.assertEqual(verified[0].payload["data"]["review"],
                         {"action": "APPROVE", "reason_code": "DOCUMENT_CONFIRMED_GENUINE"})

        self.assertEqual(self.dispatcher.deliver_due(self.org), 3)
        self.assertEqual({row.status for row in self.deliveries()}, {"DELIVERED"})
        secrets = {everything["id"]: everything["secret"], verified_only["id"]: verified_only["secret"]}
        for url, body, headers in self.sender.sent:
            self.assertEqual(url, PUBLIC_URL)
            event = json.loads(body)
            self.assertEqual(headers["KYC-Event-ID"], event["id"])
            self.assertEqual(headers["KYC-Event-Type"], event["type"])
            endpoint_id = next(str(row.endpoint_id) for row in self.deliveries() if str(row.id) == headers["KYC-Delivery-ID"])
            signing.verify(secrets[endpoint_id], body, headers["KYC-Signature"])
            with self.assertRaises(signing.SignatureError):
                signing.verify(secrets[endpoint_id], body.replace(b"VERIFIED", b"REJECTED"), headers["KYC-Signature"])

    async def test_automatic_pass_and_expiry_events(self):
        await self.endpoint()
        session_id = self.processing(level="DOCUMENT_ONLY", signed_barcode=True)
        code, body, _ = await call(self.app, f"/v1/kyc/{session_id}/verify", "POST", {}, self.headers)
        self.assertEqual(body["status"], "VERIFIED", body)
        self.assertEqual([row.event_type for row in self.deliveries()], ["kyc.verified"])
        self.assertEqual(self.deliveries()[0].payload["data"]["decision"]["result"], "PASS")
        payload = {"user_id": "u-expiring", "country": "KH", "expected_document_type": "KH_NATIONAL_ID"}
        expiring = (await call(self.app, "/v1/kyc/sessions", "POST", payload, self.headers))[1]["session_id"]
        with Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, UUID(expiring))
            record.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await call(self.app, f"/v1/kyc/{expiring}", headers=self.headers)
        self.assertEqual(self.deliveries(event_type="kyc.expired")[0].payload["data"]["previous_status"], "CREATED")

    async def test_rolled_back_transitions_produce_nothing(self):
        await self.endpoint()
        session_id = self.ready(status="PROCESSING")
        tenant = TenantContext(self.org, actor_id="test")
        with self.assertRaises(RuntimeError), Session(self.engine) as db, db.begin():
            record = db.get(KYCSession, session_id)
            apply_event(db, record, tenant, Event.ASSESSMENT_FAIL, uuid4())
            self.assertTrue(db.info[PENDING_KEY])
            raise RuntimeError("abort")
        self.assertEqual(self.deliveries(), [])
        with Session(self.engine) as db:
            self.assertNotIn(PENDING_KEY, db.info)
            self.assertEqual(db.get(KYCSession, session_id).status, SessionStatus.PROCESSING)

    def test_event_mapping(self):
        S = SessionStatus
        self.assertEqual(events_for(Event.DOCUMENT_ACCEPTED, S.DOCUMENT_PROCESSING, S.SELFIE_REQUIRED),
                         ["kyc.document.accepted", "kyc.selfie.required"])
        self.assertEqual(events_for(Event.DOCUMENT_ACCEPTED, S.DOCUMENT_PROCESSING, S.PROCESSING),
                         ["kyc.document.accepted", "kyc.processing"])
        self.assertEqual(events_for(Event.RECAPTURE_REQUIRED, S.MANUAL_REVIEW, S.DOCUMENT_REQUIRED), ["kyc.recapture.required"])
        self.assertEqual(events_for(Event.START, S.CREATED, S.DOCUMENT_REQUIRED), [])
        self.assertEqual(events_for(Event.ASSESSMENT_FAIL, S.PROCESSING, S.REJECTED), ["kyc.rejected"])


class DeliveryTests(WebhookCase):
    async def queued(self):
        created = await self.endpoint()
        code, delivery_row, _ = await call(self.app, f"/v1/webhooks/{created['id']}/test", "POST", {}, self.headers)
        self.assertEqual((code, delivery_row["event_type"]), (202, "webhook.test"))
        return created, delivery_row

    async def test_failures_back_off_then_abandon_and_can_be_redelivered(self):
        created, queued = await self.queued()
        self.dispatcher.max_attempts = 2
        self.sender.results = [SendResult(500, "HTTP_500"), SendResult(None, "TIMEOUT"), SendResult(204, None)]
        start = datetime.now(timezone.utc)
        self.assertEqual(self.dispatcher.deliver_due(self.org, start), 1)
        row = self.deliveries()[0]
        self.assertEqual((row.status, row.attempts, row.last_status_code, row.last_error), ("PENDING", 1, 500, "HTTP_500"))
        due = row.next_attempt_at.replace(tzinfo=timezone.utc)
        self.assertAlmostEqual((due - start).total_seconds(), 30, delta=5)
        self.assertEqual(self.dispatcher.deliver_due(self.org, start + timedelta(seconds=5)), 0)  # not due yet
        self.assertEqual(self.dispatcher.deliver_due(self.org, start + timedelta(seconds=40)), 1)
        row = self.deliveries()[0]
        self.assertEqual((row.status, row.attempts, row.last_error), ("ABANDONED", 2, "TIMEOUT"))
        with Session(self.engine) as db:
            self.assertEqual(db.get(WebhookEndpoint, UUID(created["id"])).consecutive_failures, 2)
        code, listed, _ = await call(self.app, f"/v1/webhooks/{created['id']}/deliveries?status=ABANDONED", headers=self.headers)
        self.assertEqual([item["id"] for item in listed], [queued["id"]])
        code, again, _ = await call(self.app, f"/v1/webhooks/{created['id']}/deliveries/{queued['id']}/redeliver", "POST",
                                    {}, self.headers)
        self.assertEqual((code, again["status"], again["attempts"]), (202, "PENDING", 0))
        self.dispatcher.deliver_due(self.org)
        row = self.deliveries()[0]
        self.assertEqual((row.status, row.last_status_code), ("DELIVERED", 204))
        self.assertEqual(len({headers["KYC-Event-ID"] for _, _, headers in self.sender.sent}), 1)  # same event ID each time
        with Session(self.engine) as db:
            self.assertEqual(db.get(WebhookEndpoint, UUID(created["id"])).consecutive_failures, 0)

    async def test_a_claimed_delivery_is_leased(self):
        await self.queued()
        claims = self.dispatcher._claim(self.org, datetime.now(timezone.utc))  # as if the worker crashed after claiming
        self.assertEqual(len(claims), 1)
        self.assertEqual(self.dispatcher.deliver_due(self.org), 0)
        self.assertEqual(self.dispatcher.deliver_due(self.org, datetime.now(timezone.utc) + timedelta(minutes=6)), 1)
        self.assertEqual(self.deliveries()[0].attempts, 2)

    async def test_secret_rotation_overlaps_then_ends(self):
        created, _ = await self.queued()
        code, rotated, _ = await call(self.app, f"/v1/webhooks/{created['id']}/rotate-secret", "POST", {}, self.headers)
        self.assertEqual(code, 200)
        self.assertNotEqual(rotated["secret"], created["secret"])
        self.assertIsNotNone(rotated["previous_secret_expires_at"])
        self.dispatcher.deliver_due(self.org)
        _, body, headers = self.sender.sent[-1]
        self.assertEqual(headers["KYC-Signature"].count("v1="), 2)
        for secret in (created["secret"], rotated["secret"]):
            signing.verify(secret, body, headers["KYC-Signature"])
        with Session(self.engine) as db, db.begin():
            db.get(WebhookEndpoint, UUID(created["id"])).previous_secret_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await call(self.app, f"/v1/webhooks/{created['id']}/test", "POST", {}, self.headers)
        self.dispatcher.deliver_due(self.org)
        _, body, headers = self.sender.sent[-1]
        self.assertEqual(headers["KYC-Signature"].count("v1="), 1)
        signing.verify(rotated["secret"], body, headers["KYC-Signature"])
        with self.assertRaises(signing.SignatureError):
            signing.verify(created["secret"], body, headers["KYC-Signature"])

    async def test_deleted_endpoints_abandon_pending_deliveries(self):
        created, _ = await self.queued()
        await call(self.app, f"/v1/webhooks/{created['id']}", "DELETE", headers=self.headers)
        self.assertEqual([(row.status, row.last_error) for row in self.deliveries()], [("ABANDONED", "ENDPOINT_DISABLED")])
        self.assertEqual(self.dispatcher.deliver_due(self.org), 0)
        session_id = self.processing(level="DOCUMENT_ONLY", signed_barcode=True)
        await call(self.app, f"/v1/kyc/{session_id}/verify", "POST", {}, self.headers)
        self.assertEqual(len(self.deliveries()), 1)

    async def test_retention_removes_only_old_finished_deliveries(self):
        await self.queued()
        await self.queued()
        self.dispatcher.deliver_due(self.org)
        await self.queued()  # still pending
        with Session(self.engine) as db, db.begin():
            for row in db.scalars(sa.select(WebhookDelivery)):
                row.created_at = datetime.now(timezone.utc) - timedelta(days=31)

        class NoObjects:
            def list_refs(self, organization_id):
                return []
        report = purge_organization(self.app.state.session_factory, NoObjects(), self.org)
        self.assertEqual(report.expired_webhook_deliveries, 2)
        self.assertEqual([row.status for row in self.deliveries()], ["PENDING"])


class SigningTests(unittest.TestCase):
    def test_signatures_bind_body_and_time(self):
        secret, body = signing.new_signing_secret(), b'{"id":"e1"}'
        header = signing.sign([secret], body, timestamp=1_000_000)
        self.assertEqual(signing.verify(secret, body, header, now=1_000_100), 1_000_000)
        for bad_body, bad_header, now in ((b'{"id":"e2"}', header, 1_000_000), (body, header, 1_000_301),
                                          (body, header.replace("t=1000000", "t=1000001"), 1_000_000),
                                          (body, "v1=abc", 1_000_000), (body, "", 1_000_000)):
            with self.assertRaises(signing.SignatureError):
                signing.verify(secret, bad_body, bad_header, now=now)
        with self.assertRaises(signing.SignatureError):
            signing.verify(signing.new_signing_secret(), body, header, now=1_000_000)


class _Receiver(BaseHTTPRequestHandler):
    received = []
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append((self.path, dict(self.headers), body))
        self.send_response(type(self).status)
        if type(self).status == 302:
            self.send_header("Location", "http://169.254.169.254/")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class HTTPSenderTests(unittest.TestCase):
    def setUp(self):
        _Receiver.received, _Receiver.status = [], 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hooks/kyc?tenant=a"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_posts_to_a_pinned_address_and_reports_status(self):
        result = HTTPSender(timeout=5, allow_private=True).send(self.url, b'{"a":1}', {"Content-Type": "application/json",
                                                                                       "KYC-Signature": "t=1,v1=x"})
        self.assertEqual((result.delivered, result.status_code, result.error), (True, 200, None))
        path, headers, body = _Receiver.received[0]
        self.assertEqual((path, body, headers["KYC-Signature"]), ("/hooks/kyc?tenant=a", b'{"a":1}', "t=1,v1=x"))

    def test_private_targets_are_refused_without_connecting(self):
        result = HTTPSender(timeout=5).send(self.url, b"{}", {})
        self.assertEqual((result.status_code, result.error), (None, "TARGET_NOT_ALLOWED"))
        self.assertEqual(_Receiver.received, [])

    def test_redirects_are_not_followed_and_failures_are_codes(self):
        _Receiver.status = 302
        result = HTTPSender(timeout=5, allow_private=True).send(self.url, b"{}", {})
        self.assertEqual((result.delivered, result.error), (False, "HTTP_302"))
        self.assertEqual(len(_Receiver.received), 1)
        port = self.server.server_address[1]
        self.tearDown()
        result = HTTPSender(timeout=2, allow_private=True).send(f"http://127.0.0.1:{port}/", b"{}", {})
        self.assertEqual(result.error, "CONNECTION_ERROR")
        self.setUp()

    def test_url_checks(self):
        self.assertEqual(delivery.check_url("https://1.1.1.1:8443/a?b=c"), ("https", "1.1.1.1", 8443, "/a?b=c"))
        for url in ("https://1.1.1.1/#frag", "http://1.1.1.1/", "https://" + "a" * 2050):
            with self.assertRaises(TargetNotAllowed):
                delivery.check_url(url)
        self.assertEqual(delivery.resolve("https://1.1.1.1/").address, "1.1.1.1")
        with self.assertRaises(TargetNotAllowed):
            delivery.resolve("https://does-not-exist.invalid/")
