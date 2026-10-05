import base64
import os
from pathlib import Path
import stat
import tempfile
import unittest
from uuid import uuid4

from cryptography.exceptions import InvalidTag

from kyc.storage.captures import CaptureStorageError, LocalEncryptedCaptureStore, parse_keyring


def keyring(*versions):
    return ",".join(f"{version}:{base64.b64encode(os.urandom(32)).decode()}" for version in versions)


class CaptureStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.ring = keyring("v2", "v1")
        active, keys = parse_keyring(self.ring)
        self.store = LocalEncryptedCaptureStore(self.root, active, keys)
        self.ids = (uuid4(), uuid4(), uuid4())
        self.data = b"synthetic-capture-bytes " * 100

    def tearDown(self):
        self.directory.cleanup()

    def test_round_trip_is_encrypted_at_rest_with_private_permissions(self):
        stored = self.store.put(*self.ids, self.data)
        path = self.root / str(self.ids[0]) / str(self.ids[1]) / f"{self.ids[2]}.bin"
        blob = path.read_bytes()
        self.assertNotIn(b"synthetic-capture-bytes", blob)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stored.key_version, "v2")
        self.assertEqual(self.store.get(stored.ref, *self.ids), self.data)
        self.assertEqual(self.store.list_refs(self.ids[0]), [stored.ref])

    def test_ciphertext_is_bound_to_tenant_session_and_object(self):
        stored = self.store.put(*self.ids, self.data)
        organization, session, image = self.ids
        for wrong in [(uuid4(), session, image), (organization, uuid4(), image), (organization, session, uuid4())]:
            with self.subTest(wrong=wrong), self.assertRaises(InvalidTag):
                self.store.get(stored.ref, *wrong)

    def test_tampered_ciphertext_fails_authentication(self):
        stored = self.store.put(*self.ids, self.data)
        path = self.root / str(self.ids[0]) / str(self.ids[1]) / f"{self.ids[2]}.bin"
        blob = bytearray(path.read_bytes())
        blob[-1] ^= 1
        path.write_bytes(bytes(blob))
        with self.assertRaises(InvalidTag):
            self.store.get(stored.ref, *self.ids)

    def test_rotated_keys_still_decrypt_older_objects(self):
        old_active, old_keys = parse_keyring(self.ring.split(",")[1])
        stored = LocalEncryptedCaptureStore(self.root, old_active, old_keys).put(*self.ids, self.data)
        self.assertEqual(stored.key_version, "v1")
        self.assertEqual(self.store.get(stored.ref, *self.ids), self.data)
        retired = LocalEncryptedCaptureStore(self.root, "v3", parse_keyring(keyring("v3"))[1])
        with self.assertRaises(CaptureStorageError):
            retired.get(stored.ref, *self.ids)

    def test_references_cannot_escape_the_storage_root(self):
        org, session, image = (str(item) for item in self.ids)
        for ref in ["local://../../etc/passwd", f"local://{org}/{session}/../{image}.bin", f"gs://bucket/{org}/{session}/{image}.bin",
                    f"local://{org}/{session}/{image}.txt", f"local://{org.upper()}/{session}/{image}.bin", "local://a/b/c.bin"]:
            with self.subTest(ref=ref), self.assertRaises(CaptureStorageError):
                self.store.get(ref, *self.ids)

    def test_delete_removes_object_and_empty_directories(self):
        stored = self.store.put(*self.ids, self.data)
        self.store.delete(stored.ref)
        self.assertEqual(list(self.root.iterdir()), [])
        self.store.delete(stored.ref)  # idempotent

    def test_keyring_validation(self):
        for value in ["", "v1", "v1:not-base64!", f"v1:{base64.b64encode(b'short').decode()}",
                      f"{keyring('v1')},{keyring('v1')}", f"bad version:{base64.b64encode(os.urandom(32)).decode()}"]:
            with self.subTest(value=value[:12]), self.assertRaises(ValueError):
                parse_keyring(value)
