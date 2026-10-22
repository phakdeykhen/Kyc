"""Session assessment (Phase 13): fraud analysis, then the risk engine, then the status change.

PROCESSING → ASSESSMENT_PASS → VERIFIED   (state machine re-checks the evidence guard)
           → ASSESSMENT_REVIEW → MANUAL_REVIEW
           → ASSESSMENT_FAIL → REJECTED
Runs automatically after the request that reached PROCESSING commits, and on demand
through POST /v1/kyc/{id}/verify. Both lock the session row, so it is decided once.
"""

import logging
from uuid import UUID

from fastapi.responses import JSONResponse
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from kyc.api.dependencies import TenantContext
from kyc.api.schemas import VerifyResponse
from kyc.core.errors import error_response
from kyc.db.models import AuditLog, BarcodeResult, KYCSession, RiskAssessmentRecord
from kyc.db.session import set_tenant
from kyc.domain.enums import RiskDecision, SessionStatus
from kyc.domain.state_machine import Event, InvalidTransition, VerificationEvidence
from kyc.risk.engine import RiskInputs, evaluate
from kyc.risk.policy import RiskPolicy
from kyc.services.fraud import FraudAnalyzer
from kyc.services.results import collect_evidence, latest_decision
from kyc.services.sessions import apply_event

log = logging.getLogger(__name__)
SYSTEM_ACTOR = "risk-engine"
EVENTS = {"PASS": Event.ASSESSMENT_PASS, "REVIEW": Event.ASSESSMENT_REVIEW, "FAIL": Event.ASSESSMENT_FAIL}
DECIDED = {SessionStatus.MANUAL_REVIEW, SessionStatus.VERIFIED, SessionStatus.REJECTED}


class SessionAssessor:
    def __init__(self, factory: sessionmaker | None, fraud: FraudAnalyzer, policy: RiskPolicy | None = None):
        self.factory = factory
        self.fraud = fraud
        self.policy = policy or RiskPolicy()

    def assess(self, organization_id: UUID, session_id: UUID, request_id: UUID) -> str:
        """Own transaction; used after commit and by the deferred worker."""
        try:
            with self.factory() as db, db.begin():
                set_tenant(db, organization_id)
                record = db.scalar(sa.select(KYCSession).where(KYCSession.id == session_id,
                                   KYCSession.organization_id == organization_id).with_for_update())
                if record is None or record.status != SessionStatus.PROCESSING:
                    return "NOT_READY"
                assessment = self.assess_in(db, record, TenantContext(organization_id, actor_id=SYSTEM_ACTOR), request_id)
                return "NO_DOCUMENT" if assessment is None else assessment.decision.value
        except Exception as error:  # noqa: BLE001 - recorded; POST /verify or the worker can retry
            log.warning("Session assessment failed: %s", type(error).__name__)
            with self.factory() as db, db.begin():
                set_tenant(db, organization_id)
                db.add(AuditLog(organization_id=organization_id, session_id=session_id, actor_id=SYSTEM_ACTOR,
                                action="RISK_ASSESSMENT_FAILED", request_id=request_id,
                                reason_codes=[type(error).__name__], event_metadata={}))
            return "ERROR"

    def assess_in(self, db: Session, record: KYCSession, tenant: TenantContext,
                  request_id: UUID) -> RiskAssessmentRecord | None:
        """Caller holds the session row lock and has checked it is PROCESSING."""
        report = self.fraud.analyze_in(db, record, tenant, request_id)
        if report is None:
            return None
        evidence = collect_evidence(db, record)
        document = evidence.document
        resolved = self.policy.resolve(record.verification_level, document.issuing_country or record.country,
                                       document.document_type.value)
        inputs = RiskInputs(record.verification_level, dict(evidence.checks),
                            tuple((item.signal, item.category, item.severity) for item in evidence.signals),
                            bool(evidence.face_comparison and evidence.face_comparison.calibrated),
                            authenticity_sources(db, record, evidence.checks, report.coverage))
        outcome = evaluate(inputs, resolved)
        decision, reasons, trace = outcome.decision, outcome.reason_codes, outcome.trace
        previous = record.status
        try:
            apply_event(db, record, tenant, EVENTS[decision], request_id, evidence=outcome.evidence)
        except InvalidTransition:
            # The state machine's own evidence guard disagreed with a PASS: never verify on doubt.
            decision, reasons = "REVIEW", ["VERIFICATION_EVIDENCE_GUARD"]
            trace = trace + [{"outcome": "REVIEW", "reason": "VERIFICATION_EVIDENCE_GUARD", "rule": "state-machine"}]
            apply_event(db, record, tenant, Event.ASSESSMENT_REVIEW, request_id, evidence=VerificationEvidence())
        row = RiskAssessmentRecord(organization_id=record.organization_id, session_id=record.id,
                                   decision=RiskDecision(decision), policy_version=resolved.version, reason_codes=reasons,
                                   check_summary={"checks": inputs.checks, "signals": [list(item) for item in inputs.signals],
                                                  "trace": trace, "required": list(resolved.required),
                                                  "policy_digest": resolved.digest, "overrides_applied": list(resolved.overrides),
                                                  "verification_level": record.verification_level.value,
                                                  "authenticity_sources": list(inputs.authenticity_sources)})
        db.add(row)
        db.add(AuditLog(organization_id=tenant.organization_id, session_id=record.id, actor_id=tenant.actor_id,
                        action="RISK_ASSESSED", request_id=request_id, from_status=previous.value,
                        to_status=record.status.value, reason_codes=reasons,
                        event_metadata={"version": record.version, "decision": decision, "policy_version": resolved.version}))
        db.flush()
        return row


FORENSICS = ("IMAGE_MANIPULATION", "FONT_INCONSISTENCY", "DOCUMENT_LAYOUT_MISMATCH")


def authenticity_sources(db: Session, record: KYCSession, checks: dict, coverage: dict) -> tuple[str, ...]:
    sources = []
    if checks.get("nfc") == "PASS":
        sources.append("NFC_CHIP_VERIFIED")
    if db.scalar(sa.select(sa.func.count()).select_from(BarcodeResult).where(
            BarcodeResult.organization_id == record.organization_id, BarcodeResult.session_id == record.id,
            BarcodeResult.signature_valid.is_(True))):
        sources.append("SIGNED_BARCODE_VERIFIED")
    if all(coverage.get(name, {}).get("status") == "SUPPORTED" for name in FORENSICS):
        sources.append("DOCUMENT_FORENSICS")
    return tuple(sources)


def _error(code: int, reason: str, detail: str) -> JSONResponse:
    return error_response(code, reason, detail, attempts_remaining=None)


def verify_session(db: Session, tenant: TenantContext, record: KYCSession, assessor: SessionAssessor,
                   request_id: UUID) -> VerifyResponse | JSONResponse:
    """POST /verify: decide a PROCESSING session now, or return the decision already made (idempotent)."""
    if record.status == SessionStatus.PROCESSING:
        if assessor.assess_in(db, record, tenant, request_id) is None:
            return _error(409, "DOCUMENT_NOT_PROCESSED", "The document has not been processed.")
    elif record.status == SessionStatus.EXPIRED:
        return _error(409, "SESSION_EXPIRED", "The session has expired. Create a new session.")
    elif record.status not in DECIDED:
        return _error(409, "VERIFICATION_NOT_READY", f"Verification cannot run while the session is {record.status.value}.")
    decision = latest_decision(db, record)
    if decision is None:  # decided by a reviewer path without an assessment (not possible today)
        return _error(409, "NO_ASSESSMENT", "This session has no risk assessment.")
    return VerifyResponse(session_id=record.id, status=record.status, decision=decision)


def pending_assessments(factory: sessionmaker, organization_id: UUID) -> list[UUID]:
    """Every PROCESSING session is undecided (for the deferred worker and retries after failures)."""
    with factory() as db, db.begin():
        set_tenant(db, organization_id)
        return list(db.scalars(sa.select(KYCSession.id).where(KYCSession.organization_id == organization_id,
                                                              KYCSession.status == SessionStatus.PROCESSING)))
