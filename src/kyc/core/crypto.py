"""Keyrings and AES-256-GCM field sealing shared by capture storage and PII fields."""

import base64
import binascii
import hashlib
import hmac
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def parse_keyring(value: str) -> tuple[str, dict[str, bytes]]:
    """'version:base64key[,version:base64key]'. The first entry encrypts; all entries decrypt."""
    keys: dict[str, bytes] = {}
    active = None
    for entry in filter(None, (part.strip() for part in value.split(","))):
        version, separator, encoded = entry.partition(":")
        if not separator or not VERSION_PATTERN.match(version) or version in keys:
            raise ValueError("Keys must be unique 'version:base64key' entries.")
        try:
            key = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Keys must be base64 encoded.") from None
        if len(key) != 32:
            raise ValueError("Keys must be 32 bytes (AES-256).")
        keys[version] = key
        active = active or version
    if active is None:
        raise ValueError("At least one key is required.")
    return active, keys


def decode_key(encoded: str) -> bytes:
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Keys must be base64 encoded.") from None
    if len(key) != 32:
        raise ValueError("Keys must be 32 bytes.")
    return key


class FieldCipher:
    """Seals short PII values. Associated data binds each value to its tenant/record/field."""

    def __init__(self, keyring: str, hmac_key: bytes):
        self.active, self.keys = parse_keyring(keyring)
        self.hmac_key = hmac_key

    def seal(self, value: str, context: str) -> tuple[bytes, str]:
        nonce = os.urandom(12)
        return nonce + AESGCM(self.keys[self.active]).encrypt(nonce, value.encode(), context.encode()), self.active

    def open(self, sealed: bytes, key_version: str, context: str) -> str:
        return AESGCM(self.keys[key_version]).decrypt(sealed[:12], sealed[12:], context.encode()).decode()

    def lookup_hash(self, value: str, namespace: str) -> str:
        """Keyed, non-reversible lookup value for duplicate detection; never a plain hash."""
        return hmac.new(self.hmac_key, f"{namespace}|{value}".encode(), hashlib.sha256).hexdigest()
