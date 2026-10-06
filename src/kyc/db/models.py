"""PostgreSQL-first schema. SQLite variants exist only for isolated fast tests."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from kyc.domain.enums import CheckResult, DocumentType, NFCStatus, ReviewAction, RiskDecision, SessionStatus, VerificationLevel

JSON_VALUE = sa.JSON().with_variant(JSONB(), "postgresql")


def now() -> datetime:
    return datetime.now(timezone.utc)


def enum_type(enum):
    return sa.Enum(enum, values_callable=lambda items: [item.value for item in items], native_enum=False, create_constraint=True)


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class Record:
    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now, server_default=sa.func.now())


class SessionArtifact(Record):
    organization_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)
    session_id: Mapped[UUID] = mapped_column(sa.Uuid, nullable=False)


def artifact_constraints(table: str, *extra):
    return (
        sa.ForeignKeyConstraint(["organization_id", "session_id"], ["kyc_sessions.organization_id", "kyc_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "session_id", "id", name=f"uq_{table}_scope_id"),
        *extra,
    )


def document_constraints(table: str, *extra):
    return artifact_constraints(table,
        sa.ForeignKeyConstraint(["organization_id", "session_id", "document_id"], ["identity_documents.organization_id", "identity_documents.session_id", "identity_documents.id"], ondelete="CASCADE"),
        sa.Index(f"ix_{table}_document_scope", "organization_id", "session_id", "document_id"),
        *extra,
    )


class Organization(Record, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(sa.String(200))
    pii_retention_days: Mapped[int] = mapped_column(default=30, server_default="30")
    capture_retention_hours: Mapped[int] = mapped_column(default=24, server_default="24")
    template_retention_hours: Mapped[int] = mapped_column(default=24, server_default="24")
    # A suspended organization's API keys, client tokens and reviewers stop working (Phase 15).
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True, server_default=sa.true())
    __table_args__ = (sa.CheckConstraint("pii_retention_days > 0 AND capture_retention_hours > 0 AND template_retention_hours > 0", name="retention_positive"),)


class KYCSession(Record, Base):
    __tablename__ = "kyc_sessions"
    organization_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id", ondelete="RESTRICT"))
    user_id: Mapped[str] = mapped_column(sa.String(128))
    country: Mapped[str] = mapped_column(sa.String(2))
    expected_document_type: Mapped[DocumentType] = mapped_column(enum_type(DocumentType))
    verification_level: Mapped[VerificationLevel] = mapped_column(enum_type(VerificationLevel))
    status: Mapped[SessionStatus] = mapped_column(enum_type(SessionStatus), default=SessionStatus.CREATED)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=now, onupdate=now)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    # Phase 15: the credential that created the session, the client's Idempotency-Key with a
    # fingerprint of the request it was first used with, and the session client token's hash.
    created_by: Mapped[str | None] = mapped_column(sa.String(128))
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(sa.String(64))
    client_token_sha256: Mapped[str | None] = mapped_column(sa.String(64))
    __table_args__ = (
        sa.UniqueConstraint("organization_id", "id", name="uq_kyc_sessions_scope_id"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_kyc_sessions_idempotency_key"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        sa.CheckConstraint("length(country) = 2 AND country = upper(country)", name="country_code"),
        sa.CheckConstraint("version > 0", name="version_positive"),
        sa.Index("ix_kyc_sessions_org_status_created", "organization_id", "status", "created_at"),
        sa.Index("ix_kyc_sessions_org_expires", "organization_id", "expires_at"),
    )


class IdentityDocument(SessionArtifact, Base):
    __tablename__ = "identity_documents"
    document_type: Mapped[DocumentType] = mapped_column(enum_type(DocumentType))
    issuing_country: Mapped[str | None] = mapped_column(sa.String(2))
    document_version: Mapped[str | None] = mapped_column(sa.String(80))
    document_number_hmac: Mapped[str | None] = mapped_column(sa.String(64))
    encrypted_identity_ref: Mapped[str | None] = mapped_column(sa.String(1024))
    classification_confidence: Mapped[float | None] = mapped_column(sa.Float)
    side_classification: Mapped[dict] = mapped_column(JSON_VALUE, default=dict, server_default="{}")
    extraction_version: Mapped[str | None] = mapped_column(sa.String(200))
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    delete_after: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = artifact_constraints(__tablename__, sa.CheckConstraint("classification_confidence IS NULL OR classification_confidence BETWEEN 0 AND 1", name="confidence_range"), sa.Index("ix_identity_documents_org_number_hmac", "organization_id", "document_number_hmac"))


class DocumentImage(SessionArtifact, Base):
    __tablename__ = "document_images"
    document_id: Mapped[UUID] = mapped_column(sa.Uuid)
    side: Mapped[str] = mapped_column(sa.String(20))
    encrypted_object_ref: Mapped[str] = mapped_column(sa.String(1024))
    key_version: Mapped[str] = mapped_column(sa.String(256))
    media_type: Mapped[str] = mapped_column(sa.String(40))
    sha256: Mapped[str] = mapped_column(sa.String(64))
    quality_scores: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    quality_policy_version: Mapped[str] = mapped_column(sa.String(80))
    delete_after: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = document_constraints(__tablename__,
        sa.CheckConstraint("side IN ('FRONT', 'BACK', 'DATA_PAGE', 'PORTRAIT')", name="document_side"),
        # One current capture per side; a recapture replaces the row.
        sa.UniqueConstraint("organization_id", "session_id", "document_id", "side", name="uq_document_images_document_side"),
        sa.Index("ix_document_images_org_delete_after", "organization_id", "delete_after"),
        sa.Index("ix_document_images_org_sha256", "organization_id", "sha256"))


class DocumentField(SessionArtifact, Base):
    __tablename__ = "document_fields"
    document_id: Mapped[UUID] = mapped_column(sa.Uuid)
    field_name: Mapped[str] = mapped_column(sa.String(80))
    raw_value_ciphertext: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    normalized_value_ciphertext: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    key_version: Mapped[str | None] = mapped_column(sa.String(256))
    confidence: Mapped[float] = mapped_column(sa.Float)
    bounding_box: Mapped[list | None] = mapped_column(JSON_VALUE)
    side: Mapped[str | None] = mapped_column(sa.String(20))
    source: Mapped[str] = mapped_column(sa.String(20), default="OCR", server_default="OCR")
    flags: Mapped[list] = mapped_column(JSON_VALUE, default=list, server_default="[]")
    __table_args__ = document_constraints(__tablename__, sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"), sa.CheckConstraint("(raw_value_ciphertext IS NULL AND normalized_value_ciphertext IS NULL) OR key_version IS NOT NULL", name="ciphertext_key_required"),
        sa.CheckConstraint("source IN ('OCR', 'DERIVED', 'MRZ', 'BARCODE', 'NFC')", name="field_source"),
        sa.UniqueConstraint("organization_id", "session_id", "document_id", "field_name", name="uq_document_fields_document_field"))


class DocumentCheck(SessionArtifact, Base):
    __tablename__ = "document_checks"
    document_id: Mapped[UUID] = mapped_column(sa.Uuid)
    check_type: Mapped[str] = mapped_column(sa.String(80))
    result: Mapped[CheckResult] = mapped_column(enum_type(CheckResult))
    evidence_metadata: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    __table_args__ = document_constraints(__tablename__, sa.Index("ix_document_checks_session_type", "organization_id", "session_id", "check_type"))


class MRZResult(SessionArtifact, Base):
    __tablename__ = "mrz_results"
    document_id: Mapped[UUID] = mapped_column(sa.Uuid)
    format: Mapped[str] = mapped_column(sa.String(8))
    mrz_valid: Mapped[bool] = mapped_column(sa.Boolean)
    check_digit_results: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    field_consistency: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    __table_args__ = document_constraints(__tablename__, sa.CheckConstraint("format IN ('TD1', 'TD2', 'TD3', 'UNKNOWN')", name="mrz_format"))


class BarcodeResult(SessionArtifact, Base):
    __tablename__ = "barcode_results"
    document_id: Mapped[UUID] = mapped_column(sa.Uuid)
    symbology: Mapped[str] = mapped_column(sa.String(40))
    decoded: Mapped[bool] = mapped_column(sa.Boolean)
    format_valid: Mapped[bool | None] = mapped_column(sa.Boolean)
    signature_present: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    signature_valid: Mapped[bool | None] = mapped_column(sa.Boolean)
    data_consistency: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    payload_ciphertext: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    key_version: Mapped[str | None] = mapped_column(sa.String(256))
    __table_args__ = document_constraints(__tablename__, sa.CheckConstraint("payload_ciphertext IS NULL OR key_version IS NOT NULL", name="payload_key_required"), sa.CheckConstraint("signature_valid IS NULL OR signature_present", name="signature_presence"))


class NFCResult(SessionArtifact, Base):
    __tablename__ = "nfc_results"
    document_id: Mapped[UUID] = mapped_column(sa.Uuid)
    status: Mapped[NFCStatus] = mapped_column(enum_type(NFCStatus))
    passive_authentication: Mapped[bool | None] = mapped_column(sa.Boolean)
    chip_authentication: Mapped[bool | None] = mapped_column(sa.Boolean)
    trust_store_version: Mapped[str | None] = mapped_column(sa.String(120))
    active_authentication: Mapped[bool | None] = mapped_column(sa.Boolean)
    data_group_checks: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    evidence_metadata: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    __table_args__ = document_constraints(__tablename__)


class NFCChallenge(SessionArtifact, Base):
    """Single-use Active Authentication nonce (not secret; freshness is what matters)."""

    __tablename__ = "nfc_challenges"
    challenge: Mapped[bytes] = mapped_column(sa.LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = artifact_constraints(__tablename__,
        sa.CheckConstraint("length(challenge) = 8", name="challenge_length"),
        sa.Index("ix_nfc_challenges_session_created", "organization_id", "session_id", "created_at"))


class SelfieCapture(SessionArtifact, Base):
    __tablename__ = "selfie_captures"
    encrypted_object_ref: Mapped[str] = mapped_column(sa.String(1024))
    key_version: Mapped[str] = mapped_column(sa.String(256))
    media_type: Mapped[str] = mapped_column(sa.String(40))
    sha256: Mapped[str] = mapped_column(sa.String(64))
    quality_scores: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    quality_policy_version: Mapped[str] = mapped_column(sa.String(80))
    delete_after: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = artifact_constraints(__tablename__,
        sa.UniqueConstraint("organization_id", "session_id", name="uq_selfie_captures_session"),
        sa.Index("ix_selfie_captures_org_delete_after", "organization_id", "delete_after"),
        sa.Index("ix_selfie_captures_org_sha256", "organization_id", "sha256"),
        sa.CheckConstraint("length(sha256) = 64", name="sha256_length"),
    )


class FaceQualityCheck(SessionArtifact, Base):
    __tablename__ = "face_quality_checks"
    source: Mapped[str] = mapped_column(sa.String(32))
    result: Mapped[CheckResult] = mapped_column(enum_type(CheckResult))
    evidence_metadata: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    delete_after: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = artifact_constraints(__tablename__,
        sa.CheckConstraint("source IN ('DOCUMENT_PORTRAIT', 'LIVE_SELFIE')", name="face_quality_source"),
        sa.Index("ix_face_quality_checks_session_source", "organization_id", "session_id", "source", "created_at"),
        sa.Index("ix_face_quality_checks_org_delete_after", "organization_id", "delete_after"),
    )


class BiometricTemplate(SessionArtifact, Base):
    __tablename__ = "biometric_templates"
    source: Mapped[str] = mapped_column(sa.String(32))
    model_name: Mapped[str] = mapped_column(sa.String(120))
    model_version: Mapped[str] = mapped_column(sa.String(120))
    model_sha256: Mapped[str] = mapped_column(sa.String(64), server_default="0" * 64)
    embedding_dimension: Mapped[int] = mapped_column(sa.Integer, server_default="128")
    document_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    template_ciphertext: Mapped[bytes] = mapped_column(sa.LargeBinary)
    key_version: Mapped[str] = mapped_column(sa.String(256))
    delete_after: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = artifact_constraints(__tablename__,
        sa.CheckConstraint("source IN ('DOCUMENT_PORTRAIT', 'CHIP_PORTRAIT', 'LIVE_SELFIE')", name="template_source"),
        sa.CheckConstraint("length(model_sha256) = 64", name="model_sha256_length"),
        sa.CheckConstraint("embedding_dimension BETWEEN 1 AND 4096", name="embedding_dimension"),
        sa.ForeignKeyConstraint(["organization_id", "session_id", "document_id"],
            ["identity_documents.organization_id", "identity_documents.session_id", "identity_documents.id"],
            ondelete="CASCADE", name="fk_biometric_templates_document"),
        sa.Index("ix_biometric_templates_org_delete_after", "organization_id", "delete_after"),
        sa.Index("ix_biometric_templates_reference", "organization_id", "session_id", "source", "document_id"),
    )


class FaceComparison(SessionArtifact, Base):
    __tablename__ = "face_comparisons"
    reference_template_id: Mapped[UUID] = mapped_column(sa.Uuid)
    live_template_id: Mapped[UUID] = mapped_column(sa.Uuid)
    model_name: Mapped[str] = mapped_column(sa.String(120))
    model_version: Mapped[str] = mapped_column(sa.String(120))
    model_sha256: Mapped[str] = mapped_column(sa.String(64), server_default="0" * 64)
    comparison_metric: Mapped[str] = mapped_column(sa.String(40), server_default="COSINE_SIMILARITY")
    evidence_metadata: Mapped[dict] = mapped_column(JSON_VALUE, default=dict, server_default="{}")
    threshold_policy_version: Mapped[str] = mapped_column(sa.String(120))
    comparison_score: Mapped[float] = mapped_column(sa.Float)
    result: Mapped[CheckResult] = mapped_column(enum_type(CheckResult))
    __table_args__ = artifact_constraints(__tablename__,
        sa.CheckConstraint("comparison_metric = 'COSINE_SIMILARITY'", name="comparison_metric"),
        sa.CheckConstraint("comparison_score BETWEEN -1 AND 1", name="cosine_score_range"),
        sa.CheckConstraint("length(model_sha256) = 64", name="model_sha256_length"),
        sa.ForeignKeyConstraint(["organization_id", "session_id", "reference_template_id"], ["biometric_templates.organization_id", "biometric_templates.session_id", "biometric_templates.id"], ondelete="CASCADE", name="fk_face_comparisons_reference_template"),
        sa.ForeignKeyConstraint(["organization_id", "session_id", "live_template_id"], ["biometric_templates.organization_id", "biometric_templates.session_id", "biometric_templates.id"], ondelete="CASCADE", name="fk_face_comparisons_live_template"),
        sa.Index("ix_face_comparisons_reference_scope", "organization_id", "session_id", "reference_template_id"),
        sa.Index("ix_face_comparisons_live_scope", "organization_id", "session_id", "live_template_id"),
    )


class LivenessCheck(SessionArtifact, Base):
    __tablename__ = "liveness_checks"
    method: Mapped[str] = mapped_column(sa.String(24))
    result: Mapped[CheckResult] = mapped_column(enum_type(CheckResult))
    score: Mapped[float | None] = mapped_column(sa.Float)
    attack_type: Mapped[str | None] = mapped_column(sa.String(80))
    model_name: Mapped[str] = mapped_column(sa.String(120))
    model_version: Mapped[str] = mapped_column(sa.String(120))
    challenge_hash: Mapped[str | None] = mapped_column(sa.String(64))
    evidence_reference: Mapped[str | None] = mapped_column(sa.String(1024))
    evidence_metadata: Mapped[dict] = mapped_column(JSON_VALUE, default=dict, server_default="{}")
    __table_args__ = artifact_constraints(__tablename__, sa.CheckConstraint("method IN ('PASSIVE_LIVENESS', 'ACTIVE_LIVENESS')", name="liveness_method"), sa.CheckConstraint("score IS NULL OR score BETWEEN 0 AND 1", name="score_range"))


class LivenessChallenge(SessionArtifact, Base):
    """Single-use active challenge. Only the nonce hash is stored; the nonce goes to the client once."""

    __tablename__ = "liveness_challenges"
    steps: Mapped[list] = mapped_column(JSON_VALUE)
    nonce_hash: Mapped[str] = mapped_column(sa.String(64))
    policy_version: Mapped[str] = mapped_column(sa.String(80))
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = artifact_constraints(__tablename__,
        sa.CheckConstraint("length(nonce_hash) = 64", name="nonce_hash_length"),
        sa.Index("ix_liveness_challenges_session_created", "organization_id", "session_id", "created_at"))


class FraudSignal(SessionArtifact, Base):
    __tablename__ = "fraud_signals"
    signal: Mapped[str] = mapped_column(sa.String(120))
    severity: Mapped[str] = mapped_column(sa.String(8))
    category: Mapped[str] = mapped_column(sa.String(20), default="CONSISTENCY", server_default="CONSISTENCY")
    evidence_metadata: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    __table_args__ = artifact_constraints(__tablename__, sa.CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH')", name="severity_level"),
        sa.CheckConstraint("category IN ('CONSISTENCY', 'TAMPER', 'VALIDITY', 'DUPLICATE', 'METADATA', 'PORTRAIT', 'CONTEXT')",
                           name="signal_category"))


class RiskAssessmentRecord(SessionArtifact, Base):
    __tablename__ = "risk_assessments"
    decision: Mapped[RiskDecision] = mapped_column(enum_type(RiskDecision))
    policy_version: Mapped[str] = mapped_column(sa.String(120))
    reason_codes: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    check_summary: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    __table_args__ = artifact_constraints(__tablename__)


class ManualReview(SessionArtifact, Base):
    __tablename__ = "manual_reviews"
    reviewer_id: Mapped[str] = mapped_column(sa.String(128))
    action: Mapped[ReviewAction] = mapped_column(enum_type(ReviewAction))
    reason_code: Mapped[str] = mapped_column(sa.String(80))
    reason_ciphertext: Mapped[bytes] = mapped_column(sa.LargeBinary)   # the reviewer's note, encrypted
    key_version: Mapped[str] = mapped_column(sa.String(256))
    session_version: Mapped[int | None] = mapped_column(sa.Integer)     # the case version the reviewer saw
    risk_assessment_id: Mapped[UUID | None] = mapped_column(sa.Uuid)    # the assessment being resolved
    __table_args__ = artifact_constraints(__tablename__, sa.CheckConstraint("length(reason_code) > 0", name="reason_required"),
                                          sa.Index("ix_manual_reviews_session_created", "organization_id", "session_id", "created_at"))


class Reviewer(Record, Base):
    """A person allowed to review one organization's cases. Only a SHA-256 of the token is stored."""

    __tablename__ = "reviewers"
    organization_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id", ondelete="RESTRICT"))
    display_name: Mapped[str] = mapped_column(sa.String(120))
    role: Mapped[str] = mapped_column(sa.String(20))
    token_sha256: Mapped[str] = mapped_column(sa.String(64))
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True, server_default=sa.true())
    __table_args__ = (sa.UniqueConstraint("token_sha256", name="uq_reviewers_token_sha256"),
                      sa.UniqueConstraint("organization_id", "id", name="uq_reviewers_scope_id"),
                      sa.CheckConstraint("role IN ('REVIEWER', 'AUDITOR')", name="reviewer_role"),
                      sa.CheckConstraint("length(token_sha256) = 64", name="token_hash_length"))


class ApiKey(Record, Base):
    """An organization's API credential. Only a SHA-256 of the key is stored; scopes limit what it may do."""

    __tablename__ = "api_keys"
    organization_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(sa.String(120))
    key_prefix: Mapped[str] = mapped_column(sa.String(16))
    key_sha256: Mapped[str] = mapped_column(sa.String(64))
    scopes: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(sa.Integer, default=600, server_default="600")
    created_by: Mapped[str] = mapped_column(sa.String(128))
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (sa.UniqueConstraint("key_sha256", name="uq_api_keys_key_sha256"),
                      sa.UniqueConstraint("organization_id", "id", name="uq_api_keys_scope_id"),
                      sa.CheckConstraint("length(key_sha256) = 64", name="key_hash_length"),
                      sa.CheckConstraint("rate_limit_per_minute BETWEEN 1 AND 100000", name="rate_limit_range"),
                      sa.Index("ix_api_keys_org_created", "organization_id", "created_at"))


class WebhookEndpoint(Record, Base):
    """A customer URL that receives signed events. The signing secret is encrypted with the PII keyring."""

    __tablename__ = "webhook_endpoints"
    organization_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id", ondelete="RESTRICT"))
    url: Mapped[str] = mapped_column(sa.String(2048))
    description: Mapped[str | None] = mapped_column(sa.String(200))
    event_types: Mapped[list] = mapped_column(JSON_VALUE, default=list)   # empty: every kyc.* event
    secret_ciphertext: Mapped[bytes] = mapped_column(sa.LargeBinary)
    key_version: Mapped[str] = mapped_column(sa.String(256))
    # During a rotation the previous secret keeps signing until it expires.
    previous_secret_ciphertext: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    previous_key_version: Mapped[str | None] = mapped_column(sa.String(256))
    previous_secret_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True, server_default=sa.true())
    created_by: Mapped[str] = mapped_column(sa.String(128))
    disabled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0")
    __table_args__ = (sa.UniqueConstraint("organization_id", "id", name="uq_webhook_endpoints_scope_id"),
                      sa.CheckConstraint("previous_secret_ciphertext IS NULL OR previous_key_version IS NOT NULL",
                                         name="previous_key_required"),
                      sa.Index("ix_webhook_endpoints_org_active", "organization_id", "active"))


class WebhookDelivery(Record, Base):
    """Transactional outbox row: one event for one endpoint, retried until delivered or abandoned."""

    __tablename__ = "webhook_deliveries"
    organization_id: Mapped[UUID] = mapped_column(sa.Uuid)
    endpoint_id: Mapped[UUID] = mapped_column(sa.Uuid)
    event_id: Mapped[UUID] = mapped_column(sa.Uuid)
    event_type: Mapped[str] = mapped_column(sa.String(64))
    # Like audit logs, a delivery keeps the session reference after the session is purged.
    session_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    status: Mapped[str] = mapped_column(sa.String(16), default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(sa.Integer)
    last_error: Mapped[str | None] = mapped_column(sa.String(40))
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (
        sa.ForeignKeyConstraint(["organization_id", "endpoint_id"], ["webhook_endpoints.organization_id", "webhook_endpoints.id"],
                                ondelete="CASCADE", name="fk_webhook_deliveries_endpoint"),
        sa.UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_deliveries_endpoint_event"),
        sa.CheckConstraint("status IN ('PENDING', 'DELIVERED', 'ABANDONED')", name="delivery_status"),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.Index("ix_webhook_deliveries_due", "organization_id", "status", "next_attempt_at"),
        sa.Index("ix_webhook_deliveries_endpoint_created", "organization_id", "endpoint_id", "created_at"),
    )


class Consent(SessionArtifact, Base):
    __tablename__ = "consents"
    user_id: Mapped[str] = mapped_column(sa.String(128))
    scope: Mapped[str] = mapped_column(sa.String(80))
    policy_version: Mapped[str] = mapped_column(sa.String(80))
    granted: Mapped[bool] = mapped_column(sa.Boolean)
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = artifact_constraints(__tablename__)


class AuditLog(Record, Base):
    __tablename__ = "audit_logs"
    organization_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("organizations.id", ondelete="RESTRICT"))
    # Pseudonymous reference deliberately outlives deletion of a session.
    session_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    actor_id: Mapped[str] = mapped_column(sa.String(128))
    action: Mapped[str] = mapped_column(sa.String(80))
    reason_codes: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    from_status: Mapped[str | None] = mapped_column(sa.String(32))
    to_status: Mapped[str | None] = mapped_column(sa.String(32))
    request_id: Mapped[UUID] = mapped_column(sa.Uuid)
    event_metadata: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    __table_args__ = (sa.Index("ix_audit_logs_org_session_created", "organization_id", "session_id", "created_at"),)
