"""Verify the platform's webhook signatures.

    event = verify_webhook(raw_body, request.headers["KYC-Signature"], secret)

Use the raw request bytes, not re-serialized JSON. Then deduplicate on `event["id"]`:
deliveries are at-least-once, and a retry carries the same event ID.
"""

import hashlib
import hmac
import json
import time

SIGNATURE_HEADER = "KYC-Signature"
DEFAULT_TOLERANCE_SECONDS = 300


class WebhookVerificationError(ValueError):
    pass


def verify_webhook(payload: bytes, signature_header: str, secret: str,
                   tolerance: int = DEFAULT_TOLERANCE_SECONDS, now: int | None = None) -> dict:
    """Return the event if a v1 signature matches and the timestamp is within the tolerance."""
    timestamp, signatures = None, []
    for item in (signature_header or "").split(","):
        name, _, value = item.strip().partition("=")
        if name == "t" and value.isdigit():
            timestamp = int(value)
        elif name == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise WebhookVerificationError("Malformed signature header.")
    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > tolerance:
        raise WebhookVerificationError("Signature timestamp is outside the tolerance (possible replay).")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise WebhookVerificationError("Signature does not match.")
    return json.loads(payload)
