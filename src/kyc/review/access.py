"""Reviewer roles, permissions, authentication and decision reason codes (spec §20).

Reviewers authenticate with their own bearer token, scoped to one organization. The
client application's API key cannot reach review endpoints, and reviewer tokens cannot
reach client endpoints, so an integrating application can never approve its own
sessions. Every permission check is enforced server-side; the dashboard only reflects it.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.api.dependencies import refuse
from kyc.core.observability import route_label
from kyc.db.models import AuditLog, Organization, Reviewer
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
    # Records a refused action outside the request's transaction, which the 403 rolls back (Phase 17).
    on_denied: Callable[[str], None] | None = field(default=None, compare=False, repr=False)

    @property
    def actor_id(self) -> str:
        return f"reviewer:{self.reviewer_id}"

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            if self.on_denied is not None:
                self.on_denied(permission)
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
        raise refuse(request, 401, "REVIEWER_TOKEN_MISSING", "A reviewer token is required.")
    try:
        organization_id = UUID(organization or "")
    except ValueError:
        raise refuse(request, 400, "ORGANIZATION_HEADER_INVALID", "A valid X-Organization-ID is required.") from None
    with request.app.state.session_factory() as db, db.begin():
        set_tenant(db, organization_id)  # RLS: a token can only be found inside its own organization
        row = db.scalar(sa.select(Reviewer).where(Reviewer.organization_id == organization_id,
                                                  Reviewer.token_sha256 == token_hash(token), Reviewer.active.is_(True)))
        organization = db.get(Organization, organization_id)
        if row is None or organization is None:
            raise refuse(request, 401, "REVIEWER_TOKEN_INVALID", "A valid reviewer token is required.")
        if (row.expires_at is None and request.app.state.settings.environment == "production") or \
                (row.expires_at is not None and _aware(row.expires_at) <= datetime.now(timezone.utc)):
            raise refuse(request, 401, "REVIEWER_TOKEN_EXPIRED", "This reviewer token has expired; ask for a new one.")
        if not organization.active:
            raise refuse(request, 403, "ORGANIZATION_SUSPENDED", "This organization is suspended.")
        if row.role not in ROLES:
            raise refuse(request, 403, "REVIEWER_ROLE_INVALID", "Your reviewer role does not allow this.")
        context = ReviewerContext(organization_id, row.id, row.display_name, row.role, ROLES[row.role],
                                  on_denied=_denial_recorder(request, organization_id, row.id))
        yield ReviewSession(db, context)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _denial_recorder(request: Request, organization_id: UUID, reviewer_id: UUID) -> Callable[[str], None]:
    def record(permission: str) -> None:
        request.state.security_reason = "REVIEWER_PERMISSION_DENIED"
        with request.app.state.session_factory() as db, db.begin():
            set_tenant(db, organization_id)
            session_id = request.path_params.get("session_id")
            db.add(AuditLog(organization_id=organization_id, session_id=UUID(str(session_id)) if session_id else None,
                            actor_id=f"reviewer:{reviewer_id}", action="REVIEW_ACCESS_DENIED",
                            request_id=request.state.request_id, reason_codes=["REVIEW_ACCESS_DENIED"],
                            event_metadata={"permission": permission, "path": route_label(request)}))
    return record


Review = Annotated[ReviewSession, Depends(review_session, scope="function")]
