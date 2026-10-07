"""Expose a scanned official QR link without claiming a government verification result.

QR payloads remain encrypted at rest. The private record link is subject to identity
permissions, and no URL from a scanned document is fetched by the application server.
"""

import base64
import binascii
from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from cryptography.exceptions import InvalidTag
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.schemas import ResultGovernmentVerification
from kyc.core.crypto import FieldCipher
from kyc.db.models import BarcodeResult, IdentityDocument, KYCSession


def official_verification_url(text: str) -> str | None:
    """Recognize the Verify.gov.kh record URL format observed on the supplied NSSF card.

    Reject redirects, lookalike hosts, credentials, unexpected ports, fragments and
    malformed keys. Recognizing this URL never authenticates the linked record.
    """
    text = text.strip()
    if not text or len(text) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in text) or "\\" in text:
        return None
    try:
        url = urlsplit(text)
        if url.scheme != "https" or url.netloc.lower() not in {"verify.gov.kh", "verify.gov.kh:443"} or url.fragment:
            return None
        if not re.fullmatch(r"/verify/[A-Za-z0-9_-]{1,128}", url.path):
            return None
        query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    if set(query) - {"key"}:
        return None
    key = query.get("key")
    if key is not None and (len(key) != 1 or not re.fullmatch(r"[0-9A-Fa-f]{64}", key[0])):
        return None
    return urlunsplit(("https", "verify.gov.kh", url.path, f"key={key[0]}" if key else "", ""))


def build_government_verification(db: Session, record: KYCSession, cipher: FieldCipher | None,
                                  reveal_link: bool = False) -> ResultGovernmentVerification:
    if record.erased_at is not None:
        return ResultGovernmentVerification(status="ERASED")
    document = db.scalar(sa.select(IdentityDocument).where(
        IdentityDocument.organization_id == record.organization_id,
        IdentityDocument.session_id == record.id, IdentityDocument.processed_at.is_not(None))
        .order_by(IdentityDocument.processed_at.desc()).limit(1))
    if document is None:
        return ResultGovernmentVerification(status="PENDING")
    deadline = document.delete_after.replace(tzinfo=document.delete_after.tzinfo or timezone.utc)
    if deadline <= datetime.now(timezone.utc):
        return ResultGovernmentVerification(status="LINK_EXPIRED")
    rows = db.scalars(sa.select(BarcodeResult).where(
        BarcodeResult.organization_id == record.organization_id,
        BarcodeResult.session_id == record.id, BarcodeResult.document_id == document.id,
        BarcodeResult.decoded.is_(True)).order_by(BarcodeResult.created_at, BarcodeResult.id))
    unreadable = False
    for row in rows:
        if cipher is None or row.payload_ciphertext is None or row.key_version is None:
            unreadable = True
            continue
        side = (row.data_consistency or {}).get("side")
        if side not in {"FRONT", "BACK", "DATA_PAGE"}:
            unreadable = True
            continue
        context = f"barcode/{record.organization_id}/{record.id}/{document.id}/{side}/{row.symbology}"
        try:
            encoded = cipher.open(row.payload_ciphertext, row.key_version, context)
            text = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (InvalidTag, KeyError, ValueError, UnicodeDecodeError, binascii.Error):
            unreadable = True
            continue
        link = official_verification_url(text)
        if link is not None:
            return ResultGovernmentVerification(status="LINK_AVAILABLE" if reveal_link else "LINK_RESTRICTED",
                                                verification_url=link if reveal_link else None)
    return ResultGovernmentVerification(status="UNAVAILABLE" if unreadable else "NO_OFFICIAL_QR")
