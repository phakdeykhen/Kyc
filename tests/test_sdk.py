"""Phase 16: the Python SDK against the real application (in-process transport, no socket)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))
from kyc_sdk import KYCAPIError, KYCClient, SessionClient, verify_webhook  # noqa: E402
from kyc_sdk.webhooks import WebhookVerificationError  # noqa: E402

from kyc.webhooks import signing  # noqa: E402
from tests.helpers import call  # noqa: E402
from tests.test_api import TEST_KEY  # noqa: E402
from tests.test_webhooks import PUBLIC_URL, WebhookCase  # noqa: E402

BASE = "https://kyc.test"


def asgi_transport(app):
    """Runs each SDK request through the ASGI app on a private event loop thread."""
    pool = ThreadPoolExecutor(max_workers=1)

    def send(method, url, headers, body):
        headers = dict(headers)
        content_type = headers.pop("Content-Type", "application/json")
        headers.pop("Content-Length", None)
        status, parsed, response_headers = pool.submit(asyncio.run, call(
            app, url.removeprefix(BASE), method, headers=headers, raw=body or b"", content_type=content_type)).result()
        data = parsed if isinstance(parsed, bytes) else json.dumps(parsed).encode()
        return status, response_headers, data
    return send


class PythonSDKTests(WebhookCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.transport = asgi_transport(self.app)
        self.client = KYCClient(BASE, TEST_KEY, str(self.org), transport=self.transport)

    async def test_server_and_device_flows(self):
        session = self.client.create_session("sdk-customer", "KH", "KH_NATIONAL_ID", idempotency_key="sdk-signup-0001")
        self.assertEqual(self.client.create_session("sdk-customer", "KH", "KH_NATIONAL_ID",
                                                    idempotency_key="sdk-signup-0001")["session_id"], session["session_id"])
        token = self.client.issue_client_token(session["session_id"])
        device = SessionClient(BASE, token["client_token"], str(self.org), transport=self.transport)
        self.assertEqual(device.get_session(session["session_id"])["status"], "CREATED")
        with self.assertRaises(KYCAPIError) as raised:
            device.upload_document(session["session_id"], "FRONT", b"not an image")
        self.assertEqual(raised.exception.status, 422)  # authorized; the quality gate refuses a non-image
        self.assertIsNotNone(raised.exception.request_id)
        with self.assertRaises(KYCAPIError) as raised:
            device._request("GET", f"/v1/kyc/{session['session_id']}/result")
        self.assertEqual(raised.exception.status, 403)
        self.assertIn("identity_masked", self.client.get_result(session["session_id"]))
        self.assertEqual(self.client.organization()["credential"]["type"], "development_key")

    async def test_keys_and_webhooks(self):
        key = self.client.create_api_key("sdk key", ["sessions:read"], expires_in_days=7)
        self.assertEqual([item["id"] for item in self.client.list_api_keys()], [key["id"]])
        self.assertEqual(self.client.revoke_api_key(key["id"])["status"], "REVOKED")
        with self.assertRaises(KYCAPIError) as raised:
            KYCClient(BASE, key["api_key"], str(self.org), transport=self.transport).document_types()
        self.assertEqual(raised.exception.status, 401)

        endpoint = self.client.create_webhook(PUBLIC_URL, ["kyc.verified"], "SDK test")
        self.assertEqual(self.client.update_webhook(endpoint["id"], event_types=[])["event_types"], [])
        queued = self.client.test_webhook(endpoint["id"])
        self.dispatcher.deliver_due(self.org)
        _, body, headers = self.sender.sent[-1]
        event = verify_webhook(body, headers["KYC-Signature"], endpoint["secret"])
        self.assertEqual((event["type"], event["id"]), ("webhook.test", headers["KYC-Event-ID"]))
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(body + b" ", headers["KYC-Signature"], endpoint["secret"])
        delivered = self.client.list_deliveries(endpoint["id"], status="DELIVERED")
        self.assertEqual([item["id"] for item in delivered], [queued["id"]])
        self.assertEqual(self.client.redeliver(endpoint["id"], queued["id"])["status"], "PENDING")
        rotated = self.client.rotate_webhook_secret(endpoint["id"])
        self.assertNotEqual(rotated["secret"], endpoint["secret"])
        self.assertFalse(self.client.delete_webhook(endpoint["id"])["active"])
        self.assertEqual(self.client.list_webhooks(), [])
        self.assertEqual(len(self.client.list_webhooks(include_deleted=True)), 1)

    def test_sdk_verifier_matches_the_server_signer(self):
        secret, body = signing.new_signing_secret(), b'{"id":"evt","type":"kyc.verified"}'
        header = signing.sign([signing.new_signing_secret(), secret], body)
        self.assertEqual(verify_webhook(body, header, secret)["id"], "evt")
        old = signing.sign([secret], body, timestamp=1_000)
        with self.assertRaises(WebhookVerificationError):
            verify_webhook(body, old, secret)  # replayed outside the tolerance

    async def test_device_consent_and_server_erasure(self):
        self.app.state.settings.require_document_consent = True
        session = self.client.create_session("privacy-sdk-customer", "KH", "KH_PASSPORT",
                                             verification_level="DOCUMENT_ONLY")
        token = self.client.issue_client_token(session["session_id"])
        device = SessionClient(BASE, token["client_token"], str(self.org), transport=self.transport)
        with self.assertRaises(KYCAPIError) as raised:
            device.upload_document(session["session_id"], "DATA_PAGE", b"not an image")
        self.assertEqual(raised.exception.body["reason_code"], "DOCUMENT_CONSENT_REQUIRED")
        self.assertEqual(device.give_document_consent(session["session_id"])["scope"], "DOCUMENT_PROCESSING")
        with self.assertRaises(KYCAPIError) as raised:
            device.upload_document(session["session_id"], "DATA_PAGE", b"not an image")
        self.assertNotEqual(raised.exception.body.get("reason_code"), "DOCUMENT_CONSENT_REQUIRED")
        erased = self.client.erase(session["session_id"])
        self.assertFalse(erased["already_erased"])
        self.assertTrue(self.client.erase(session["session_id"])["already_erased"])
        with self.assertRaises(KYCAPIError) as raised:
            device.get_session(session["session_id"])
        self.assertEqual(raised.exception.status, 401, "Erasure revokes the device token")
