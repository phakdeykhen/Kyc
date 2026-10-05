"""Synthetic ePassport chips signed by a fictional test PKI (ICAO's "Utopia"). Never real keys or documents."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import os

from asn1crypto import algos, cms, core, x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from PIL import Image

from tests.mrz_build import td3

LDS_OID = "2.23.136.1.1.1"


def tlv(tag: int, value: bytes) -> bytes:
    tag_bytes = tag.to_bytes((tag.bit_length() + 7) // 8 or 1, "big")
    length = len(value)
    if length < 0x80:
        size = bytes([length])
    else:
        raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
        size = bytes([0x80 | len(raw)]) + raw
    return tag_bytes + size + value


def _name(common_name: str, country: str = "UT") -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, country), x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


@dataclass
class TestPKI:
    csca_key: object
    csca: x509.Certificate
    dsc_key: object
    dsc: x509.Certificate

    def csca_pem(self) -> bytes:
        return self.csca.public_bytes(serialization.Encoding.PEM)


def make_pki(country="UT", dsc_valid_days=3650, dsc_signed_by=None) -> TestPKI:
    now = datetime.now(timezone.utc)
    csca_key = ec.generate_private_key(ec.SECP256R1())
    csca = (x509.CertificateBuilder().subject_name(_name(f"{country} Test CSCA", country)).issuer_name(_name(f"{country} Test CSCA", country))
            .public_key(csca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=365)).not_valid_after(now + timedelta(days=365 * 15))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(csca_key, hashes.SHA256()))
    signer_key, signer_cert = (dsc_signed_by.csca_key, dsc_signed_by.csca) if dsc_signed_by else (csca_key, csca)
    dsc_key = ec.generate_private_key(ec.SECP256R1())
    dsc = (x509.CertificateBuilder().subject_name(_name(f"{country} Test Document Signer", country)).issuer_name(signer_cert.subject)
           .public_key(dsc_key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(now - timedelta(days=30)).not_valid_after(now + timedelta(days=dsc_valid_days))
           .sign(signer_key, hashes.SHA256()))
    return TestPKI(csca_key, csca, dsc_key, dsc)


def dg1(mrz_lines) -> bytes:
    return tlv(0x61, tlv(0x5F1F, "".join(mrz_lines).encode("ascii")))


def dg2(image: Image.Image | None = None, fmt="JPEG") -> bytes:
    image = image or Image.new("RGB", (240, 320), (180, 150, 130))
    buffer = BytesIO()
    image.save(buffer, fmt)
    facial_record = b"FAC\x00" + b"010\x00" + bytes(12) + buffer.getvalue()  # ISO/IEC 19794-5 header, simplified
    biometric = tlv(0xA1, tlv(0x80, b"\x01\x01")) + tlv(0x5F2E, facial_record)
    return tlv(0x75, tlv(0x7F61, tlv(0x02, b"\x01") + tlv(0x7F60, biometric)))


def dg15(public_key) -> bytes:
    return tlv(0x6F, public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))


class LDSHash(core.Sequence):
    _fields = [("data_group_number", core.Integer), ("data_group_hash_value", core.OctetString)]


class LDSHashes(core.SequenceOf):
    _child_spec = LDSHash


class LDSObject(core.Sequence):
    _fields = [("version", core.Integer), ("hash_algorithm", algos.DigestAlgorithm), ("data_group_hash_values", LDSHashes)]


def sod(pki: TestPKI, data_groups: dict[int, bytes], hash_name="sha256", tamper_signature=False) -> bytes:
    lds = LDSObject({"version": 0, "hash_algorithm": {"algorithm": hash_name},
                     "data_group_hash_values": [{"data_group_number": number, "data_group_hash_value": hashlib.new(hash_name, data).digest()}
                                                for number, data in sorted(data_groups.items())]}).dump()
    attributes = cms.CMSAttributes([
        cms.CMSAttribute({"type": "content_type", "values": [LDS_OID]}),
        cms.CMSAttribute({"type": "message_digest", "values": [hashlib.sha256(lds).digest()]}),
    ])
    signature = pki.dsc_key.sign(b"\x31" + attributes.dump()[1:], ec.ECDSA(hashes.SHA256()))
    if tamper_signature:
        signature = signature[:-1] + bytes([signature[-1] ^ 1])
    certificate = asn1_x509.Certificate.load(pki.dsc.public_bytes(serialization.Encoding.DER))
    signed = cms.SignedData({
        "version": "v3", "digest_algorithms": [{"algorithm": "sha256"}],
        "encap_content_info": {"content_type": LDS_OID, "content": cms.ParsableOctetString(lds)},
        "certificates": [certificate],
        "signer_infos": [{
            "version": "v1",
            "sid": cms.SignerIdentifier({"issuer_and_serial_number": {"issuer": certificate.issuer, "serial_number": certificate.serial_number}}),
            "digest_algorithm": {"algorithm": "sha256"}, "signed_attrs": attributes,
            "signature_algorithm": {"algorithm": "sha256_ecdsa"}, "signature": signature,
        }],
    })
    return tlv(0x77, cms.ContentInfo({"content_type": "signed_data", "content": signed}).dump())


def aa_rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=1024)


def aa_sign(private_key, challenge: bytes) -> bytes:
    """ISO/IEC 9796-2 scheme 1 with SHA-1, as most RSA ePassports do."""
    numbers = private_key.private_numbers()
    size = (numbers.public_numbers.n.bit_length() + 7) // 8
    recovered = os.urandom(size - 22)
    message = b"\x6a" + recovered + hashlib.sha1(recovered + challenge).digest() + b"\xbc"
    return pow(int.from_bytes(message, "big"), numbers.d, numbers.public_numbers.n).to_bytes(size, "big")


@dataclass
class Chip:
    pki: TestPKI
    groups: dict[int, bytes]
    sod: bytes
    aa_key: object


def make_chip(pki=None, mrz=None, portrait=None, tamper_dg1=False, with_aa=True) -> Chip:
    pki = pki or make_pki()
    aa_key = aa_rsa_key() if with_aa else None
    groups = {1: dg1(mrz or td3(state="UTO", number="L898902C3", nationality="UTO", birth="740812", sex="F",
                                    expiry="340415", surname="ERIKSSON", given="ANNA MARIA")),
              2: dg2(portrait)}
    if aa_key:
        groups[15] = dg15(aa_key.public_key())
    security = sod(pki, groups)
    if tamper_dg1:
        groups[1] = groups[1].replace(b"ERIKSSON", b"ERIKSSOM")
    return Chip(pki, groups, security, aa_key)
