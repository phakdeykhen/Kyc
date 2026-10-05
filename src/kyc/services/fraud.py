"""Cross-check and fraud analysis (Phase 12), run when a session reaches PROCESSING.

Gathers facts from the evidence earlier phases stored, decrypting values only in memory
to compare them, and records signals. Identity values never enter signals, checks or
audit logs. Re-running replaces the previous analysis, so the stored signals always
describe the current evidence. The session status is not changed: deciding is the
risk engine's job (Phase 13).
"""

import base64
from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from kyc.api.dependencies import TenantContext
from kyc.barcode.evaluate import _norm as normalize_barcode
from kyc.barcode.payload import parse as parse_barcode
from kyc.biometrics import compare_embeddings, deserialize_embedding
from kyc.biometrics.types import FaceMatchPolicy, InvalidFaceEmbedding
from kyc.core.crypto import FieldCipher
from kyc.db.models import (AuditLog, BarcodeResult, BiometricTemplate, DocumentCheck, DocumentField, DocumentImage,
                           FraudSignal, IdentityDocument, KYCSession, MRZResult, NFCResult, SelfieCapture)
from kyc.db.session import set_tenant
from kyc.domain.enums import CheckResult, SessionStatus
from kyc.fraud import engine, metadata
from kyc.fraud.crosscheck import Comparison
from kyc.fraud.engine import (BarcodeFacts, CaptureMetadata, DuplicateFacts, FraudContext, FraudReport, NFCFacts,
                              PortraitFacts)
from kyc.mrz import parser as mrz_parser
from kyc.services.biometrics import _template_context

log = logging.getLogger(__name__)
SYSTEM_ACTOR = "fraud-engine"
DERIVED_CHECKS = ("CROSS_CHECK", "FRAUD_ANALYSIS")
SEVERITY = {"FAIL": 3, "REVIEW": 2, "UNAVAILABLE": 2, "PASS": 1, "NOT_APPLICABLE": 0}
MRZ_BARCODE_FIELDS = ("document_number", "date_of_birth", "expiry_date", "sex", "full_name")


class FraudAnalyzer:
    def __init__(self, factory: sessionmaker | None, field_cipher: FieldCipher | None, biometric_cipher,
                 capture_store, face_policy: FaceMatchPolicy | None, window_hours: int = 24, velocity_limit: int = 3):
        self.factory = factory
        self.field_cipher = field_cipher
        self.biometric_cipher = biometric_cipher
        self.capture_store = capture_store
        self.face_policy = face_policy or FaceMatchPolicy()
        self.window_hours = window_hours
        self.velocity_limit = velocity_limit

    def analyze(self, organization_id: UUID, session_id: UUID, request_id: UUID) -> str:
        """Own transaction (runs after the request that reached PROCESSING has committed)."""
        try:
            with self.factory() as db, db.begin():
                set_tenant(db, organization_id)
                record = db.scalar(sa.select(KYCSession).where(KYCSession.id == session_id,
                                   KYCSession.organization_id == organization_id).with_for_update())
                if record is None or record.status != SessionStatus.PROCESSING:
                    return "NOT_READY"
                report = self.analyze_in(db, record, TenantContext(organization_id, actor_id=SYSTEM_ACTOR), request_id)
                return "NO_DOCUMENT" if report is None else report.result
        except Exception as error:  # noqa: BLE001 - recorded; the analysis can be re-run
            log.warning("Fraud analysis failed for a session: %s", type(error).__name__)
            with self.factory() as db, db.begin():
                set_tenant(db, organization_id)
                db.add(AuditLog(organization_id=organization_id, session_id=session_id, actor_id=SYSTEM_ACTOR,
                                action="FRAUD_ANALYSIS_FAILED", request_id=request_id,
                                reason_codes=[type(error).__name__], event_metadata={}))
            return "ERROR"

    def analyze_in(self, db: Session, record: KYCSession, tenant: TenantContext, request_id: UUID) -> FraudReport | None:
        document = db.scalar(sa.select(IdentityDocument).where(
            IdentityDocument.organization_id == record.organization_id, IdentityDocument.session_id == record.id,
            IdentityDocument.processed_at.is_not(None)).order_by(IdentityDocument.created_at.desc()).limit(1))
        if document is None:
            return None
        context = self.gather(db, record, document)
        report = engine.analyze(context)
        self._persist(db, record, document, tenant, request_id, report)
        return report

    # Fact gathering -----------------------------------------------------------------
    def gather(self, db: Session, record: KYCSession, document: IdentityDocument) -> FraudContext:
        now = datetime.now(timezone.utc)
        context = FraudContext(verification_level=record.verification_level.value, session_date=now.date())
        context.checks = self._checks(db, record, document)
        values = self._values(db, record, document)
        mrz = mrz_parser.read(values["mrz"].splitlines()) if values.get("mrz") else None
        stored_mrz = db.scalar(sa.select(MRZResult).where(MRZResult.organization_id == record.organization_id,
                                                          MRZResult.session_id == record.id,
                                                          MRZResult.document_id == document.id))
        digits = (stored_mrz.check_digit_results if stored_mrz else None) or (mrz.check_digits if mrz else {})

        def mrz_protected(name: str) -> frozenset[str]:
            return frozenset({"MRZ"}) if digits.get(name, {}).get("valid") else frozenset()

        if stored_mrz is not None:
            for name, state in (stored_mrz.field_consistency or {}).items():
                context.comparisons.append(Comparison("MRZ", "VISUAL", name, state, mrz_protected(name)))
        for row in db.scalars(sa.select(BarcodeResult).where(BarcodeResult.organization_id == record.organization_id,
                                                             BarcodeResult.session_id == record.id,
                                                             BarcodeResult.document_id == document.id)):
            signed = frozenset({"BARCODE"}) if row.signature_valid else frozenset()
            context.barcodes.append(BarcodeFacts(row.symbology, row.signature_present, row.signature_valid,
                                                 (row.data_consistency or {}).get("signature_reason")))
            for name, state in ((row.data_consistency or {}).get("fields") or {}).items():
                context.comparisons.append(Comparison("BARCODE", "VISUAL", name, state, signed))
            coded = self._barcode_fields(record, document, row)
            if mrz is not None and coded:
                for name in MRZ_BARCODE_FIELDS:
                    machine = getattr(mrz, name, None)
                    machine = machine.isoformat() if hasattr(machine, "isoformat") else machine
                    if machine and coded.get(name):
                        same = normalize_barcode(name, str(machine).replace("<", "")) == normalize_barcode(name, coded[name])
                        context.comparisons.append(Comparison("MRZ", "BARCODE", name, "MATCH" if same else "MISMATCH",
                                                              mrz_protected(name) | signed))
        nfc = next((row for row in db.scalars(sa.select(NFCResult).where(
            NFCResult.organization_id == record.organization_id, NFCResult.session_id == record.id)
            .order_by(NFCResult.created_at.desc())) if not row.evidence_metadata.get("retryable")), None)
        if nfc is not None:
            evidence = nfc.evidence_metadata or {}
            context.nfc = NFCFacts(nfc.status.value, tuple(evidence.get("reason_codes", [])), nfc.active_authentication)
            signed = frozenset({"NFC"}) if nfc.status.value == "NFC_VERIFIED" else frozenset()
            for name, state in (evidence.get("document_consistency") or {}).items():
                other = "MRZ" if name == "mrz_data_lines" else "VISUAL"
                context.comparisons.append(Comparison("NFC", other, name, state, signed))
            if signed:
                context.portrait = self._portrait(db, record, document, now)
        context.duplicates = self._duplicates(db, record, document, now)
        context.metadata = self._metadata(db, record, document)
        return context

    @staticmethod
    def _checks(db, record, document) -> dict[str, tuple[str, tuple[str, ...]]]:
        merged: dict[str, tuple[str, tuple[str, ...]]] = {}
        for row in db.scalars(sa.select(DocumentCheck).where(
                DocumentCheck.organization_id == record.organization_id, DocumentCheck.document_id == document.id,
                DocumentCheck.created_at >= document.processed_at, DocumentCheck.check_type.not_in(DERIVED_CHECKS))):
            result, reasons = merged.get(row.check_type, ("NOT_APPLICABLE", ()))
            current = row.result.value
            worst = current if SEVERITY.get(current, 0) > SEVERITY.get(result, 0) else result
            merged[row.check_type] = (worst, tuple(dict.fromkeys(reasons + tuple(row.evidence_metadata.get("reason_codes", [])))))
        return merged

    def _values(self, db, record, document) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.field_cipher is None:
            return values
        for field in db.scalars(sa.select(DocumentField).where(DocumentField.organization_id == record.organization_id,
                                                               DocumentField.document_id == document.id,
                                                               DocumentField.field_name == "mrz")):
            if field.normalized_value_ciphertext is not None:
                context = f"field/{record.organization_id}/{record.id}/{document.id}/{field.field_name}/normalized"
                values[field.field_name] = self.field_cipher.open(field.normalized_value_ciphertext, field.key_version, context)
        return values

    def _barcode_fields(self, record, document, row: BarcodeResult) -> dict[str, str]:
        if self.field_cipher is None or row.payload_ciphertext is None:
            return {}
        side = (row.data_consistency or {}).get("side")
        context = f"barcode/{record.organization_id}/{record.id}/{document.id}/{side}/{row.symbology}"
        payload = base64.b64decode(self.field_cipher.open(row.payload_ciphertext, row.key_version, context))
        return parse_barcode(payload, payload.decode("utf-8", "replace")).fields

    def _duplicates(self, db, record, document, now) -> DuplicateFacts:
        other_users = same_user = recent = 0
        if document.document_number_hmac:
            rows = db.execute(sa.select(KYCSession.user_id, IdentityDocument.processed_at).join(
                KYCSession, sa.and_(KYCSession.organization_id == IdentityDocument.organization_id,
                                    KYCSession.id == IdentityDocument.session_id)).where(
                IdentityDocument.organization_id == record.organization_id,
                IdentityDocument.document_number_hmac == document.document_number_hmac,
                IdentityDocument.document_type == document.document_type,
                IdentityDocument.session_id != record.id, IdentityDocument.processed_at.is_not(None))).all()
            since = now - timedelta(hours=self.window_hours)
            other_users = len({user for user, _ in rows if user != record.user_id})
            same_user = sum(1 for user, _ in rows if user == record.user_id)
            recent = 1 + sum(1 for _, processed in rows if _aware(processed) >= since)
        hashes = {image.side: image.sha256 for image in db.scalars(sa.select(DocumentImage).where(
            DocumentImage.organization_id == record.organization_id, DocumentImage.document_id == document.id))}
        reused = set()
        if hashes:
            found = set(db.scalars(sa.select(DocumentImage.sha256).where(
                DocumentImage.organization_id == record.organization_id, DocumentImage.sha256.in_(hashes.values()),
                DocumentImage.session_id != record.id)))
            reused = {side for side, digest in hashes.items() if digest in found}
        selfie = db.scalar(sa.select(SelfieCapture.sha256).where(SelfieCapture.organization_id == record.organization_id,
                                                                 SelfieCapture.session_id == record.id))
        reused_selfie = bool(selfie) and bool(db.scalar(sa.select(sa.func.count()).select_from(SelfieCapture).where(
            SelfieCapture.organization_id == record.organization_id, SelfieCapture.sha256 == selfie,
            SelfieCapture.session_id != record.id)))
        return DuplicateFacts(self.window_hours, self.velocity_limit, other_users, same_user, recent,
                              tuple(sorted(reused)), reused_selfie)

    def _metadata(self, db, record, document) -> list[CaptureMetadata]:
        if self.capture_store is None:
            return []
        files = [(image.side, image.id, image.encrypted_object_ref, image.media_type) for image in db.scalars(
            sa.select(DocumentImage).where(DocumentImage.organization_id == record.organization_id,
                                           DocumentImage.document_id == document.id))]
        files += [("SELFIE", item.id, item.encrypted_object_ref, item.media_type) for item in db.scalars(
            sa.select(SelfieCapture).where(SelfieCapture.organization_id == record.organization_id,
                                           SelfieCapture.session_id == record.id))]
        found = []
        for side, object_id, ref, media_type in files:
            try:
                data = self.capture_store.get(ref, record.organization_id, record.id, object_id)
            except Exception:  # noqa: BLE001 - purged or unreadable capture: no metadata evidence
                continue
            editor, screenshot, captured = metadata.read(data)
            found.append(CaptureMetadata(side, media_type, editor, screenshot, captured))
        return found

    def _portrait(self, db, record, document, now) -> PortraitFacts | None:
        if self.biometric_cipher is None:
            return None

        def latest(source, **filters):
            query = sa.select(BiometricTemplate).where(BiometricTemplate.organization_id == record.organization_id,
                                                       BiometricTemplate.session_id == record.id,
                                                       BiometricTemplate.source == source,
                                                       BiometricTemplate.delete_after > now)
            for column, value in filters.items():
                query = query.where(getattr(BiometricTemplate, column) == value)
            return db.scalar(query.order_by(BiometricTemplate.created_at.desc()).limit(1))

        printed, chip = latest("DOCUMENT_PORTRAIT", document_id=document.id), latest("CHIP_PORTRAIT")
        if printed is None or chip is None:
            return None
        try:
            embeddings = [deserialize_embedding(self.biometric_cipher.open(
                item.template_ciphertext, item.key_version, _template_context(record, item.id, item.source, item)))
                for item in (printed, chip)]
            comparison = compare_embeddings(*embeddings, self.face_policy)
        except (InvalidFaceEmbedding, ValueError):
            return None
        return PortraitFacts(comparison.score, self.face_policy.fail_threshold, self.face_policy.calibrated,
                             comparison.threshold_policy_version)

    # Persistence --------------------------------------------------------------------
    @staticmethod
    def _persist(db, record, document, tenant, request_id, report: FraudReport) -> None:
        scope = (FraudSignal.organization_id == record.organization_id, FraudSignal.session_id == record.id)
        db.execute(sa.delete(FraudSignal).where(*scope))
        db.execute(sa.delete(DocumentCheck).where(DocumentCheck.organization_id == record.organization_id,
                                                  DocumentCheck.session_id == record.id,
                                                  DocumentCheck.check_type.in_(DERIVED_CHECKS)))
        for item in report.signals:
            db.add(FraudSignal(organization_id=record.organization_id, session_id=record.id, signal=item.signal,
                               severity=item.severity.value, category=item.category.value,
                               evidence_metadata={"detector": item.detector, "fields": list(item.fields),
                                                  "sources": list(item.sources), "details": item.details,
                                                  "engine_version": report.engine_version}))
        conflicts = [item.signal for item in report.signals if item.detector == "cross-check"]
        db.add(DocumentCheck(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                             check_type="CROSS_CHECK", result=CheckResult(report.cross_check_result),
                             evidence_metadata={"reason_codes": conflicts or (["SOURCES_CONSISTENT"]
                                                if report.cross_check_result == "PASS" else ["NO_INDEPENDENT_SOURCES"]),
                                                "fields": report.cross_check, "engine_version": report.engine_version}))
        flagged = [item.signal for item in report.signals if item.severity.value in ("MEDIUM", "HIGH")]
        counts = {level: sum(1 for item in report.signals if item.severity.value == level) for level in ("HIGH", "MEDIUM", "LOW")}
        db.add(DocumentCheck(organization_id=record.organization_id, session_id=record.id, document_id=document.id,
                             check_type="FRAUD_ANALYSIS", result=CheckResult(report.result),
                             evidence_metadata={"reason_codes": flagged or ["NO_FRAUD_SIGNALS_FROM_SUPPORTED_DETECTORS"],
                                                "signal_counts": counts, "detectors": report.detectors,
                                                "coverage": report.coverage, "engine_version": report.engine_version}))
        db.add(AuditLog(organization_id=tenant.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                        action="FRAUD_ANALYZED", request_id=request_id, from_status=record.status.value,
                        to_status=record.status.value, reason_codes=[item.signal for item in report.signals],
                        event_metadata={"version": record.version, "result": report.result,
                                        "cross_check": report.cross_check_result, "engine_version": report.engine_version}))
        db.flush()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

