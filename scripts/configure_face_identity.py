"""Append independent development face-identity secrets without enabling recognition.

Preserves existing .env values. Production secrets, independent ledger storage,
trusted approval keys and capture-provider validation require operator provisioning.
"""
import base64
import os
from pathlib import Path
import secrets

from dotenv import dotenv_values
import sqlalchemy as sa


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    if not target.is_file():
        raise SystemExit("Run scripts/configure_local.py first.")
    current = dotenv_values(target)
    url = sa.engine.make_url(current["DATABASE_URL"])
    password = secrets.token_urlsafe(40)
    values = {
        "FACE_RECOGNITION_ENABLED": "false",
        "FACE_RECOGNITION_DATABASE_URL": url.set(username="kyc_biometrics", password=password).render_as_string(hide_password=False),
        "FACE_RECOGNITION_KEYS": "face-v1:" + base64.b64encode(secrets.token_bytes(32)).decode(),
        "FACE_RECOGNITION_LEDGER_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "FACE_RECOGNITION_LEDGER_PATH": "var/face-deletions.jsonl",
        "FACE_RECOGNITION_POLICY": "config/approved-face-identity.json",
        "FACE_RECOGNITION_APPROVAL_KEY": "config/face-identity-approval.pub.pem",
    }
    missing = {key: value for key, value in values.items() if key not in current}
    with target.open("a") as stream:
        if missing:
            stream.write("\n# Optional face identity: independently disabled until its release gates pass.\n")
            for key, value in missing.items():
                stream.write(f"{key}={value}\n")
    os.chmod(target, 0o600)
    print("Independent face identity settings added; existing values preserved. Recognition remains disabled. No secrets printed.")


if __name__ == "__main__":
    main()
