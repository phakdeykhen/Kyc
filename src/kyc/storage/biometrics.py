"""Separately keyed biometric encryption; vectors never enter capture or PII storage."""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kyc.core.crypto import parse_keyring

MAGIC = b"KBT1"
MAX_TEMPLATE_BYTES = 64 * 1024


class BiometricCipher:
    """AES-256-GCM bytes, bound to a tenant/session/template/source/model context.

    The service supplies the complete context, including the model's SHA-256. There
    is no pickle or executable serialization. Key rotation retains older decryptors.
    """

    def __init__(self, keyring: str):
        self.active, self.keys = parse_keyring(keyring)

    @staticmethod
    def _aad(context: str) -> bytes:
        if not context.startswith("biometric/") or len(context.split("/")) < 8:
            raise ValueError("A complete biometric tenant/session/template/source/model context is required.")
        return context.encode("utf-8")

    def seal(self, payload: bytes, context: str) -> tuple[bytes, str]:
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_TEMPLATE_BYTES:
            raise ValueError("Invalid biometric payload size.")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.keys[self.active]).encrypt(nonce, payload, self._aad(context))
        return MAGIC + nonce + ciphertext, self.active

    def open(self, sealed: bytes, key_version: str, context: str) -> bytes:
        if key_version not in self.keys:
            raise ValueError("Biometric key version is unavailable.")
        if not isinstance(sealed, bytes) or not 32 <= len(sealed) <= MAX_TEMPLATE_BYTES + 32 or sealed[:4] != MAGIC:
            raise ValueError("Invalid biometric envelope.")
        return AESGCM(self.keys[key_version]).decrypt(sealed[4:16], sealed[16:], self._aad(context))
