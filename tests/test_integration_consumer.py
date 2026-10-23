"""The documented receiver must survive forgery, replay, duplication and reordering."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))
sys.path.insert(0, str(ROOT / "examples" / "integration"))
from kyc_sdk.webhooks import verify_webhook, WebhookVerificationError
from kyc.webhooks.signing import sign
from webhook_receiver import accept_result, connect


class IntegrationConsumerTests(unittest.TestCase):
    def event(self, version):
        return {"id": str(uuid4()), "type": "verification.result.updated", "organization_id": "synthetic-tenant",
                "data": {"application_id": "synthetic-app", "session_id": "synthetic-session", "result_version": version, "sandbox": True}}

    def test_exact_body_timestamp_and_rotation(self):
        secret, previous = "whsec_synthetic-new", "whsec_synthetic-previous"
        event = self.event(2)
        body = json.dumps(event, indent=2).encode()
        header = sign([secret, previous], body, timestamp=1000)
        self.assertEqual(verify_webhook(body, header, previous, now=1000), event)
        self.assertEqual(verify_webhook(body, header, secret, now=1000), event)
        for payload, signed, timestamp in ((json.dumps(event).encode(), header, 1000), (body, header, 1301),
                                            (body, header, 699), (body, "t=1000,v1=forged", 1000)):
            with self.assertRaises(WebhookVerificationError):
                verify_webhook(payload, signed, secret, now=timestamp)

    def test_durable_duplicates_and_out_of_order_versions(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "consumer.sqlite")
            newer, older = self.event(5), self.event(3)
            result = {"session_id": "synthetic-session", "result_version": 5, "outcome": "VERIFIED", "sandbox": True}
            with connect(path) as db:
                self.assertTrue(accept_result(db, newer, result))
            with connect(path) as db:
                self.assertFalse(accept_result(db, newer, result))
                self.assertTrue(accept_result(db, older, result | {"result_version": 3, "outcome": "MANUAL_REVIEW"}))
                self.assertEqual(db.execute("SELECT version, outcome FROM results").fetchone(), (5, "VERIFIED"))
                with self.assertRaises(ValueError):
                    accept_result(db, self.event(6), result)
                self.assertEqual(db.execute("SELECT count(*) FROM processed_events").fetchone()[0], 2)
