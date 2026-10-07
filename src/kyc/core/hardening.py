"""Phase 17 production readiness gate.

`ENVIRONMENT=production` starts only when every control below is in place. The checks
name what is missing and never echo a secret value, so the error is safe to log.
"""

import os

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from kyc.core.crypto import decode_key, parse_keyring
from kyc.db.privileges import API_ROLE

# libpq modes that refuse a plaintext connection.
TLS_MODES = {"require", "verify-ca", "verify-full"}
KEYRINGS = ("capture_encryption_keys", "pii_encryption_keys", "biometric_encryption_keys", "webhook_secret_keys")


def keyring_values(settings) -> dict[str, set[bytes]]:
    """Raw key bytes per configured keyring (and the PII lookup key), for separation checks."""
    found = {}
    for name in KEYRINGS:
        value = getattr(settings, name)
        if value is not None:
            found[name] = set(parse_keyring(value.get_secret_value())[1].values())
    if settings.pii_hmac_key is not None:
        found["pii_hmac_key"] = {decode_key(settings.pii_hmac_key.get_secret_value())}
    return found


def shared_keys(settings) -> list[str]:
    """Pairs of key settings that share key material. Each purpose must have its own keys."""
    found = keyring_values(settings)
    names = sorted(found)
    return [f"{first} and {second}" for index, first in enumerate(names) for second in names[index + 1:]
            if found[first] & found[second]]


def database_uses_tls(url: str) -> bool:
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError, TypeError):
        return False
    query = parsed.query
    # Repeated parameters become tuples; ambiguous TLS options must not pass the gate.
    if isinstance(query.get("sslmode"), str) and query["sslmode"] in TLS_MODES:
        return True
    # A Unix socket (Cloud SQL connector, /cloudsql/...) never crosses the network unencrypted.
    hosts = query.get("host") or parsed.host or ""
    hosts = (hosts,) if isinstance(hosts, str) else hosts
    # libpq supports multiple hosts and hostaddr takes precedence for TCP routing.
    # A socket-looking first host must not hide another unencrypted TCP target.
    # libpq service files and PGHOSTADDR can override socket-looking hosts with TCP.
    if query.get("hostaddr") or os.environ.get("PGHOSTADDR") or query.get("service") or os.environ.get("PGSERVICE"):
        return False
    return bool(hosts) and all(item.startswith("/") for host in hosts for item in host.split(","))


def allowed_host_list(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def database_uses_api_role(url: str) -> bool:
    """Require the runtime role, including libpq's query-level user override."""
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError, TypeError):
        return False
    return parsed.username == API_ROLE and parsed.query.get("user", API_ROLE) == API_ROLE


def production_problems(settings) -> list[str]:
    problems = []
    if settings.development_api_key is not None:
        problems.append("DEVELOPMENT_API_KEY must be unset; use provisioned API keys.")
    if not database_uses_api_role(settings.database_url.get_secret_value()):
        problems.append(f"DATABASE_URL must use the {API_ROLE} runtime role; administrative credentials belong in MIGRATION_DATABASE_URL.")
    if not database_uses_tls(settings.database_url.get_secret_value()):
        problems.append("DATABASE_URL must use sslmode=require, verify-ca or verify-full, or a Unix socket.")
    for name in KEYRINGS:
        if getattr(settings, name) is None:
            problems.append(f"{name.upper()} is required.")
    if settings.pii_hmac_key is None:
        problems.append("PII_HMAC_KEY is required.")
    if settings.webhook_allow_private_targets:
        problems.append("WEBHOOK_ALLOW_PRIVATE_TARGETS must be false.")
    hosts = allowed_host_list(settings.allowed_hosts)
    if not hosts or "*" in hosts:
        problems.append("ALLOWED_HOSTS must list the public host names (no unrestricted wildcard).")
    if not settings.require_document_consent:
        problems.append("REQUIRE_DOCUMENT_CONSENT must be true.")
    if settings.enable_capture_client:
        problems.append("ENABLE_CAPTURE_CLIENT must be false; the /capture page is a development client.")
    return problems
