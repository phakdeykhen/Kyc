"""Webhook signatures.

    KYC-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>[,v1=<…>]

The timestamp is signed with the body, so a captured request cannot be replayed later
than the receiver's tolerance (default five minutes). Receivers also deduplicate on the
event ID, because a delivery can arrive more than once (at-least-once retries). During
a secret rotation the header carries one v1 signature per valid secret.
"""

import hashlib
import hmac
import secrets
import time

SECRET_PREFIX = "whsec_"
SIGNATURE_HEADER = "KYC-Signature"
DEFAULT_TOLERANCE_SECONDS = 300


class SignatureError(ValueError):
    pass


def new_signing_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def compute(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def sign(secrets_: list[str], body: bytes, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    return ",".join([f"t={timestamp}"] + [f"v1={compute(secret, timestamp, body)}" for secret in secrets_])


def verify(secret: str, body: bytes, header: str, tolerance: int = DEFAULT_TOLERANCE_SECONDS,
           now: int | None = None) -> int:
    """Raise SignatureError unless one v1 signature matches and the timestamp is fresh; return the timestamp."""
    timestamp, signatures = None, []
    for item in (header or "").split(","):
        name, _, value = item.strip().partition("=")
        if name == "t" and value.isdigit():
            timestamp = int(value)
        elif name == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise SignatureError("Malformed signature header.")
    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > tolerance:
        raise SignatureError("Signature timestamp is outside the tolerance.")
    expected = compute(secret, timestamp, body)
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise SignatureError("No signature matches.")
    return timestamp
