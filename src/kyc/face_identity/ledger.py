"""Deletion deny-list outside database backups. HMAC chain detects editing/truncation vs a checkpoint.

Keep this directory on independently protected storage. Restoring BOTH the database and
an old ledger/checkpoint cannot be detected locally; production approval requires an
external immutable checkpoint and a restore drill. No raw biometrics or profile values.
"""

import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from .policy import canonical


class DeletionLedger:
    def __init__(self, path: Path, key: bytes):
        self.path, self.key = Path(path), key

    def _read(self, stream):
        stream.seek(0)
        data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024 or (data and not data.endswith(b"\n")):
            raise ValueError("DELETION_LEDGER_INVALID")
        previous, entries = "0" * 64, []
        for line in data.splitlines():
            row = json.loads(line)
            signature = row.pop("mac")
            if row["previous"] != previous or row["sequence"] != len(entries) + 1:
                raise ValueError("DELETION_LEDGER_CHAIN_INVALID")
            expected = hmac.new(self.key, canonical(row), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ValueError("DELETION_LEDGER_SIGNATURE_INVALID")
            previous = signature
            entries.append(row)
        checkpoint = json.loads(self.path.with_suffix(self.path.suffix + ".checkpoint").read_text())
        if checkpoint["sequence"] > len(entries):
            raise ValueError("DELETION_LEDGER_ROLLBACK")
        at_checkpoint = "0" * 64
        if checkpoint["sequence"]:
            line = data.splitlines()[checkpoint["sequence"] - 1]
            at_checkpoint = json.loads(line)["mac"]
        if not hmac.compare_digest(checkpoint["mac"], at_checkpoint):
            raise ValueError("DELETION_LEDGER_ROLLBACK")
        return entries, previous

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self._checkpoint(0, "0" * 64)

    def _checkpoint(self, sequence, mac):
        target = self.path.with_suffix(self.path.suffix + ".checkpoint")
        temp = target.with_suffix(target.suffix + ".tmp")
        with open(temp, "wb") as stream:
            os.chmod(temp, 0o600)
            stream.write(canonical({"sequence": sequence, "mac": mac}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def entries(self):
        with self.path.open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return self._read(stream)[0]

    def denied(self, tenant):
        return {row["enrollment"] for row in self.entries() if row["tenant"] == str(tenant)}

    def append(self, tenant, enrollment, reason):
        # A conservative deny may survive a rolled-back database transaction. That
        # safely disables the old enrollment; explicit fresh consent can create a new one.
        with self.path.open("r+b") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            entries, previous = self._read(stream)
            if any(row["tenant"] == str(tenant) and row["enrollment"] == str(enrollment) for row in entries):
                return
            row = {"sequence": len(entries) + 1, "previous": previous, "tenant": str(tenant),
                   "enrollment": str(enrollment), "reason": reason,
                   "at": datetime.now(timezone.utc).isoformat()}
            mac = hmac.new(self.key, canonical(row), hashlib.sha256).hexdigest()
            stream.seek(0, os.SEEK_END)
            stream.write(canonical({**row, "mac": mac}) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
            self._checkpoint(row["sequence"], mac)
