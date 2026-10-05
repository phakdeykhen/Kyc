"""Active Authentication (ICAO Doc 9303 Part 11 §6.1): the chip proves it holds the
private key matching DG15 by signing a fresh server nonce. Copied (cloned) chip data
cannot do this. RSA uses ISO/IEC 9796-2 digital signature scheme 1; ECDSA uses plain
(r‖s) signatures.
"""

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

TRAILERS = {b"\xbc": "sha1", b"\x38\xcc": "sha224", b"\x34\xcc": "sha256", b"\x36\xcc": "sha384", b"\x35\xcc": "sha512"}


def _iso9796_2(public_key: rsa.RSAPublicKey, signature: bytes, challenge: bytes) -> bool:
    numbers = public_key.public_numbers()
    size = (numbers.n.bit_length() + 7) // 8
    value = pow(int.from_bytes(signature, "big"), numbers.e, numbers.n)
    for candidate in (value, numbers.n - value):  # either representative is permitted
        message = candidate.to_bytes(size, "big")
        if message[0] & 0xF0 != 0x60:
            continue
        for trailer, hash_name in TRAILERS.items():
            if not message.endswith(trailer):
                continue
            digest_size = hashlib.new(hash_name).digest_size
            body = message[:len(message) - len(trailer)]
            digest, recovered = body[-digest_size:], body[1:-digest_size]
            if message[0] == 0x6A and hashlib.new(hash_name, recovered + challenge).digest() == digest:
                return True
    return False


def verify(public_key, challenge: bytes, signature: bytes) -> bool:
    if isinstance(public_key, rsa.RSAPublicKey):
        return _iso9796_2(public_key, signature, challenge)
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        half = len(signature) // 2
        if len(signature) % 2 or half == 0:
            return False
        der = encode_dss_signature(int.from_bytes(signature[:half], "big"), int.from_bytes(signature[half:], "big"))
        digest = hashes.SHA256() if public_key.curve.key_size <= 256 else hashes.SHA384() if public_key.curve.key_size <= 384 else hashes.SHA512()
        try:
            public_key.verify(der, challenge, ec.ECDSA(digest))
            return True
        except InvalidSignature:
            return False
    return False
