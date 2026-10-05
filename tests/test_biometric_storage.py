import base64
import os
import unittest
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from pydantic import ValidationError

from kyc.storage.biometrics import BiometricCipher, MAX_TEMPLATE_BYTES
from tests.test_api import configuration
from tests.test_capture_store import keyring


class BiometricStorageTests(unittest.TestCase):
    def setUp(self):
        self.ring = keyring("bio-v2", "bio-v1")
        self.cipher = BiometricCipher(self.ring)
        self.context = f"biometric/{uuid4()}/{uuid4()}/{uuid4()}/LIVE_SELFIE/OpenCV SFace/2021dec/" + "a" * 64
        self.payload = b'{"vector":[0.1,0.2],"model":"test-only"}'

    def test_ciphertext_round_trip_is_separately_keyed(self):
        sealed, version = self.cipher.seal(self.payload, self.context)
        self.assertEqual(version, "bio-v2")
        self.assertNotIn(b"vector", sealed)
        self.assertEqual(self.cipher.open(sealed, version, self.context), self.payload)
        other_key = BiometricCipher(keyring("bio-v2"))
        with self.assertRaises(InvalidTag):
            other_key.open(sealed, version, self.context)

    def test_tenant_session_template_source_and_model_are_authenticated(self):
        sealed, version = self.cipher.seal(self.payload, self.context)
        for index in range(1, 8):
            parts = self.context.split("/")
            parts[index] = str(uuid4())
            with self.subTest(index=index), self.assertRaises(InvalidTag):
                self.cipher.open(sealed, version, "/".join(parts))

    def test_tampering_is_rejected(self):
        sealed, version = self.cipher.seal(self.payload, self.context)
        changed = sealed[:-1] + bytes([sealed[-1] ^ 1])
        with self.assertRaises(InvalidTag):
            self.cipher.open(changed, version, self.context)

    def test_rotation_keeps_existing_templates_decryptable(self):
        old = BiometricCipher(self.ring.split(",")[1])
        sealed, version = old.seal(self.payload, self.context)
        self.assertEqual(version, "bio-v1")
        self.assertEqual(self.cipher.open(sealed, version, self.context), self.payload)
        retired = BiometricCipher(keyring("bio-v3"))
        with self.assertRaises(ValueError):
            retired.open(sealed, version, self.context)

    def test_payload_size_and_incomplete_contexts_are_bounded(self):
        for payload in (b"", b"x" * (MAX_TEMPLATE_BYTES + 1)):
            with self.subTest(size=len(payload)), self.assertRaises(ValueError):
                self.cipher.seal(payload, self.context)
        for context in ("field/o/s/t/x", "biometric/o/s/t"):
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.cipher.seal(self.payload, context)
        for envelope in (b"", b"BAD!" + b"x" * 100):
            with self.subTest(envelope=envelope[:4]), self.assertRaises(ValueError):
                self.cipher.open(envelope, "bio-v2", self.context)

    def test_config_rejects_reusing_capture_or_pii_keys_for_biometrics(self):
        same = keyring("v1")
        for settings in ({"capture_encryption_keys": same},
                         {"pii_encryption_keys": same, "pii_hmac_key": base64.b64encode(os.urandom(32)).decode()}):
            with self.subTest(settings=list(settings)), self.assertRaises(ValidationError):
                configuration(uuid4(), biometric_encryption_keys=same, **settings)
