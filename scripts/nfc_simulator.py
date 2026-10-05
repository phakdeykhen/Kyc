"""Development ePassport chip simulator: plays the mobile app's NFC step against the API.

Real chips are read by the mobile SDK (JMRTD on Android, Core NFC on iOS). This script
creates a FICTIONAL test PKI and synthetic chips so the server-side verification can be
exercised end to end without hardware. Never point a production trust store at it.

  python scripts/nfc_simulator.py init --pki-dir var/nfc-test-pki
      → writes a test CSCA to var/nfc-test-pki/trust/ (set NFC_CSCA_TRUST_STORE to that folder)
  python scripts/nfc_simulator.py read --pki-dir var/nfc-test-pki --session <uuid> [--portrait face.jpg] [--clone]
      → requests an AA challenge, builds a chip signed by the test Document Signer, uploads it
"""

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from cryptography.hazmat.primitives import serialization  # noqa: E402
from PIL import Image  # noqa: E402

from tests import nfc_fixtures as fx  # noqa: E402

PEM = serialization.Encoding.PEM


def init(pki_dir: Path) -> None:
    pki = fx.make_pki()
    (pki_dir / "trust").mkdir(parents=True, exist_ok=True)
    (pki_dir / "trust" / "test-csca.pem").write_bytes(pki.csca_pem())
    for name, key in (("csca-key.pem", pki.csca_key), ("dsc-key.pem", pki.dsc_key)):
        path = pki_dir / name
        path.write_bytes(key.private_bytes(PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        os.chmod(path, 0o600)
    (pki_dir / "dsc.pem").write_bytes(pki.dsc.public_bytes(PEM))
    print(f"Test CSCA written to {pki_dir / 'trust'}; set NFC_CSCA_TRUST_STORE to that folder and restart the API.")


def load(pki_dir: Path) -> fx.TestPKI:
    from cryptography import x509
    csca = x509.load_pem_x509_certificate((pki_dir / "trust" / "test-csca.pem").read_bytes())
    dsc = x509.load_pem_x509_certificate((pki_dir / "dsc.pem").read_bytes())
    key = lambda name: serialization.load_pem_private_key((pki_dir / name).read_bytes(), None)  # noqa: E731
    return fx.TestPKI(key("csca-key.pem"), csca, key("dsc-key.pem"), dsc)


def request(url, headers, data=None, content_type=None):
    req = urllib.request.Request(url, data=data, method="POST", headers={**headers, **({"Content-Type": content_type} if content_type else {})})
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def read(args) -> None:
    pki = load(args.pki_dir)
    portrait = Image.open(args.portrait).convert("RGB") if args.portrait else None
    chip = fx.make_chip(pki=pki, portrait=portrait)
    headers = {"X-API-Key": os.environ["DEVELOPMENT_API_KEY"], "X-Organization-ID": os.environ["DEVELOPMENT_ORGANIZATION_ID"]}
    base = f"{args.base_url}/v1/kyc/{args.session}"
    code, challenge = request(f"{base}/nfc/challenge", headers, b"{}", "application/json")
    if code != 200:
        sys.exit(f"challenge failed: {code} {challenge}")
    signer = fx.aa_rsa_key() if args.clone else chip.aa_key  # a clone copies data but not the chip's private key
    fields = {"read_status": "READ", "access_protocol": "PACE", "challenge_id": challenge["challenge_id"],
              "aa_signature": fx.aa_sign(signer, bytes.fromhex(challenge["challenge"])).hex()}
    files = {"sod": chip.sod, "dg1": chip.groups[1], "dg2": chip.groups[2], "dg15": chip.groups[15]}
    boundary = uuid.uuid4().hex
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    body += b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{k}.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                     + v + b"\r\n" for k, v in files.items())
    body += f"--{boundary}--\r\n".encode()
    code, result = request(base + "/nfc", headers, body, f"multipart/form-data; boundary={boundary}")
    print(code, json.dumps(result, indent=1, default=str))


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
sub = parser.add_subparsers(dest="command", required=True)
p_init = sub.add_parser("init")
p_init.add_argument("--pki-dir", type=Path, required=True)
p_read = sub.add_parser("read")
p_read.add_argument("--pki-dir", type=Path, required=True)
p_read.add_argument("--session", required=True)
p_read.add_argument("--portrait", type=Path)
p_read.add_argument("--clone", action="store_true", help="answer Active Authentication with the wrong key")
p_read.add_argument("--base-url", default="http://127.0.0.1:8000")
args = parser.parse_args()
init(args.pki_dir) if args.command == "init" else read(args)
