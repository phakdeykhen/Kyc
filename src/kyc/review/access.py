"""Reviewer roles, permissions, authentication and decision reason codes (spec §20).

Reviewers authenticate with their own bearer token, scoped to one organization. The
client application's API key cannot reach review endpoints, and reviewer tokens cannot
reach client endpoints, so an integrating application can never approve its own
sessions. Every permission check is enforced server-side; the dashboard only reflects it.
"""

from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.db.models import Organization, Reviewer
from kyc.db.session import set_tenant

TOKEN_PREFIX = "rvw_"

# VIEW_CASE   checks, signals, risk trace, masked document number, history (reason codes only)
# VIEW_IDENTITY  unmasked identity fields and reviewers' free-text notes
# VIEW_IMAGES document photos and selfie, while retention allows
# DECIDE      approve, reject, request recapture
ROLES = {
    "REVIEWER": frozenset({"VIEW_CASE", "VIEW_IDENTITY", "VIEW_IMAGES", "DECIDE"}),
    "AUDITOR": frozenset({"VIEW_CASE"}),
}

# Reason codes are a fixed vocabulary per action; the free-text note carries the detail.
REASONS = {
    "APPROVE": ("DOCUMENT_CONFIRMED_GENUINE", "FACE_CONFIRMED_SAME_PERSON", "OCR_ERROR_CONFIRMED",
                "DATA_DIFFERENCE_EXPLAINED", "DUPLICATE_USE_EXPLAINED"),
    "REJECT": ("DOCUMENT_SUSPECTED_FORGED", "FACE_DIFFERENT_PERSON", "PRESENTATION_ATTACK_SUSPECTED",
               "DOCUMENT_EXPIRED", "IDENTITY_MISUSE_SUSPECTED", "POLICY_NOT_MET"),
    "REQUEST_RECAPTURE": ("DOCUMENT_UNREADABLE", "WRONG_DOCUMENT", "GLARE_OR_BLUR", "SELFIE_UNUSABLE",
                          "EVIDENCE_INCOMPLETE"),
}


def new_token() -> tuple[str, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    # The token carries 256 random bits, so a plain SHA-256 is not guessable (unlike a password).
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class ReviewerContext:
    organization_id: UUID
    reviewer_id: UUID
    display_name: str
    role: str
    permissions: frozenset[str]

    @property
    def actor_id(self) -> str:
        return f"reviewer:{self.reviewer_id}"

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise HTTPException(403, detail="Your reviewer role does not allow this.")


@dataclass
class ReviewSession:
    db: Session
    reviewer: ReviewerContext


def review_session(request: Request,
                   authorization: Annotated[str | None, Header()] = None,
                   organization: Annotated[str | None, Header(alias="X-Organization-ID")] = None) -> Iterator[ReviewSession]:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        raise HTTPException(401, detail="A reviewer token is required.")
    try:
        organization_id = UUID(organization or "")
    except ValueError:
        raise HTTPException(400, detail="A valid X-Organization-ID is required.") from None
    with request.app.state.session_factory() as db, db.begin():
        set_tenant(db, organization_id)  # RLS: a token can only be found inside its own organization
        row = db.scalar(sa.select(Reviewer).where(Reviewer.organization_id == organization_id,
                                                  Reviewer.token_sha256 == token_hash(token), Reviewer.active.is_(True)))
        organization = db.get(Organization, organization_id)
        if row is None or organization is None:
            raise HTTPException(401, detail="A valid reviewer token is required.")
        if not organization.active:
            raise HTTPException(403, detail="This organization is suspended.")
        yield ReviewSession(db, ReviewerContext(organization_id, row.id, row.display_name, row.role, ROLES[row.role]))


Review = Annotated[ReviewSession, Depends(review_session)]
