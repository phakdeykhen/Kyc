"""Credential formats and scopes.

API keys (`kyc_…`) belong to one organization and carry explicit scopes. Session client
tokens (`kst_…`) are issued per session for a device or browser and can only capture
evidence for, and read the status of, that one session. Both carry 256 random bits, so a
plain SHA-256 is enough to store them (unlike a password); the secret is shown once.
"""

import hashlib
import secrets

API_KEY_PREFIX = "kyc_"
CLIENT_TOKEN_PREFIX = "kst_"
MAX_CREDENTIAL_LENGTH = 200

SCOPES = {
    "sessions:write": "Create sessions, upload evidence, issue client tokens and request a decision.",
    "sessions:read": "Read session status and results (identity fields masked).",
    "results:identity": "See unmasked identity fields in results.",
    "webhooks:manage": "Create, change and remove webhook endpoints; read deliveries.",
    "keys:manage": "Create, list and revoke this organization's API keys.",
}
ALL_SCOPES = frozenset(SCOPES)
DEFAULT_SCOPES = ("sessions:write", "sessions:read")
# Held only by session client tokens; never grantable to an API key.
CLIENT_SCOPE = "session:capture"


def new_secret(prefix: str) -> tuple[str, str]:
    token = prefix + secrets.token_urlsafe(32)
    return token, digest(token)


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def display_prefix(token: str) -> str:
    """Enough of the key to recognise it in a list, never enough to use it."""
    return token[:12]
