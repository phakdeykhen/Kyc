"""Phases 8-9: face quality, encrypted selfie captures and biometric provenance.

Revision ID: 0004_phase8_9
Revises: 0003_phase3
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_phase8_9"
down_revision = "0003_phase3"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ZERO_HASH = "0" * 64


def _scope(table):
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id", "session_id"], ["kyc_sessions.organization_id", "kyc_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "session_id", "id", name=f"uq_{table}_scope_id"),
    )


def upgrade():
    op.create_table("selfie_captures", *_scope("selfie_captures"),
        sa.Column("encrypted_object_ref", sa.String(1024), nullable=False),
        sa.Column("key_version", sa.String(256), nullable=False),
        sa.Column("media_type", sa.String(40), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("quality_scores", JSON_VALUE, nullable=False),
        sa.Column("quality_policy_version", sa.String(80), nullable=False),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "session_id", name="uq_selfie_captures_session"),
        sa.CheckConstraint("length(sha256) = 64", name="sha256_length"),
    )
    op.create_index("ix_selfie_captures_org_delete_after", "selfie_captures", ["organization_id", "delete_after"])
    op.create_table("face_quality_checks", *_scope("face_quality_checks"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("result", sa.Enum("PASS", "REVIEW", "FAIL", "NOT_APPLICABLE", "UNAVAILABLE",
                                   name="checkresult", native_enum=False, create_constraint=True), nullable=False),
        sa.Column("evidence_metadata", JSON_VALUE, nullable=False),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('DOCUMENT_PORTRAIT', 'LIVE_SELFIE')", name="face_quality_source"),
    )
    op.create_index("ix_face_quality_checks_session_source", "face_quality_checks", ["organization_id", "session_id", "source", "created_at"])
    op.create_index("ix_face_quality_checks_org_delete_after", "face_quality_checks", ["organization_id", "delete_after"])
    with op.batch_alter_table("biometric_templates") as batch:
        batch.add_column(sa.Column("model_sha256", sa.String(64), nullable=False, server_default=ZERO_HASH))
        batch.add_column(sa.Column("embedding_dimension", sa.Integer(), nullable=False, server_default="128"))
        batch.add_column(sa.Column("document_id", sa.Uuid(), nullable=True))
        batch.create_check_constraint("model_sha256_length", "length(model_sha256) = 64")
        batch.create_check_constraint("embedding_dimension", "embedding_dimension BETWEEN 1 AND 4096")
        batch.create_foreign_key("fk_biometric_templates_document", "identity_documents",
                                ["organization_id", "session_id", "document_id"], ["organization_id", "session_id", "id"], ondelete="CASCADE")
    op.create_index("ix_biometric_templates_org_delete_after", "biometric_templates", ["organization_id", "delete_after"])
    op.create_index("ix_biometric_templates_reference", "biometric_templates", ["organization_id", "session_id", "source", "document_id"])
    with op.batch_alter_table("face_comparisons") as batch:
        batch.add_column(sa.Column("model_sha256", sa.String(64), nullable=False, server_default=ZERO_HASH))
        batch.add_column(sa.Column("comparison_metric", sa.String(40), nullable=False, server_default="COSINE_SIMILARITY"))
        batch.add_column(sa.Column("evidence_metadata", JSON_VALUE, nullable=False, server_default="{}"))
        batch.create_check_constraint("model_sha256_length", "length(model_sha256) = 64")
        batch.create_check_constraint("comparison_metric", "comparison_metric = 'COSINE_SIMILARITY'")
        batch.create_check_constraint("cosine_score_range", "comparison_score BETWEEN -1 AND 1")
    if op.get_bind().dialect.name == "postgresql":
        scope = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        for table in ("selfie_captures", "face_quality_checks"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation ON "{table}" USING ({scope}) WITH CHECK ({scope})')


def downgrade():
    with op.batch_alter_table("face_comparisons") as batch:
        for constraint in ("cosine_score_range", "comparison_metric", "model_sha256_length"):
            batch.drop_constraint(op.f(f"ck_face_comparisons_{constraint}"), type_="check")
        for column in ("evidence_metadata", "comparison_metric", "model_sha256"):
            batch.drop_column(column)
    op.drop_index("ix_biometric_templates_reference", table_name="biometric_templates")
    op.drop_index("ix_biometric_templates_org_delete_after", table_name="biometric_templates")
    with op.batch_alter_table("biometric_templates") as batch:
        batch.drop_constraint("fk_biometric_templates_document", type_="foreignkey")
        for constraint in ("embedding_dimension", "model_sha256_length"):
            batch.drop_constraint(op.f(f"ck_biometric_templates_{constraint}"), type_="check")
        for column in ("document_id", "embedding_dimension", "model_sha256"):
            batch.drop_column(column)
    op.drop_index("ix_face_quality_checks_org_delete_after", table_name="face_quality_checks")
    op.drop_index("ix_face_quality_checks_session_source", table_name="face_quality_checks")
    op.drop_table("face_quality_checks")
    op.drop_index("ix_selfie_captures_org_delete_after", table_name="selfie_captures")
    op.drop_table("selfie_captures")
