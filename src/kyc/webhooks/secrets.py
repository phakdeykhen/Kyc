"""Webhook signing-secret encryption with its own keyring (Phase 17).

Phase 16 sealed signing secrets with the PII keyring. Secrets sealed with
WEBHOOK_SECRET_KEYS store their key version with a `wh:` tag, so both kinds can be
opened while scripts/rotate_keys.py moves the old ones over. Without a webhook keyring
(development only), new secrets still use the PII keyring.
"""

from kyc.core.crypto import FieldCipher

TAG = "wh:"


class WebhookSecretCipher:
    def __init__(self, keyring: str | None, legacy: FieldCipher | None):
        # FieldCipher's lookup-hash key is never used for secrets; the keyring is what matters.
        self.dedicated = FieldCipher(keyring, b"") if keyring else None
        self.legacy = legacy

    @property
    def configured(self) -> bool:
        return self.dedicated is not None or self.legacy is not None

    @property
    def active_version(self) -> str | None:
        if self.dedicated is not None:
            return TAG + self.dedicated.active
        return self.legacy.active if self.legacy is not None else None

    def seal(self, secret: str, context: str) -> tuple[bytes, str]:
        if self.dedicated is not None:
            sealed, version = self.dedicated.seal(secret, context)
            return sealed, TAG + version
        if self.legacy is None:
            raise RuntimeError("No keyring is configured for webhook secrets.")
        return self.legacy.seal(secret, context)

    def open(self, sealed: bytes, key_version: str, context: str) -> str:
        if key_version.startswith(TAG):
            if self.dedicated is None:
                raise RuntimeError("WEBHOOK_SECRET_KEYS is needed to open this webhook secret.")
            return self.dedicated.open(sealed, key_version[len(TAG):], context)
        if self.legacy is None:
            raise RuntimeError("PII_ENCRYPTION_KEYS is needed to open this webhook secret.")
        return self.legacy.open(sealed, key_version, context)


def build_webhook_cipher(settings, field_cipher: FieldCipher | None) -> WebhookSecretCipher | None:
    keyring = settings.webhook_secret_keys.get_secret_value() if settings.webhook_secret_keys is not None else None
    cipher = WebhookSecretCipher(keyring, field_cipher)
    return cipher if cipher.configured else None
