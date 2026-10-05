"""Manual review (Phase 14, spec §20): queue, case view, images, and reviewer decisions.

Reviewers see only what their role allows; the API strips everything else before it
leaves the server. Every case view, image view and decision is audited. A decision
needs a fixed reason code and a free-text note (encrypted), and the case version the
reviewer looked at, so two reviewers can never decide on different evidence.
Approval is guarded: a human can resolve uncertainty, but cannot approve missing
evidence, a failed required check or cryptographic tamper proof.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import TenantContext
from kyc.db.models import (AuditLog, BarcodeResult, DocumentField, DocumentImage, FaceComparison, FraudSignal,
                           IdentityDocument, KYCSession, LivenessCheck, ManualReview, MRZResult, NFCResult,
                           Reviewer, RiskAssessmentRecord, SelfieCapture)
from kyc.domain.enums import ReviewAction, SessionStatus, VerificationLevel as L
from kyc.domain.state_machine import Event, VerificationEvidence
from kyc.review.access import REASONS, ReviewerContext
from kyc.services.biometrics import clear_identity_evidence
from kyc.services.results import collect_evidence, mask
from kyc.services.sessions import apply_event, aware

IDENTITY_FIELDS = ("full_name", "full_name_local", "date_of_birth", "sex", "nationality", "document_number",
                   "expiry_date", "issue_date", "place_of_birth", "address", "national_id_number", "mrz")


def _error(code: int, reason: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=code, content={"detail": detail, "reason_code": reason, **extra})


def _audit(db, reviewer: ReviewerContext, record, request_id, action, reasons=(), **metadata):
    db.add(AuditLog(organization_id=reviewer.organization_id, session_id=record.id, actor_id=reviewer.actor_id,
                    action=action, request_id=request_id, from_status=record.status.value, to_status=record.status.value,
                    reason_codes=list(reasons), event_metadata={"version": record.version, "role": reviewer.role, **metadata}))


def _latest(db, model, record, *where):
    return db.scalar(sa.select(model).where(model.organization_id == record.organization_id, model.session_id == record.id,
                                            *where).order_by(model.created_at.desc()).limit(1))


def _session(db: Session, reviewer: ReviewerContext, session_id: UUID, lock: bool = False) -> KYCSession:
    query = sa.select(KYCSession).where(KYCSession.id == session_id, KYCSession.organization_id == reviewer.organization_id)
    record = db.scalar(query.with_for_update() if lock else query)
    if record is None:
        raise HTTPException(404, detail="Case not found.")
    return record


# Queue -----------------------------------------------------------------------------
def queue(db: Session, reviewer: ReviewerContext, limit: int, offset: int) -> dict:
    reviewer.require("VIEW_CASE")
    scope = (KYCSession.organization_id == reviewer.organization_id, KYCSession.status == SessionStatus.MANUAL_REVIEW)
    total = db.scalar(sa.select(sa.func.count()).select_from(KYCSession).where(*scope)) or 0
    rows = db.scalars(sa.select(KYCSession).where(*scope).order_by(KYCSession.updated_at, KYCSession.id)
                      .limit(limit).offset(offset)).all()
    now = datetime.now(timezone.utc)
    items = []
    for record in rows:
        assessment = _latest(db, RiskAssessmentRecord, record)
        signals = db.scalars(sa.select(FraudSignal).where(FraudSignal.organization_id == record.organization_id,
                                                          FraudSignal.session_id == record.id,
                                                          FraudSignal.severity.in_(("HIGH", "MEDIUM")))).all()
        items.append({
            "session_id": record.id, "version": record.version, "verification_level": record.verification_level,
            "expected_document_type": record.expected_document_type, "country": record.country,
            "in_review_since": aware(record.updated_at),
            "waiting_minutes": int((now - aware(record.updated_at)).total_seconds() // 60),
            "expires_at": aware(record.expires_at),
            "reason_codes": assessment.reason_codes if assessment else [],
            "high_signals": sorted({item.signal for item in signals if item.severity == "HIGH"}),
            "medium_signals": sorted({item.signal for item in signals if item.severity == "MEDIUM"}),
        })
    return {"total": total, "limit": limit, "offset": offset, "items": items}


# Case view -------------------------------------------------------------------------
def case(db: Session, reviewer: ReviewerContext, session_id: UUID, field_cipher, request_id: UUID) -> dict:
    reviewer.require("VIEW_CASE")
    record = _session(db, reviewer, session_id)
    identity = "VIEW_IDENTITY" in reviewer.permissions and field_cipher is not None
    images_allowed = "VIEW_IMAGES" in reviewer.permissions
    evidence = collect_evidence(db, record)
    document = evidence.document
    now = datetime.now(timezone.utc)

    fields, document_number = [], None
    if document is not None:
        for row in db.scalars(sa.select(DocumentField).where(DocumentField.organization_id == record.organization_id,
                                                             DocumentField.document_id == document.id)
                              .order_by(DocumentField.field_name)):
            value = None
            if row.normalized_value_ciphertext is not None and field_cipher is not None:
                context = f"field/{record.organization_id}/{record.id}/{document.id}/{row.field_name}/normalized"
                value = field_cipher.open(row.normalized_value_ciphertext, row.key_version, context)
            if row.field_name == "document_number":
                document_number = value
            fields.append({"name": row.field_name, "confidence": round(row.confidence, 3), "source": row.source,
                           "side": row.side, "flags": row.flags, "value": value if identity else None})
    assessment = _latest(db, RiskAssessmentRecord, record)
    mrz = None if document is None else db.scalar(sa.select(MRZResult).where(
        MRZResult.organization_id == record.organization_id, MRZResult.document_id == document.id))
    nfc = next((row for row in db.scalars(sa.select(NFCResult).where(
        NFCResult.organization_id == record.organization_id, NFCResult.session_id == record.id)
        .order_by(NFCResult.created_at.desc())) if not row.evidence_metadata.get("retryable")), None)
    liveness = next((row for row in db.scalars(sa.select(LivenessCheck).where(
        LivenessCheck.organization_id == record.organization_id, LivenessCheck.session_id == record.id)
        .order_by(LivenessCheck.created_at.desc())) if not row.evidence_metadata.get("retryable")), None)
    comparisons = db.scalars(sa.select(FaceComparison).where(FaceComparison.organization_id == record.organization_id,
                                                             FaceComparison.session_id == record.id)
                             .order_by(FaceComparison.created_at.desc())).all()
    images = []
    if images_allowed and document is not None:
        images = [{"image_id": row.id, "kind": f"DOCUMENT_{row.side}", "media_type": row.media_type,
                   "available_until": aware(row.delete_after)}
                  for row in db.scalars(sa.select(DocumentImage).where(
                      DocumentImage.organization_id == record.organization_id, DocumentImage.document_id == document.id,
                      DocumentImage.delete_after > now).order_by(DocumentImage.side))]
        images += [{"image_id": row.id, "kind": "SELFIE", "media_type": row.media_type, "available_until": aware(row.delete_after)}
                   for row in db.scalars(sa.select(SelfieCapture).where(
                       SelfieCapture.organization_id == record.organization_id, SelfieCapture.session_id == record.id,
                       SelfieCapture.delete_after > now))]
    names = {row.id: row.display_name for row in db.scalars(sa.select(Reviewer).where(
        Reviewer.organization_id == record.organization_id))}
    history = []
    for row in db.scalars(sa.select(ManualReview).where(ManualReview.organization_id == record.organization_id,
                                                        ManualReview.session_id == record.id).order_by(ManualReview.created_at)):
        note = None
        if identity:
            note = field_cipher.open(row.reason_ciphertext, row.key_version, _note_context(record, row.id))
        history.append({"action": row.action, "reason_code": row.reason_code, "decided_at": aware(row.created_at),
                        "reviewer": names.get(_uuid(row.reviewer_id), "former reviewer"), "note": note})
    signals = [{"signal": row.signal, "severity": row.severity, "category": row.category,
                "detector": row.evidence_metadata.get("detector"), "fields": row.evidence_metadata.get("fields", []),
                "sources": row.evidence_metadata.get("sources", []), "details": row.evidence_metadata.get("details", {})}
               for row in db.scalars(sa.select(FraudSignal).where(FraudSignal.organization_id == record.organization_id,
                                                                  FraudSignal.session_id == record.id))]
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    signals.sort(key=lambda item: (order[item["severity"]], item["signal"]))
    _audit(db, reviewer, record, request_id, "REVIEW_CASE_VIEWED", identity_shown=identity, images_listed=len(images))
    return {
        "session": {"session_id": record.id, "status": record.status, "version": record.version,
                    "verification_level": record.verification_level, "country": record.country,
                    "expected_document_type": record.expected_document_type, "user_id": record.user_id,
                    "created_at": aware(record.created_at), "updated_at": aware(record.updated_at),
                    "expires_at": aware(record.expires_at)},
        "permissions": sorted(reviewer.permissions),
        "decision_options": {action: list(codes) for action, codes in REASONS.items()} if "DECIDE" in reviewer.permissions else {},
        "risk": None if assessment is None else {
            "decision": assessment.decision, "reason_codes": assessment.reason_codes, "policy_version": assessment.policy_version,
            "assessed_at": aware(assessment.created_at), "trace": assessment.check_summary.get("trace", []),
            "authenticity_sources": assessment.check_summary.get("authenticity_sources", [])},
        "checks": evidence.checks,
        "fraud_signals": signals,
        "document": None if document is None else {
            "type": document.document_type, "issuing_country": document.issuing_country,
            "classification_confidence": document.classification_confidence,
            "document_number": document_number if identity else mask(document_number)},
        "fields": fields,
        "mrz": None if mrz is None else {"format": mrz.format, "valid": mrz.mrz_valid,
                                         "check_digits": {name: item.get("valid") for name, item in mrz.check_digit_results.items()},
                                         "field_consistency": mrz.field_consistency},
        "barcodes": [] if document is None else [
            {"symbology": row.symbology, "format_valid": row.format_valid, "signature_present": row.signature_present,
             "signature_valid": row.signature_valid, "fields": (row.data_consistency or {}).get("fields", {})}
            for row in db.scalars(sa.select(BarcodeResult).where(BarcodeResult.organization_id == record.organization_id,
                                                                 BarcodeResult.document_id == document.id))],
        "nfc": None if nfc is None else {
            "status": nfc.status, "passive_authentication": nfc.passive_authentication,
            "active_authentication": nfc.active_authentication, "trust_store_version": nfc.trust_store_version,
            "data_group_checks": nfc.data_group_checks, "reason_codes": nfc.evidence_metadata.get("reason_codes", []),
            "document_consistency": nfc.evidence_metadata.get("document_consistency", {}),
            "chip_face_match": nfc.evidence_metadata.get("chip_face_match")},
        "face_comparisons": [{"reference": row.evidence_metadata.get("reference_source", "DOCUMENT_PORTRAIT"),
                              "score": round(row.comparison_score, 4), "result": row.result,
                              "policy_version": row.threshold_policy_version,
                              "calibrated": bool(row.evidence_metadata.get("calibrated"))} for row in comparisons],
        "liveness": None if liveness is None else {
            "result": liveness.result, "score": liveness.score, "attack_type": liveness.attack_type,
            "reason_codes": liveness.evidence_metadata.get("reason_codes", []),
            "calibrated": liveness.evidence_metadata.get("calibrated"),
            "coverage": liveness.evidence_metadata.get("coverage", {})},
        "images": images,
        "history": history,
    }


def _uuid(value: str):
    try:
        return UUID(str(value))
    except ValueError:
        return None


def _note_context(record: KYCSession, review_id: UUID) -> str:
    return f"review/{record.organization_id}/{record.id}/{review_id}/note"


# Images ----------------------------------------------------------------------------
def image(db: Session, reviewer: ReviewerContext, session_id: UUID, image_id: UUID, store, request_id: UUID) -> Response:
    reviewer.require("VIEW_IMAGES")
    record = _session(db, reviewer, session_id)
    now = datetime.now(timezone.utc)
    found = db.scalar(sa.select(DocumentImage).where(DocumentImage.id == image_id,
                      DocumentImage.organization_id == record.organization_id, DocumentImage.session_id == record.id))
    kind = f"DOCUMENT_{found.side}" if found else None
    if found is None:
        found = db.scalar(sa.select(SelfieCapture).where(SelfieCapture.id == image_id,
                          SelfieCapture.organization_id == record.organization_id, SelfieCapture.session_id == record.id))
        kind = "SELFIE"
    if found is None or aware(found.delete_after) <= now or store is None:
        raise HTTPException(404, detail="Image not available (never stored, or removed by retention).")
    data = store.get(found.encrypted_object_ref, record.organization_id, record.id, found.id)
    _audit(db, reviewer, record, request_id, "REVIEW_IMAGE_VIEWED", image_kind=kind)
    return Response(content=data, media_type=found.media_type, headers={
        "Cache-Control": "no-store", "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


# Decision --------------------------------------------------------------------------
LIVENESS_LEVELS = (L.DOCUMENT_FACE_LIVENESS, L.DOCUMENT_FACE_LIVENESS_NFC)


def approval_blockers(evidence, required: tuple[str, ...]) -> list[str]:
    """What a reviewer may not approve away: absent or unavailable required evidence, a failed
    required check, and cryptographic tamper proof. These need recapture or rejection."""
    blockers = []
    for name in required:
        value = evidence.checks.get(name)
        if value is None:
            blockers.append(f"EVIDENCE_MISSING_{name.upper()}")
        elif value in ("UNAVAILABLE", "FAIL"):
            blockers.append(f"{name.upper()}_{value}")
    blockers += [f"TAMPER_{item.signal}" for item in evidence.signals if item.category == "TAMPER" and item.severity == "HIGH"]
    return blockers


def decide(db: Session, reviewer: ReviewerContext, session_id: UUID, action: str, reason_code: str, note: str,
           expected_version: int, field_cipher, policy, request_id: UUID):
    reviewer.require("DECIDE")
    if reason_code not in REASONS[action]:
        return _error(422, "REASON_CODE_NOT_ALLOWED", f"Use one of: {', '.join(REASONS[action])}.")
    if field_cipher is None:
        return _error(503, "PII_ENCRYPTION_NOT_CONFIGURED", "Review notes cannot be stored securely.")
    record = _session(db, reviewer, session_id, lock=True)
    if record.status != SessionStatus.MANUAL_REVIEW:
        return _error(409, "CASE_NOT_IN_REVIEW", f"The case is {record.status.value}.", status=record.status.value)
    if record.version != expected_version:
        return _error(409, "CASE_CHANGED", "The case changed since you opened it. Reload and review again.",
                      version=record.version)
    evidence = collect_evidence(db, record)
    if action == "APPROVE":
        document = evidence.document
        required = policy.resolve(record.verification_level, (document.issuing_country if document else None) or record.country,
                                  document.document_type.value if document else None).required
        blockers = approval_blockers(evidence, required)
        if blockers:
            _audit(db, reviewer, record, request_id, "REVIEW_APPROVAL_BLOCKED", blockers)
            return _error(409, "APPROVAL_BLOCKED", "This case cannot be approved; request recapture or reject.",
                          blockers=blockers)
    assessment = _latest(db, RiskAssessmentRecord, record)
    seen_version, previous = record.version, record.status
    review = ManualReview(id=uuid4(), organization_id=record.organization_id, session_id=record.id,
                          reviewer_id=str(reviewer.reviewer_id),
                          action=ReviewAction(action), reason_code=reason_code, reason_ciphertext=b"", key_version="",
                          session_version=seen_version, risk_assessment_id=assessment.id if assessment else None)
    review.reason_ciphertext, review.key_version = field_cipher.seal(note, _note_context(record, review.id))
    db.add(review)
    tenant = TenantContext(reviewer.organization_id, actor_id=reviewer.actor_id)
    if action == "APPROVE":
        level = record.verification_level
        # The reviewer attests to what the engine could not settle; the blocker check above
        # guarantees every required check exists and none failed.
        apply_event(db, record, tenant, Event.REVIEW_APPROVED, request_id, evidence=VerificationEvidence(
            document_valid=True, face_match=level != L.DOCUMENT_ONLY, liveness_pass=level in LIVENESS_LEVELS,
            nfc_verified=level == L.DOCUMENT_FACE_LIVENESS_NFC, policy_pass=True))
    elif action == "REJECT":
        apply_event(db, record, tenant, Event.REVIEW_REJECTED, request_id)
    else:
        if evidence.document is not None:
            clear_identity_evidence(db, record, evidence.document)
        apply_event(db, record, tenant, Event.RECAPTURE_REQUIRED, request_id)
    db.add(AuditLog(organization_id=reviewer.organization_id, session_id=record.id, actor_id=reviewer.actor_id,
                    action="REVIEW_DECISION", request_id=request_id, from_status=previous.value, to_status=record.status.value,
                    reason_codes=[reason_code], event_metadata={"version": record.version, "action": action,
                                                                "role": reviewer.role, "case_version_seen": seen_version}))
    db.flush()
    return {"session_id": record.id, "status": record.status, "version": record.version, "action": action,
            "reason_code": reason_code}

