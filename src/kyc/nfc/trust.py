"""Country Signing CA (CSCA) trust store.

Operators provision CSCA certificates (for example extracted from the ICAO PKD or
national master lists) into a directory of PEM/DER files. The store's version is a hash
of the sorted certificate fingerprints, so every verification records exactly which set
of trust anchors it relied on.
"""

import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes


class CSCATrustStore:
    def __init__(self, certificates: list[x509.Certificate] | None = None):
        self.certificates = certificates or []
        fingerprints = sorted(cert.fingerprint(hashes.SHA256()).hex()
                              for cert in self.certificates)
        self.version = ("csca-" + hashlib.sha256("".join(fingerprints).encode()).hexdigest()[:16]) if fingerprints else None

    @classmethod
    def load(cls, directory: Path | None) -> "CSCATrustStore":
        if directory is None or not Path(directory).is_dir():
            return cls()
        certificates = []
        for path in sorted(Path(directory).iterdir()):
            if path.suffix.lower() not in {".pem", ".crt", ".cer", ".der"}:
                continue
            data = path.read_bytes()
            certificates.append(x509.load_pem_x509_certificate(data) if b"-----BEGIN" in data
                                else x509.load_der_x509_certificate(data))
        return cls(certificates)

    def issuers_of(self, certificate: x509.Certificate) -> list[x509.Certificate]:
        return [anchor for anchor in self.certificates if anchor.subject == certificate.issuer]
