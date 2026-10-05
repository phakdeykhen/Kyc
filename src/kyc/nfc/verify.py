"""Combines Passive and Active Authentication into the spec's NFC statuses (§11).

  NFC_NOT_SUPPORTED  no chip on the document, or the device has no NFC (client report)
  NFC_NOT_AVAILABLE  NFC present but not usable now, e.g. switched off (client report)
  NFC_FAILED         chip unreadable, or definitive evidence of altered/cloned data
  NFC_READ           chip read, but authenticity could not be established (e.g. CSCA not trusted)
  NFC_VERIFIED       Passive Authentication passed and Active Authentication did not fail

NFC_VERIFIED means the data was signed by a trusted issuer and is unaltered. It never
means the passport, or its holder, is genuine: that is the risk engine's call.
"""

from dataclasses import dataclass, field
from datetime import datetime

from kyc.domain.enums import NFCStatus
from kyc.mrz import parser as mrz_parser
from kyc.nfc import active_auth
from kyc.nfc.lds import LDSError, Portrait, dg1_mrz, dg2_portrait, dg15_public_key
from kyc.nfc.sod import PassiveAuthentication, verify_sod
from kyc.nfc.trust import CSCATrustStore


@dataclass
class ChipVerification:
    status: NFCStatus
    reasons: list[str]
    passive: PassiveAuthentication | None = None
    active_authentication: bool | None = None
    mrz: "mrz_parser.MRZResult | None" = None
    portrait: Portrait | None = None
    data_groups_read: list[int] = field(default_factory=list)


def verify_chip(groups: dict[int, bytes], sod: bytes | None, trust: CSCATrustStore,
                challenge: bytes | None = None, aa_signature: bytes | None = None,
                now: datetime | None = None) -> ChipVerification:
    if not sod or 1 not in groups:
        return ChipVerification(NFCStatus.NFC_FAILED, ["CHIP_DATA_INCOMPLETE"], data_groups_read=sorted(groups))
    passive = verify_sod(sod, groups, trust, now)
    reasons = list(passive.reasons)
    result = ChipVerification(NFCStatus.NFC_READ, reasons, passive, data_groups_read=sorted(groups))
    try:
        result.mrz = mrz_parser.read(dg1_mrz(groups[1]))
    except LDSError:
        reasons.append("DG1_UNREADABLE")
    if result.mrz is None and "DG1_UNREADABLE" not in reasons:
        reasons.append("DG1_MRZ_UNRECOGNIZED")
    if 2 in groups:
        try:
            result.portrait = dg2_portrait(groups[2])
        except LDSError:
            reasons.append("DG2_PORTRAIT_UNREADABLE")
    if 15 in groups:
        try:
            key = dg15_public_key(groups[15])
        except LDSError:
            reasons.append("DG15_UNREADABLE")
            key = None
        if key is not None and challenge and aa_signature:
            result.active_authentication = active_auth.verify(key, challenge, aa_signature)
            if not result.active_authentication:
                reasons.append("ACTIVE_AUTHENTICATION_FAILED")
        elif key is not None:
            reasons.append("ACTIVE_AUTHENTICATION_NOT_PERFORMED")
    else:
        reasons.append("ACTIVE_AUTHENTICATION_NOT_SUPPORTED_BY_CHIP")

    if not passive.sod_parsed or passive.tampered or result.active_authentication is False or "DG1_UNREADABLE" in reasons:
        result.status = NFCStatus.NFC_FAILED
    elif passive.passed and result.mrz is not None:
        result.status = NFCStatus.NFC_VERIFIED
    return result
