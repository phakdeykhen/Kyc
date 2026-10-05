"""Pure, event-driven state machine. Events are internal, never client commands."""

from dataclasses import dataclass
from enum import StrEnum

from .enums import SessionStatus as S, VerificationLevel as L

TERMINAL_STATUSES = frozenset({S.VERIFIED, S.REJECTED, S.EXPIRED})


class Event(StrEnum):
    START = "START"
    DOCUMENT_SUBMITTED = "DOCUMENT_SUBMITTED"
    RECAPTURE_REQUIRED = "RECAPTURE_REQUIRED"
    DOCUMENT_ACCEPTED = "DOCUMENT_ACCEPTED"
    SELFIE_ACCEPTED = "SELFIE_ACCEPTED"
    LIVENESS_ACCEPTED = "LIVENESS_ACCEPTED"
    NFC_ACCEPTED = "NFC_ACCEPTED"
    ASSESSMENT_PASS = "ASSESSMENT_PASS"
    ASSESSMENT_REVIEW = "ASSESSMENT_REVIEW"
    ASSESSMENT_FAIL = "ASSESSMENT_FAIL"
    REVIEW_APPROVED = "REVIEW_APPROVED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    EXPIRE = "EXPIRE"


@dataclass(frozen=True)
class VerificationEvidence:
    """Trusted server-side evidence from future engines, not uploaded assertions."""

    document_valid: bool = False
    face_match: bool = False
    liveness_pass: bool = False
    nfc_verified: bool = False
    policy_pass: bool = False

    def complete_for(self, level: L) -> bool:
        return all((
            self.document_valid,
            self.policy_pass,
            level == L.DOCUMENT_ONLY or self.face_match,
            level in {L.DOCUMENT_ONLY, L.DOCUMENT_FACE} or self.liveness_pass,
            level != L.DOCUMENT_FACE_LIVENESS_NFC or self.nfc_verified,
        ))


class InvalidTransition(ValueError):
    pass


def transition(status: S, event: Event, level: L, evidence: VerificationEvidence | None = None) -> S:
    if status in TERMINAL_STATUSES:
        raise InvalidTransition("Terminal sessions cannot be changed. Create a new session.")
    if event == Event.EXPIRE:
        return S.EXPIRED
    fixed = {
        (S.CREATED, Event.START): S.DOCUMENT_REQUIRED,
        (S.DOCUMENT_REQUIRED, Event.DOCUMENT_SUBMITTED): S.DOCUMENT_PROCESSING,
        (S.DOCUMENT_PROCESSING, Event.RECAPTURE_REQUIRED): S.DOCUMENT_REQUIRED,
        (S.PROCESSING, Event.ASSESSMENT_REVIEW): S.MANUAL_REVIEW,
        (S.PROCESSING, Event.ASSESSMENT_FAIL): S.REJECTED,
        (S.MANUAL_REVIEW, Event.REVIEW_REJECTED): S.REJECTED,
        (S.MANUAL_REVIEW, Event.RECAPTURE_REQUIRED): S.DOCUMENT_REQUIRED,
    }
    if (status, event) in fixed:
        return fixed[(status, event)]
    if status == S.DOCUMENT_PROCESSING and event == Event.DOCUMENT_ACCEPTED:
        return S.PROCESSING if level == L.DOCUMENT_ONLY else S.SELFIE_REQUIRED
    if status == S.SELFIE_REQUIRED and event == Event.SELFIE_ACCEPTED:
        if level == L.DOCUMENT_ONLY:
            raise InvalidTransition("This verification level has no selfie stage.")
        return S.PROCESSING if level == L.DOCUMENT_FACE else S.LIVENESS_REQUIRED
    if status == S.LIVENESS_REQUIRED and event == Event.LIVENESS_ACCEPTED:
        if level not in {L.DOCUMENT_FACE_LIVENESS, L.DOCUMENT_FACE_LIVENESS_NFC}:
            raise InvalidTransition("This verification level has no liveness stage.")
        return S.NFC_REQUIRED if level == L.DOCUMENT_FACE_LIVENESS_NFC else S.PROCESSING
    if status == S.NFC_REQUIRED and event == Event.NFC_ACCEPTED:
        if level != L.DOCUMENT_FACE_LIVENESS_NFC:
            raise InvalidTransition("This verification level has no NFC stage.")
        return S.PROCESSING
    if (status, event) in {(S.PROCESSING, Event.ASSESSMENT_PASS), (S.MANUAL_REVIEW, Event.REVIEW_APPROVED)}:
        if evidence is None or not evidence.complete_for(level):
            raise InvalidTransition("Required independent evidence is incomplete.")
        return S.VERIFIED
    raise InvalidTransition(f"Event {event} is not allowed from {status}.")
