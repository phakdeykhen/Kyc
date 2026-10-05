"""EF.SOD Passive Authentication (ICAO Doc 9303 Part 11 §5.1, Part 10 §4.6.2).

Steps, each recorded separately:
  1. EF.SOD (tag 77) wraps a CMS SignedData whose content is an LDSSecurityObject.
  2. The signed attributes' messageDigest equals the hash of that LDSSecurityObject.
  3. The Document Signer (DSC) signature over the signed attributes verifies.
  4. The DSC was issued by a CSCA in the trust store and is within its validity period.
  5. Every data group read from the chip hashes to the value the SOD lists for it.
A chip passes only if all five hold. Readable data alone proves nothing.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib

from asn1crypto import algos, cms, core
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from kyc.nfc.lds import LDSError, read_tlv
from kyc.nfc.trust import CSCATrustStore

LDS_SECURITY_OBJECT_OID = "2.23.136.1.1.1"
HASHES = {"sha1": hashes.SHA1, "sha224": hashes.SHA224, "sha256": hashes.SHA256, "sha384": hashes.SHA384,
          "sha512": hashes.SHA512}


class DataGroupHash(core.Sequence):
    _fields = [("data_group_number", core.Integer), ("data_group_hash_value", core.OctetString)]


class DataGroupHashValues(core.SequenceOf):
    _child_spec = DataGroupHash


class LDSVersionInfo(core.Sequence):
    _fields = [("lds_version", core.PrintableString), ("unicode_version", core.PrintableString)]


class LDSSecurityObject(core.Sequence):
    _fields = [("version", core.Integer), ("hash_algorithm", algos.DigestAlgorithm),
               ("data_group_hash_values", DataGroupHashValues), ("lds_version_info", LDSVersionInfo, {"optional": True})]


@dataclass
class PassiveAuthentication:
    sod_parsed: bool = False
    digest_algorithm: str | None = None
    message_digest_valid: bool | None = None
    signature_valid: bool | None = None
    dsc_trusted: bool | None = None
    dsc_within_validity: bool | None = None
    data_group_hashes: dict[str, str] = field(default_factory=dict)   # "DG1" → MATCH | MISMATCH | NOT_IN_SOD
    listed_data_groups: list[int] = field(default_factory=list)
    dsc_issuer_country: str | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def hashes_valid(self) -> bool:
        return bool(self.data_group_hashes) and all(state == "MATCH" for state in self.data_group_hashes.values())

    @property
    def passed(self) -> bool:
        return bool(self.sod_parsed and self.message_digest_valid and self.signature_valid and self.dsc_trusted
                    and self.dsc_within_validity and self.hashes_valid)

    @property
    def tampered(self) -> bool:
        """Definitive evidence of altered or forged chip data (as opposed to unverifiable data)."""
        return (self.message_digest_valid is False or self.signature_valid is False
                or any(state == "MISMATCH" for state in self.data_group_hashes.values()))


def _verify_signature(public_key, signature: bytes, message: bytes, algorithm: str, hash_name: str,
                      pss_params=None) -> None:
    digest = HASHES[hash_name]()
    if isinstance(public_key, rsa.RSAPublicKey):
        if algorithm == "rsassa_pss":
            salt = pss_params["salt_length"].native if pss_params is not None else digest.digest_size
            public_key.verify(signature, message, padding.PSS(padding.MGF1(digest), salt), digest)
        else:
            public_key.verify(signature, message, padding.PKCS1v15(), digest)
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        public_key.verify(signature, message, ec.ECDSA(digest))
    else:
        raise InvalidSignature("Unsupported Document Signer key type")


def verify_sod(sod: bytes, data_groups: dict[int, bytes], trust: CSCATrustStore,
               now: datetime | None = None) -> PassiveAuthentication:
    now = now or datetime.now(timezone.utc)
    result = PassiveAuthentication()
    try:
        tag, wrapped, _ = read_tlv(sod)
        content = cms.ContentInfo.load(wrapped if tag == 0x77 else sod)
        signed = content["content"]
        encap = signed["encap_content_info"]
        if encap["content_type"].dotted != LDS_SECURITY_OBJECT_OID:
            raise ValueError("SOD does not contain an LDSSecurityObject")
        lds_bytes = encap["content"].contents
        lds = LDSSecurityObject.load(lds_bytes)
        lds.native  # force full parse
        signer = signed["signer_infos"][0]
        certificates = [cert.chosen for cert in signed["certificates"]] if signed["certificates"] else []
    except (LDSError, ValueError, KeyError, IndexError, TypeError) as error:
        result.reasons.append("SOD_UNPARSEABLE")
        return result
    result.sod_parsed = True

    # 5. Data-group hashes (computed first: they matter even when the chain cannot be verified).
    lds_hash = lds["hash_algorithm"]["algorithm"].native
    result.digest_algorithm = lds_hash
    listed = {item["data_group_number"].native: item["data_group_hash_value"].native for item in lds["data_group_hash_values"]}
    result.listed_data_groups = sorted(listed)
    for number, data in sorted(data_groups.items()):
        if number not in listed:
            result.data_group_hashes[f"DG{number}"] = "NOT_IN_SOD"
        else:
            actual = hashlib.new(lds_hash, data).digest()
            result.data_group_hashes[f"DG{number}"] = "MATCH" if actual == listed[number] else "MISMATCH"
    if any(state == "MISMATCH" for state in result.data_group_hashes.values()):
        result.reasons.append("DATA_GROUP_HASH_MISMATCH")
    if any(state == "NOT_IN_SOD" for state in result.data_group_hashes.values()):
        result.reasons.append("DATA_GROUP_NOT_IN_SOD")

    # 2. messageDigest attribute.
    digest_name = signer["digest_algorithm"]["algorithm"].native
    attributes = signer["signed_attrs"]
    message_digest = next((attr["values"][0].native for attr in attributes if attr["type"].native == "message_digest"), None)
    result.message_digest_valid = message_digest == hashlib.new(digest_name, lds_bytes).digest()
    if not result.message_digest_valid:
        result.reasons.append("SOD_MESSAGE_DIGEST_MISMATCH")

    # 3. DSC signature over the DER SET of signed attributes.
    sid = signer["sid"]
    dsc_der = next((cert.dump() for cert in certificates
                    if sid.name == "issuer_and_serial_number"
                    and cert.issuer == sid.chosen["issuer"] and cert.serial_number == sid.chosen["serial_number"].native),
                   certificates[0].dump() if certificates else None)
    if dsc_der is None:
        result.reasons.append("DOCUMENT_SIGNER_CERTIFICATE_MISSING")
        return result
    dsc = x509.load_der_x509_certificate(dsc_der)
    try:
        country = dsc.issuer.get_attributes_for_oid(x509.NameOID.COUNTRY_NAME)
        result.dsc_issuer_country = country[0].value if country else None
    except ValueError:
        pass
    signed_attrs = b"\x31" + attributes.dump()[1:]
    algorithm = signer["signature_algorithm"]
    try:
        _verify_signature(dsc.public_key(), signer["signature"].native, signed_attrs, algorithm.signature_algo,
                          digest_name, algorithm["parameters"] if algorithm.signature_algo == "rsassa_pss" else None)
        result.signature_valid = True
    except (InvalidSignature, ValueError, KeyError):
        result.signature_valid = False
        result.reasons.append("SOD_SIGNATURE_INVALID")

    # 4. DSC → CSCA chain and validity.
    result.dsc_within_validity = dsc.not_valid_before_utc <= now <= dsc.not_valid_after_utc
    if not result.dsc_within_validity:
        result.reasons.append("DOCUMENT_SIGNER_OUTSIDE_VALIDITY")
    if not trust.certificates:
        result.dsc_trusted = None
        result.reasons.append("CSCA_TRUST_STORE_NOT_CONFIGURED")
    else:
        result.dsc_trusted = False
        for anchor in trust.issuers_of(dsc):
            try:
                dsc.verify_directly_issued_by(anchor)
            except (InvalidSignature, ValueError, TypeError):
                continue
            result.dsc_trusted = anchor.not_valid_before_utc <= dsc.not_valid_before_utc <= anchor.not_valid_after_utc
            if result.dsc_trusted:
                break
        if not result.dsc_trusted:
            result.reasons.append("DOCUMENT_SIGNER_NOT_TRUSTED")
    return result
