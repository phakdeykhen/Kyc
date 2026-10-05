"""Document engine orchestration (Phase 3): classify, extract, validate, persist.

Runs after every required side has passed the capture gate (DOCUMENT_PROCESSING).
Outcomes:
  ACCEPTED   fields stored encrypted, checks recorded, DOCUMENT_ACCEPTED fired
  RECAPTURE  wrong/unrecognized document or unreadable critical fields; captures
             cleared, RECAPTURE_REQUIRED fired (back to DOCUMENT_REQUIRED)
  NO_ADAPTER / UNAVAILABLE / ERROR  session stays in DOCUMENT_PROCESSING for retry
Raw OCR text and field values are PII: they never enter checks or audit logs.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from kyc.api.dependencies import TenantContext
from kyc.core.crypto import FieldCipher
from kyc.db.models import AuditLog, DocumentCheck, DocumentField, DocumentImage, IdentityDocument, KYCSession, MRZResult
from kyc.db.session import set_tenant
from kyc.documents.adapters import adapter_for
from kyc.documents.preprocess import prepare_side
from kyc.documents.refine import refine_numeric_words
from kyc.documents.requirements import requirement_for
from kyc.domain.enums import CheckResult, SessionStatus
from kyc.domain.identity import OCRLine
from kyc.domain.state_machine import Event, TERMINAL_STATUSES
from kyc.engines.capture_quality import HeuristicDocumentQualityEngine, decode_capture
from kyc.engines.contracts import CheckEvidence
from kyc.mrz.reader import read_mrz_lines
from kyc.services.sessions import apply_event, aware
from kyc.storage.captures import CaptureStore

log = logging.getLogger(__name__)
SYSTEM_ACTOR = "document-engine"


@dataclass(frozen=True)
class ProcessingOutcome:
    status: str
    reason_codes: tuple[str, ...] = ()


class DocumentProcessor:
    def __init__(self, factory: sessionmaker, store: CaptureStore | None, cipher: FieldCipher | None, ocr,
                 languages: tuple[str, ...], max_pixels: int):
        self.factory = factory
        self.store = store
        self.cipher = cipher
        self.ocr = ocr
        self.languages = languages
        self.max_pixels = max_pixels
        self.quality = HeuristicDocumentQualityEngine()

    def unavailable_reason(self) -> str | None:
        if self.store is None:
            return "CAPTURE_STORAGE_NOT_CONFIGURED"
        if self.cipher is None:
            return "PII_ENCRYPTION_NOT_CONFIGURED"
        if self.ocr is None or not self.ocr.is_available(self.languages):
            return "OCR_ENGINE_UNAVAILABLE"
        return None

    # Entry point ------------------------------------------------------------------
    def process(self, organization_id: UUID, session_id: UUID, request_id: UUID, today: date | None = None) -> ProcessingOutcome:
        tenant = TenantContext(organization_id, actor_id=SYSTEM_ACTOR)
        try:
            with self.factory() as db, db.begin():
                set_tenant(db, organization_id)
                return self._process(db, tenant, session_id, request_id, today or date.today())
        except Exception as error:  # noqa: BLE001 - recorded, session left for retry
            log.warning("Document processing failed for a session: %s", type(error).__name__)
            with self.factory() as db, db.begin():
                set_tenant(db, organization_id)
                db.add(AuditLog(organization_id=organization_id, session_id=session_id, actor_id=SYSTEM_ACTOR,
                                action="DOCUMENT_PROCESSING_FAILED", request_id=request_id,
                                reason_codes=[type(error).__name__], event_metadata={}))
            return ProcessingOutcome("ERROR", (type(error).__name__,))

    def _audit_once(self, db: Session, record: KYCSession, tenant: TenantContext, request_id: UUID, action: str) -> None:
        exists = db.scalar(sa.select(AuditLog.id).where(AuditLog.organization_id == record.organization_id,
                                                        AuditLog.session_id == record.id, AuditLog.action == action,
                                                        AuditLog.to_status == record.status.value).limit(1))
        if not exists:
            db.add(AuditLog(organization_id=tenant.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                            action=action, request_id=request_id, from_status=record.status.value,
                            to_status=record.status.value, reason_codes=[action], event_metadata={"version": record.version}))

    def _process(self, db: Session, tenant: TenantContext, session_id: UUID, request_id: UUID, today: date) -> ProcessingOutcome:
        record = db.scalar(sa.select(KYCSession).where(KYCSession.id == session_id,
                           KYCSession.organization_id == tenant.organization_id).with_for_update())
        if record is None:
            return ProcessingOutcome("NOT_FOUND")
        if record.status not in TERMINAL_STATUSES and aware(record.expires_at) <= datetime.now(timezone.utc):
            apply_event(db, record, tenant, Event.EXPIRE, request_id)
            return ProcessingOutcome("EXPIRED")
        if record.status != SessionStatus.DOCUMENT_PROCESSING:
            return ProcessingOutcome("NOT_READY")
        adapter = adapter_for(record.expected_document_type)
        if adapter is None:
            self._audit_once(db, record, tenant, request_id, "DOCUMENT_ADAPTER_UNAVAILABLE")
            return ProcessingOutcome("NO_ADAPTER", ("DOCUMENT_ADAPTER_UNAVAILABLE",))
        reason = self.unavailable_reason()
        if reason:
            self._audit_once(db, record, tenant, request_id, reason)
            return ProcessingOutcome("UNAVAILABLE", (reason,))

        document = db.scalar(sa.select(IdentityDocument).where(IdentityDocument.organization_id == record.organization_id,
                                                               IdentityDocument.session_id == record.id)
                             .order_by(IdentityDocument.created_at))
        images = {image.side: image for image in db.scalars(sa.select(DocumentImage).where(
            DocumentImage.organization_id == record.organization_id, DocumentImage.session_id == record.id))}
        aspect = requirement_for(record.expected_document_type).aspect_ratio
        sides = adapter.required_sides()
        if document is None or any(side not in images for side in sides):
            return self._recapture(db, record, tenant, document, images, request_id, ("CAPTURES_INCOMPLETE",))

        lines: dict[str, list[OCRLine]] = {}
        located: dict[str, bool] = {}
        for side in sides:
            image = images[side]
            data = self.store.get(image.encrypted_object_ref, record.organization_id, record.id, image.id)
            capture = decode_capture(data, max_bytes=len(data), max_pixels=self.max_pixels)
            prepared, located[side] = prepare_side(capture.pixels, aspect, self.quality)
            layout = getattr(adapter, "layout", None)
            viz = layout.viz_regions.get(side) if layout else None
            read = (self.ocr.read_region(prepared, viz, self.languages) if viz and hasattr(self.ocr, "read_region")
                    else self.ocr.read_lines(prepared, self.languages))
            refinement = getattr(adapter, "numeric_refinement", None)
            if refinement and set(refinement.languages) <= self.ocr.available_languages():
                read = refine_numeric_words(self.ocr, prepared, read, refinement)
            region = layout.mrz_regions.get(side) if layout else None
            if region and hasattr(self.ocr, "read_region"):
                # Dedicated MRZ pass: ICAO alphabet only, so '<' fillers survive.
                read = read + read_mrz_lines(self.ocr, prepared, region)
            lines[side] = read

        classifications = {side: adapter.classify(lines[side], side) for side in sides}
        notes: list[str] = []
        if "FRONT" in classifications and "BACK" in classifications and \
                classifications["FRONT"].document_side == "BACK" and classifications["BACK"].document_side == "FRONT":
            # The person uploaded the sides the wrong way round; use them as they really are.
            lines["FRONT"], lines["BACK"] = lines["BACK"], lines["FRONT"]
            classifications["FRONT"], classifications["BACK"] = classifications["BACK"], classifications["FRONT"]
            notes.append("SIDES_SWAPPED")
        primary = classifications[sides[0]]
        if primary.document_type not in (adapter.document_type,):
            code = "DOCUMENT_NOT_RECOGNIZED" if primary.confidence == 0 else "DOCUMENT_TYPE_MISMATCH"
            return self._recapture(db, record, tenant, document, images, request_id, (code,), classifications)
        if primary.confidence < adapter.policy.recapture_below or primary.document_side not in (sides[0], "DATA_PAGE"):
            return self._recapture(db, record, tenant, document, images, request_id, ("DOCUMENT_NOT_RECOGNIZED",), classifications)
        if len(sides) > 1 and classifications[sides[1]].document_side == sides[0]:
            return self._recapture(db, record, tenant, document, images, request_id, (f"{sides[1]}_SIDE_EXPECTED",), classifications)

        extracted = adapter.extract_fields(lines)
        checks = adapter.validate_fields(extracted, today)
        if any(check.check_type == "REQUIRED_FIELDS" and check.result == CheckResult.FAIL for check in checks):
            return self._recapture(db, record, tenant, document, images, request_id, ("CRITICAL_FIELD_UNREADABLE",), classifications)

        # The primary side identifies the card; a secondary side without cues is noted, not penalized.
        confidence = primary.confidence
        notes.extend(f"{side}_SIDE_UNVERIFIED" for side in sides[1:] if classifications[side].document_side == "UNKNOWN")
        classification_result = CheckResult.PASS if confidence >= adapter.policy.accept_classification else CheckResult.REVIEW
        checks.insert(0, CheckEvidence(classification_result,
                                       ("DOCUMENT_CLASSIFIED",) if classification_result == CheckResult.PASS else ("LOW_CLASSIFICATION_CONFIDENCE",),
                                       check_type="CLASSIFICATION",
                                       details={"sides": {side: {"side": item.document_side, "confidence": item.confidence}
                                                          for side, item in classifications.items()}, "notes": notes}))
        self._store_fields(db, record, document, extracted)
        document.classification_confidence = confidence
        document.side_classification = {side: {"side": item.document_side, "confidence": item.confidence,
                                               "document_located": located[side]} for side, item in classifications.items()}
        document.extraction_version = f"{adapter.version}; {self.ocr.engine_version}"
        document.processed_at = datetime.now(timezone.utc)
        if extracted.document_number:
            document.document_number_hmac = self.cipher.lookup_hash(extracted.document_number, adapter.document_type.value)
        db.execute(sa.delete(MRZResult).where(MRZResult.organization_id == record.organization_id,
                                              MRZResult.session_id == record.id, MRZResult.document_id == document.id))
        mrz_check = next((check for check in checks if check.check_type == "MRZ" and "format" in check.details), None)
        if mrz_check is not None:
            consistency = next((check.details.get("field_consistency", {}) for check in checks
                                if check.check_type == "MRZ_CONSISTENCY"), {})
            db.add(MRZResult(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                             format=mrz_check.details["format"], mrz_valid=mrz_check.details["mrz_valid"],
                             check_digit_results=mrz_check.details["check_digit_results"],
                             field_consistency=consistency))
        for check in checks:
            db.add(DocumentCheck(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                                 check_type=check.check_type, result=check.result,
                                 evidence_metadata={"reason_codes": list(check.reason_codes), **check.details,
                                                    "adapter_version": adapter.version}))
        apply_event(db, record, tenant, Event.DOCUMENT_ACCEPTED, request_id)
        worst = sorted({check.result.value for check in checks})
        db.add(AuditLog(organization_id=tenant.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                        action="DOCUMENT_EXTRACTED", request_id=request_id, from_status=SessionStatus.DOCUMENT_PROCESSING.value,
                        to_status=record.status.value, reason_codes=[code for check in checks for code in check.reason_codes
                                                                     if check.result in (CheckResult.REVIEW, CheckResult.FAIL)],
                        event_metadata={"version": record.version, "check_results": worst, "adapter_version": adapter.version}))
        db.flush()
        return ProcessingOutcome("ACCEPTED")

    def _store_fields(self, db: Session, record: KYCSession, document: IdentityDocument, extracted) -> None:
        db.execute(sa.delete(DocumentField).where(DocumentField.organization_id == record.organization_id,
                                                  DocumentField.session_id == record.id,
                                                  DocumentField.document_id == document.id))
        for item in extracted.fields:
            context = f"field/{record.organization_id}/{record.id}/{document.id}/{item.field}"
            raw, version = self.cipher.seal(item.raw_value, context + "/raw") if item.raw_value else (None, None)
            normalized, version_n = (self.cipher.seal(item.normalized_value, context + "/normalized")
                                     if item.normalized_value else (None, None))
            db.add(DocumentField(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                                 field_name=item.field, raw_value_ciphertext=raw, normalized_value_ciphertext=normalized,
                                 key_version=version or version_n, confidence=item.confidence,
                                 bounding_box=list(item.bbox) if item.bbox else None, side=item.side,
                                 source=item.source, flags=list(item.flags)))

    def _recapture(self, db: Session, record: KYCSession, tenant: TenantContext, document: IdentityDocument | None,
                   images: dict[str, DocumentImage], request_id: UUID, reasons: tuple[str, ...],
                   classifications: dict | None = None) -> ProcessingOutcome:
        if document is not None:
            db.add(DocumentCheck(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                                 check_type="DOCUMENT_PROCESSING", result=CheckResult.FAIL,
                                 evidence_metadata={"outcome": "RECAPTURE", "reason_codes": list(reasons),
                                                    "sides": {side: {"type": item.document_type.value, "side": item.document_side,
                                                                     "confidence": item.confidence}
                                                              for side, item in (classifications or {}).items()}}))
        # Stale captures must not satisfy the next attempt; their ciphertext goes in the orphan sweep.
        for image in images.values():
            db.delete(image)
        db.flush()
        apply_event(db, record, tenant, Event.RECAPTURE_REQUIRED, request_id)
        db.add(AuditLog(organization_id=tenant.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                        action="DOCUMENT_RECAPTURE_REQUESTED", request_id=request_id,
                        from_status=SessionStatus.DOCUMENT_PROCESSING.value, to_status=record.status.value,
                        reason_codes=list(reasons), event_metadata={"version": record.version}))
        db.flush()
        return ProcessingOutcome("RECAPTURE", reasons)


def pending_sessions(factory: sessionmaker, organization_id: UUID) -> list[UUID]:
    with factory() as db, db.begin():
        set_tenant(db, organization_id)
        return list(db.scalars(sa.select(KYCSession.id).where(KYCSession.organization_id == organization_id,
                                                              KYCSession.status == SessionStatus.DOCUMENT_PROCESSING)))

