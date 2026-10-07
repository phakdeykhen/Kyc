"""Which state changes produce which webhook events."""

from kyc.domain.enums import SessionStatus as S
from kyc.domain.state_machine import Event

EVENT_TYPES = {
    "kyc.processing": "All evidence is in; the risk engine is deciding.",
    "kyc.document.accepted": "The document was read and accepted.",
    "kyc.selfie.required": "The person should take a selfie next.",
    "kyc.recapture.required": "New document photos are needed (unreadable, or a reviewer asked).",
    "kyc.review.required": "The case went to manual review.",
    "kyc.verified": "The session was verified (automatically or by a reviewer).",
    "kyc.rejected": "The session was rejected (automatically or by a reviewer).",
    "kyc.expired": "The session expired before completion.",
}
TEST_EVENT = "webhook.test"

_BY_STATUS = {S.PROCESSING: "kyc.processing", S.SELFIE_REQUIRED: "kyc.selfie.required",
              S.MANUAL_REVIEW: "kyc.review.required", S.VERIFIED: "kyc.verified", S.REJECTED: "kyc.rejected",
              S.EXPIRED: "kyc.expired"}


def events_for(event: Event, previous: S, status: S) -> list[str]:
    """Event types for one transition, in the order they happened."""
    found = []
    if event == Event.DOCUMENT_ACCEPTED:
        found.append("kyc.document.accepted")
    if event in (Event.RECAPTURE_REQUIRED, Event.REFERENCE_RECAPTURE_REQUIRED):
        found.append("kyc.recapture.required")
    if status != previous and status in _BY_STATUS:
        found.append(_BY_STATUS[status])
    return found
