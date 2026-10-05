"""Generate local credentials without printing them or overwriting an existing .env."""

import base64
from pathlib import Path
import os
import secrets
from uuid import uuid4

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    print("Existing .env preserved.")
    if "CAPTURE_ENCRYPTION_KEYS=" not in target.read_text():
        # Phase 2 upgrade path: append only the new capture key, never rewrite existing secrets.
        with target.open("a") as handle:
            handle.write("CAPTURE_ENCRYPTION_KEYS=local-v1:" + base64.b64encode(secrets.token_bytes(32)).decode() + "\n")
            handle.write("CAPTURE_STORAGE_DIR=var/captures\n")
        print("Appended a generated capture encryption key to .env; no secrets were printed.")
    if "PII_ENCRYPTION_KEYS=" not in target.read_text():
        # Phase 3 upgrade path: separate keys for extracted identity fields.
        with target.open("a") as handle:
            handle.write("PII_ENCRYPTION_KEYS=pii-v1:" + base64.b64encode(secrets.token_bytes(32)).decode() + "\n")
            handle.write("PII_HMAC_KEY=" + base64.b64encode(secrets.token_bytes(32)).decode() + "\n")
        print("Appended generated PII field keys to .env; no secrets were printed.")
else:
    app_password = secrets.token_urlsafe(32)
    migration_password = secrets.token_urlsafe(32)
    values = {
        "ENVIRONMENT": "development",
        "DATABASE_URL": f"postgresql+psycopg2://kyc_app:{app_password}@127.0.0.1:5432/kyc",
        "MIGRATION_DATABASE_URL": f"postgresql+psycopg2://kyc_migrator:{migration_password}@127.0.0.1:5432/kyc",
        "DEVELOPMENT_API_KEY": secrets.token_urlsafe(48),
        "DEVELOPMENT_ORGANIZATION_ID": str(uuid4()),
        "POSTGRES_PASSWORD": migration_password,
        "KYC_APP_PASSWORD": app_password,
        "SESSION_TTL_SECONDS": "900",
        "DB_POOL_SIZE": "5",
        "DB_MAX_OVERFLOW": "5",
        "CAPTURE_ENCRYPTION_KEYS": "local-v1:" + base64.b64encode(secrets.token_bytes(32)).decode(),
        "CAPTURE_STORAGE_DIR": "var/captures",
        "PII_ENCRYPTION_KEYS": "pii-v1:" + base64.b64encode(secrets.token_bytes(32)).decode(),
        "PII_HMAC_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
    }
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write("# Generated local development credentials. Keep this file private.\n")
        handle.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    print("Created .env with generated local credentials; no secrets were printed.")
