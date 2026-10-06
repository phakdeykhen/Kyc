"""Encrypted capture storage. Plaintext captures never touch disk.

Each object is sealed with AES-256-GCM. The organization, session and object IDs
are bound as associated data, so a ciphertext copied to another tenant or session
fails authentication. The local backend serves development and tests; a GCS
backend with CMEK implements the same protocol in the deployment phase.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kyc.core.crypto import parse_keyring  # noqa: F401  (re-exported for callers)

MAGIC = b"KYC1"


class CaptureStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObject:
    ref: str
    key_version: str
    sha256: str


class CaptureStore(Protocol):
    def put(self, organization_id: UUID, session_id: UUID, object_id: UUID, data: bytes) -> StoredObject: ...
    def get(self, ref: str, organization_id: UUID, session_id: UUID, object_id: UUID) -> bytes: ...
    def get_with_version(self, ref: str, organization_id: UUID, session_id: UUID,
                         object_id: UUID) -> tuple[bytes, str]: ...
    def delete(self, ref: str) -> None: ...
    def list_refs(self, organization_id: UUID) -> list[str]: ...
    def list_organization_ids(self) -> list[UUID]: ...
    def modified_at(self, ref: str) -> datetime | None: ...


class LocalEncryptedCaptureStore:
    scheme = "local://"

    def __init__(self, root: Path, active_version: str, keys: dict[str, bytes]):
        self.root = Path(root)
        self.active_version = active_version
        self.keys = keys

    @staticmethod
    def _aad(organization_id: UUID, session_id: UUID, object_id: UUID) -> bytes:
        return f"capture/{organization_id}/{session_id}/{object_id}".encode()

    def _path(self, ref: str) -> Path:
        if not ref.startswith(self.scheme):
            raise CaptureStorageError("Unsupported capture reference.")
        parts = ref[len(self.scheme):].split("/")
        if len(parts) != 3 or not parts[2].endswith(".bin"):
            raise CaptureStorageError("Malformed capture reference.")
        try:
            # Every component must be a canonical UUID, which rules out path traversal.
            ids = [UUID(parts[0]), UUID(parts[1]), UUID(parts[2][:-4])]
        except ValueError:
            raise CaptureStorageError("Malformed capture reference.") from None
        if [str(item) for item in ids] != [parts[0], parts[1], parts[2][:-4]]:
            raise CaptureStorageError("Malformed capture reference.")
        return self.root / parts[0] / parts[1] / parts[2]

    def put(self, organization_id: UUID, session_id: UUID, object_id: UUID, data: bytes) -> StoredObject:
        version = self.active_version.encode()
        nonce = os.urandom(12)
        sealed = AESGCM(self.keys[self.active_version]).encrypt(nonce, data, self._aad(organization_id, session_id, object_id))
        ref = f"{self.scheme}{organization_id}/{session_id}/{object_id}.bin"
        path = self._path(ref)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # A unique temporary name permits retry after an interrupted writer and
        # prevents two writers from sharing a temporary file.
        temporary = path.with_name(f".{path.name}.{os.urandom(12).hex()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(MAGIC + bytes([len(version)]) + version + nonce + sealed)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            # Persist the directory entry as well as the envelope before the
            # caller can commit its database key-version update.
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return StoredObject(ref=ref, key_version=self.active_version, sha256=hashlib.sha256(data).hexdigest())

    def get(self, ref: str, organization_id: UUID, session_id: UUID, object_id: UUID) -> bytes:
        return self.get_with_version(ref, organization_id, session_id, object_id)[0]

    def get_with_version(self, ref: str, organization_id: UUID, session_id: UUID,
                         object_id: UUID) -> tuple[bytes, str]:
        """Return authenticated bytes and the envelope's actual key version.

        The envelope, rather than a database hint, is authoritative after an
        interrupted key rotation. Read both from one snapshot of the file.
        """
        blob = self._path(ref).read_bytes()
        if len(blob) < 34 or blob[:4] != MAGIC:
            raise CaptureStorageError("Unknown capture envelope.")
        length = blob[4]
        if not 1 <= length <= 64 or len(blob) < 5 + length + 12 + 16:
            raise CaptureStorageError("Malformed capture envelope.")
        try:
            version = blob[5:5 + length].decode("ascii")
        except UnicodeDecodeError:
            raise CaptureStorageError("Malformed capture envelope.") from None
        nonce, sealed = blob[5 + length:17 + length], blob[17 + length:]
        if version not in self.keys:
            raise CaptureStorageError("Capture key version is not available.")
        return AESGCM(self.keys[version]).decrypt(nonce, sealed, self._aad(organization_id, session_id, object_id)), version

    def delete(self, ref: str) -> None:
        path = self._path(ref)
        path.unlink(missing_ok=True)
        for directory in (path.parent, path.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break

    def list_refs(self, organization_id: UUID) -> list[str]:
        base = self.root / str(organization_id)
        if not base.is_dir():
            return []
        return sorted(f"{self.scheme}{organization_id}/{item.parent.name}/{item.name}" for item in base.glob("*/*.bin"))

    def list_organization_ids(self) -> list[UUID]:
        """Include orphaned tenant namespaces that no longer have database rows."""
        if not self.root.is_dir():
            return []
        organizations = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or not any(directory.glob("*/*.bin")):
                continue
            try:
                organization = UUID(directory.name)
            except ValueError:
                raise CaptureStorageError("Malformed capture organization directory.") from None
            if str(organization) != directory.name:
                raise CaptureStorageError("Malformed capture organization directory.")
            organizations.append(organization)
        return sorted(organizations)

    def modified_at(self, ref: str) -> datetime | None:
        path = self._path(ref)
        if not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
