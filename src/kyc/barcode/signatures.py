"""Cryptographic verification of signed barcode payloads (spec §10).

Only JWS payloads are verified, and only against public keys an operator placed in the
trust store (BARCODE_TRUST_STORE: JSON object of key id → PEM public key). No official
verification mechanism is known for Cambodian document barcodes, so for them the result
is "signature present, not verifiable" rather than an invented check. `alg: none` is an
unsigned payload pretending to be signed and always fails.
"""

import base64
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from kyc.barcode.payload import ParsedPayload


class TrustStore:
    def __init__(self, keys: dict[str, object] | None = None):
        self.keys = keys or {}

    @classmethod
    def load(cls, path: Path | None) -> "TrustStore":
        if path is None:
            return cls()
        entries = json.loads(Path(path).read_text())
        return cls({kid: serialization.load_pem_public_key(pem.encode()) for kid, pem in entries.items()})


def _b64url(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def verify(parsed: ParsedPayload, trust: TrustStore) -> tuple[bool | None, str]:
    """(signature_valid, reason). None means present but not verifiable here."""
    if parsed.format == "ICAO_VDS":
        return None, "VDS_TRUST_LIST_UNAVAILABLE"
    if parsed.format != "JWS" or parsed.jws is None:
        return None, "NO_SIGNATURE"
    alg, kid = parsed.header.get("alg"), parsed.header.get("kid")
    if str(alg).lower() == "none":
        return False, "UNSIGNED_ALG_NONE"
    key = trust.keys.get(kid)
    if key is None:
        return None, "NO_TRUSTED_KEY"
    header, body, signature = parsed.jws
    message, raw = f"{header}.{body}".encode(), _b64url(signature)
    try:
        if alg in ("ES256", "ES384") and isinstance(key, ec.EllipticCurvePublicKey):
            size = len(raw) // 2
            der = encode_dss_signature(int.from_bytes(raw[:size], "big"), int.from_bytes(raw[size:], "big"))
            key.verify(der, message, ec.ECDSA(hashes.SHA256() if alg == "ES256" else hashes.SHA384()))
        elif alg in ("RS256", "PS256") and isinstance(key, rsa.RSAPublicKey):
            scheme = padding.PKCS1v15() if alg == "RS256" else padding.PSS(padding.MGF1(hashes.SHA256()), 32)
            key.verify(raw, message, scheme, hashes.SHA256())
        elif alg == "EdDSA" and isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(raw, message)
        else:
            return False, "ALGORITHM_KEY_MISMATCH"
    except (InvalidSignature, ValueError):
        return False, "SIGNATURE_INVALID"
    return True, "SIGNATURE_VALID"
