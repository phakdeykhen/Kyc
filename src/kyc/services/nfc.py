"""ePassport chip step (Phase 11): Active Authentication challenge and server-side chip verification.

The mobile app reads the chip with MRZ-derived access keys (PACE or BAC) and uploads the
raw EF.SOD and data groups. The server re-verifies everything; nothing the app concludes
is trusted. Raw chip data is not retained: only verification evidence, and (for a
verified chip) an encrypted face template of the chip portrait.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.biometrics import compare_embeddings, serialize_embedding
from kyc.biometrics.types import FaceEngineUnavailable, InvalidFaceEmbedding
from kyc.core.crypto import FieldCipher
from kyc.db.models import (AuditLog, BiometricTemplate, DocumentField, FaceComparison, IdentityDocument, KYCSession,
                           NFCChallenge, NFCResult, Organization)
from kyc.domain.enums import CheckResult, NFCStatus, SessionStatus, VerificationLevel
from kyc.domain.state_machine import Event, TERMINAL_STATUSES
from kyc.mrz import parser as mrz_parser
from kyc.nfc.trust import CSCATrustStore
from kyc.nfc.verify import verify_chip
from kyc.services.biometrics import _template_context
from kyc.services.liveness import _selfie_reference
from kyc.services.sessions import apply_event, aware

CLIENT_STATUSES = {"NOT_SUPPORTED": NFCStatus.NFC_NOT_SUPPORTED, "NOT_AVAILABLE": NFCStatus.NFC_NOT_AVAILABLE,
                   "FAILED": NFCStatus.NFC_FAILED}


@dataclass(frozen=True)
class NFCLimits:
    challenge_ttl_seconds: int
    max_attempts: int


def _error(code: int, reason: str, detail: str, remaining: int | None = None) -> JSONResponse:
    return JSONResponse(status_code=code, content={"detail": detail, "reason_code": reason, "attempts_remaining": remaining})


def _audit(db, record, tenant, request_id, action, reasons=(), **metadata):
    db.add(AuditLog(organization_id=record.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                    action=action, request_id=request_id, from_status=record.status.value, to_status=record.status.value,
                    reason_codes=list(reasons), event_metadata={"version": record.version, **metadata}))


def _session(db: Session, tenant: TenantContext, session_id: UUID, request_id: UUID):
    record = db.scalar(sa.select(KYCSession).where(KYCSession.id == session_id,
                       KYCSession.organization_id == tenant.organization_id).with_for_update())
    if record is None:
        raise HTTPException(404, detail="Session not found.")
    if record.status not in TERMINAL_STATUSES and aware(record.expires_at) <= datetime.now(timezone.utc):
        apply_event(db, record, tenant, Event.EXPIRE, request_id)
        return record, _error(409, "SESSION_EXPIRED", "The session has expired. Create a new session.")
    if record.verification_level != VerificationLevel.DOCUMENT_FACE_LIVENESS_NFC:
        return record, _error(409, "NFC_NOT_REQUIRED", "This verification level has no chip step.")
    if record.status != SessionStatus.NFC_REQUIRED:
        return record, _error(409, "NFC_NOT_OPEN", f"The chip step is not open while the session is {record.status.value}.")
    return record, None


def _attempts(db, record) -> int:
    return db.scalar(sa.select(sa.func.count()).select_from(NFCResult).where(
        NFCResult.organization_id == record.organization_id, NFCResult.session_id == record.id)) or 0


def issue_nfc_challenge(db: Session, tenant: TenantContext, session_id: UUID, limits: NFCLimits,
                        request_id: UUID) -> dict | JSONResponse:
    record, problem = _session(db, tenant, session_id, request_id)
    if problem:
        return problem
    attempts = _attempts(db, record)
    if attempts >= limits.max_attempts:
        return _error(429, "NFC_ATTEMPTS_EXCEEDED", "Too many chip attempts. Create a new session.", 0)
    now = datetime.now(timezone.utc)
    db.execute(sa.update(NFCChallenge).where(NFCChallenge.organization_id == record.organization_id,
                                             NFCChallenge.session_id == record.id, NFCChallenge.used_at.is_(None)).values(used_at=now))
    item = NFCChallenge(id=uuid4(), organization_id=record.organization_id, session_id=record.id,
                        challenge=os.urandom(8), created_at=now,
                        expires_at=now + timedelta(seconds=limits.challenge_ttl_seconds))
    db.add(item)
    _audit(db, record, tenant, request_id, "NFC_CHALLENGE_ISSUED")
    db.flush()
    # The app sends these 8 bytes to the chip with INTERNAL AUTHENTICATE and returns the chip's signature.
    return {"session_id": record.id, "challenge_id": item.id, "challenge": item.challenge.hex(), "expires_at": item.expires_at,
            "attempts_remaining": limits.max_attempts - attempts}


def _document_view(db, record, document, cipher: FieldCipher | None) -> dict:
    """Decrypted visual/MRZ values from the photographed document, for comparison only."""
    values: dict[str, str] = {}
    if cipher is None:
        return values
    for field in db.scalars(sa.select(DocumentField).where(DocumentField.organization_id == record.organization_id,
                                                           DocumentField.document_id == document.id)):
        if field.normalized_value_ciphertext is not None and field.field_name in (
                "document_number", "date_of_birth", "expiry_date", "sex", "full_name", "mrz"):
            context = f"field/{record.organization_id}/{record.id}/{document.id}/{field.field_name}/normalized"
            values[field.field_name] = cipher.open(field.normalized_value_ciphertext, field.key_version, context)
    return values


def _consistency(chip_mrz, document_values: dict) -> dict:
    if chip_mrz is None:
        return {}
    visual = {name: document_values.get(name) for name in ("document_number", "date_of_birth", "expiry_date", "sex", "full_name")}
    outcome = mrz_parser.compare(chip_mrz, visual)
    printed_mrz = mrz_parser.read(document_values["mrz"].splitlines()) if document_values.get("mrz") else None
    if printed_mrz is not None and printed_mrz.format == chip_mrz.format:
        # Only the check-digit-protected data lines are compared as a block: an OCR misread of the
        # unprotected name line is not a different passport (names are compared via the printed name).
        data = slice(0, 2) if chip_mrz.format == "TD1" else slice(1, 2)
        outcome["mrz_data_lines"] = "MATCH" if printed_mrz.lines[data] == chip_mrz.lines[data] else "MISMATCH"
    return {name: state for name, state in outcome.items() if state in ("MATCH", "MISMATCH")}


def _chip_face_match(db, record, document, verification, face_engine, biometric_cipher, match_policy, now):
    if verification.portrait is None or face_engine is None or biometric_cipher is None:
        return None, ["CHIP_PORTRAIT_NOT_COMPARED"]
    if face_engine.unavailable_reason():
        return None, ["FACE_MODELS_UNAVAILABLE"]
    try:
        with Image.open(BytesIO(verification.portrait.data)) as opened:
            image = opened.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        return None, ["CHIP_PORTRAIT_UNDECODABLE"]
    selfie = db.scalar(sa.select(BiometricTemplate).where(
        BiometricTemplate.organization_id == record.organization_id, BiometricTemplate.session_id == record.id,
        BiometricTemplate.source == "LIVE_SELFIE").order_by(BiometricTemplate.created_at.desc()).limit(1))
    reference = _selfie_reference(db, record, biometric_cipher)
    if selfie is None or reference is None:
        return None, ["SELFIE_TEMPLATE_UNAVAILABLE"]
    try:
        assessment = face_engine.assess(image, source="DOCUMENT_PORTRAIT")
        if assessment.detection is None:
            return None, ["CHIP_PORTRAIT_NO_FACE"]
        chip_embedding = face_engine.embed(image, assessment.detection)
        comparison = compare_embeddings(chip_embedding, reference, match_policy)
    except (FaceEngineUnavailable, InvalidFaceEmbedding):
        return None, ["CHIP_PORTRAIT_NOT_COMPARED"]
    organization = db.get(Organization, record.organization_id)
    template_id = uuid4()
    sealed, key = biometric_cipher.seal(serialize_embedding(chip_embedding),
                                        _template_context(record, template_id, "CHIP_PORTRAIT", chip_embedding))
    db.add(BiometricTemplate(id=template_id, organization_id=record.organization_id, session_id=record.id,
                             source="CHIP_PORTRAIT", model_name=chip_embedding.model_name,
                             model_version=chip_embedding.model_version, model_sha256=chip_embedding.model_sha256,
                             embedding_dimension=len(chip_embedding.vector), document_id=document.id,
                             template_ciphertext=sealed, key_version=key,
                             delete_after=min(now + timedelta(hours=organization.template_retention_hours),
                                              aware(document.delete_after))))
    db.flush()
    db.add(FaceComparison(organization_id=record.organization_id, session_id=record.id,
                          reference_template_id=template_id, live_template_id=selfie.id,
                          model_name=comparison.model_name, model_version=comparison.model_version,
                          model_sha256=comparison.model_sha256, threshold_policy_version=comparison.threshold_policy_version,
                          comparison_score=comparison.score, comparison_metric="COSINE_SIMILARITY", result=comparison.decision,
                          evidence_metadata={"reason_codes": list(comparison.reason_codes), "reference_source": "CHIP_PORTRAIT",
                                             "calibrated": comparison.calibrated}))
    summary = {"score": comparison.score, "result": CheckResult(comparison.decision).value,
               "policy_version": comparison.threshold_policy_version, "calibrated": comparison.calibrated}
    return summary, list(comparison.reason_codes)


def submit_nfc(db: Session, tenant: TenantContext, session_id: UUID, read_status: str, access_protocol: str | None,
               groups: dict[int, bytes], sod: bytes | None, challenge_id: UUID | None, aa_signature: bytes | None,
               trust: CSCATrustStore, field_cipher, face_engine, biometric_cipher, match_policy, limits: NFCLimits,
               request_id: UUID) -> dict | JSONResponse:
    record, problem = _session(db, tenant, session_id, request_id)
    if problem:
        return problem
    attempts = _attempts(db, record)
    if attempts >= limits.max_attempts:
        return _error(429, "NFC_ATTEMPTS_EXCEEDED", "Too many chip attempts. Create a new session.", 0)
    remaining = limits.max_attempts - attempts - 1
    document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.organization_id == record.organization_id,
                                                           IdentityDocument.session_id == record.id,
                                                           IdentityDocument.processed_at.is_not(None)))
    if document is None:
        return _error(409, "DOCUMENT_NOT_PROCESSED", "The document has not been processed.", remaining)
    now = datetime.now(timezone.utc)

    if read_status != "READ":
        status = CLIENT_STATUSES[read_status]
        retryable = status != NFCStatus.NFC_NOT_SUPPORTED and remaining > 0
        evidence = {"client_reported": True, "retryable": retryable, "reason_codes": [f"CLIENT_REPORTED_{read_status}"],
                    "access_protocol": access_protocol, "raw_chip_data_retained": False}
        db.add(NFCResult(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                         status=status, evidence_metadata=evidence, data_group_checks={}))
        _audit(db, record, tenant, request_id, "NFC_RETRY_REQUIRED" if retryable else "NFC_RECORDED",
               evidence["reason_codes"], nfc_status=status.value)
        if not retryable:
            apply_event(db, record, tenant, Event.NFC_ACCEPTED, request_id)
        db.flush()
        return {"session_id": record.id, "status": record.status, "nfc_status": status.value,
                "reason_codes": evidence["reason_codes"], "retry_allowed": retryable, "attempts_remaining": remaining}

    challenge = None
    if challenge_id is not None:
        item = db.scalar(sa.select(NFCChallenge).where(NFCChallenge.id == challenge_id,
            NFCChallenge.organization_id == record.organization_id, NFCChallenge.session_id == record.id).with_for_update())
        if item is None or item.used_at is not None or aware(item.expires_at) <= now:
            reason = "CHALLENGE_INVALID" if item is None else "CHALLENGE_ALREADY_USED" if item.used_at else "CHALLENGE_EXPIRED"
            if item is not None:
                item.used_at = item.used_at or now
            return _error(409, reason, "Request a new chip challenge.", remaining + 1)
        item.used_at = now
        challenge = item.challenge
    verification = verify_chip(groups, sod, trust, challenge, aa_signature if challenge else None, now)
    reasons = list(verification.reasons)
    if aa_signature and challenge is None:
        reasons.append("ACTIVE_AUTHENTICATION_WITHOUT_SERVER_CHALLENGE")  # a replayable answer proves nothing

    consistency = _consistency(verification.mrz, _document_view(db, record, document, field_cipher))
    mismatches = sorted(name for name, state in consistency.items() if state == "MISMATCH")
    if mismatches:
        reasons.append("CHIP_DOCUMENT_MISMATCH")
    face, face_reasons = (None, [])
    if verification.status == NFCStatus.NFC_VERIFIED:
        face, face_reasons = _chip_face_match(db, record, document, verification, face_engine, biometric_cipher,
                                              match_policy, now)
    passive = verification.passive
    evidence = {
        "client_reported": False, "retryable": False, "reason_codes": reasons, "access_protocol": access_protocol,
        "passive_authentication_steps": None if passive is None else {
            "sod_parsed": passive.sod_parsed, "digest_algorithm": passive.digest_algorithm,
            "message_digest_valid": passive.message_digest_valid, "signature_valid": passive.signature_valid,
            "dsc_trusted": passive.dsc_trusted, "dsc_within_validity": passive.dsc_within_validity,
            "dsc_issuer_country": passive.dsc_issuer_country, "listed_data_groups": passive.listed_data_groups},
        "data_groups_read": verification.data_groups_read, "document_consistency": consistency,
        "chip_face_match": face, "chip_face_reasons": face_reasons,
        "chip_authentication": "NOT_VERIFIED_SERVER_SIDE", "raw_chip_data_retained": False,
    }
    db.add(NFCResult(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                     status=verification.status, passive_authentication=None if passive is None else passive.passed,
                     chip_authentication=None, active_authentication=verification.active_authentication,
                     trust_store_version=trust.version, data_group_checks={} if passive is None else passive.data_group_hashes,
                     evidence_metadata=evidence))
    _audit(db, record, tenant, request_id, "NFC_RECORDED", reasons, nfc_status=verification.status.value,
           trust_store_version=trust.version)
    apply_event(db, record, tenant, Event.NFC_ACCEPTED, request_id)
    db.flush()
    return {"session_id": record.id, "status": record.status, "nfc_status": verification.status.value,
            "passive_authentication": None if passive is None else passive.passed,
            "active_authentication": verification.active_authentication,
            "data_group_hashes": {} if passive is None else passive.data_group_hashes,
            "document_consistency": consistency, "chip_face_match": face, "reason_codes": reasons,
            "trust_store_version": trust.version, "retry_allowed": False, "attempts_remaining": remaining}
