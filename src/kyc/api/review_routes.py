"""Reviewer API (spec §24): reviewer bearer token only; the client API key is not accepted here."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from kyc.domain.enums import SessionStatus
from kyc.review.access import Review
from kyc.services import review as service

router = APIRouter(prefix="/v1/review", tags=["review"])


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["APPROVE", "REJECT", "REQUEST_RECAPTURE"]
    reason_code: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z_]+$")
    note: str = Field(min_length=5, max_length=1000, description="Why; stored encrypted, shown only to reviewers.")
    expected_version: int = Field(ge=1, description="The case version you reviewed (from GET /v1/review/{session}).")


@router.get("/me")
def me(review: Review):
    reviewer = review.reviewer
    return {"reviewer_id": reviewer.reviewer_id, "display_name": reviewer.display_name, "role": reviewer.role,
            "organization_id": reviewer.organization_id, "permissions": sorted(reviewer.permissions)}


@router.get("/queue")
def review_queue(review: Review, limit: Annotated[int, Query(ge=1, le=100)] = 25,
                 offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
                 order: Literal["oldest", "newest"] = "oldest"):
    """Cases waiting in MANUAL_REVIEW."""
    return service.queue(review.db, review.reviewer, limit, offset, order=order)


@router.get("/sessions")
def all_sessions(review: Review, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                 offset: Annotated[int, Query(ge=0, le=100_000)] = 0, status: SessionStatus | None = None,
                 user_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None):
    """Every session of the organization, newest change first, by status and the client's user ID."""
    return service.sessions(review.db, review.reviewer, limit, offset, status=status, user_id=user_id)


@router.get("/{session_id}")
def review_case(session_id: UUID, request: Request, review: Review):
    """Everything this reviewer is authorized to see for one case (the view is audited)."""
    return service.case(review.db, review.reviewer, session_id, request.app.state.field_cipher, request.state.request_id)


@router.get("/{session_id}/images/{image_id}")
def review_image(session_id: UUID, image_id: UUID, request: Request, review: Review):
    """A retained document photo or selfie (VIEW_IMAGES; audited; never cached)."""
    return service.image(review.db, review.reviewer, session_id, image_id, request.app.state.capture_store,
                         request.state.request_id)


@router.post("/{session_id}/decision")
def review_decision(session_id: UUID, body: ReviewDecision, request: Request, review: Review):
    """APPROVE, REJECT or REQUEST_RECAPTURE, with a reason code and a note (audited)."""
    state = request.app.state
    return service.decide(review.db, review.reviewer, session_id, body.action, body.reason_code, body.note,
                          body.expected_version, state.field_cipher, state.assessor.policy, request.state.request_id,
                          state.settings.session_ttl_seconds)
