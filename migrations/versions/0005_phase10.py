"""Phase 10: single-use liveness challenges and liveness evidence metadata.

Revision ID: 0005_phase10
Revises: 0004_phase8_9
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_phase10"
down_revision = "0004_phase8_9"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    op.create_table(
        "liveness_challenges",
        sa.Column("steps", JSON_VALUE, nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("length(nonce_hash) = 64", name=op.f("ck_liveness_challenges_nonce_hash_length")),
        sa.ForeignKeyConstraint(["organization_id", "session_id"], ["kyc_sessions.organization_id", "kyc_sessions.id"],
                                name=op.f("fk_liveness_challenges_organization_id_kyc_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_liveness_challenges")),
        sa.UniqueConstraint("organization_id", "session_id", "id", name="uq_liveness_challenges_scope_id"),
    )
    op.create_index("ix_liveness_challenges_session_created", "liveness_challenges",
                    ["organization_id", "session_id", "created_at"], unique=False)
    with op.batch_alter_table("liveness_checks") as batch:
        batch.add_column(sa.Column("evidence_metadata", JSON_VALUE, nullable=False, server_default="{}"))
    if op.get_bind().dialect.name == "postgresql":
        scope = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        op.execute('ALTER TABLE "liveness_challenges" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "liveness_challenges" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation ON "liveness_challenges" USING ({scope}) WITH CHECK ({scope})')


def downgrade():
    with op.batch_alter_table("liveness_checks") as batch:
        batch.drop_column("evidence_metadata")
    op.drop_index("ix_liveness_challenges_session_created", table_name="liveness_challenges")
    op.drop_table("liveness_challenges")
