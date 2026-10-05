"""Phase 14: reviewer accounts and review decision context.

Revision ID: 0008_phase14
Revises: 0007_phase12
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_phase14"
down_revision = "0007_phase12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reviewers",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("role IN ('REVIEWER', 'AUDITOR')", name=op.f("ck_reviewers_reviewer_role")),
        sa.CheckConstraint("length(token_sha256) = 64", name=op.f("ck_reviewers_token_hash_length")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_reviewers_organization_id_organizations"),
                                ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviewers")),
        sa.UniqueConstraint("token_sha256", name="uq_reviewers_token_sha256"),
        sa.UniqueConstraint("organization_id", "id", name="uq_reviewers_scope_id"),
    )
    with op.batch_alter_table("manual_reviews") as batch:
        batch.add_column(sa.Column("session_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("risk_assessment_id", sa.Uuid(), nullable=True))
    op.create_index("ix_manual_reviews_session_created", "manual_reviews", ["organization_id", "session_id", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        scope = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        op.execute('ALTER TABLE "reviewers" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "reviewers" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation ON "reviewers" USING ({scope}) WITH CHECK ({scope})')


def downgrade():
    op.drop_index("ix_manual_reviews_session_created", table_name="manual_reviews")
    with op.batch_alter_table("manual_reviews") as batch:
        batch.drop_column("risk_assessment_id")
        batch.drop_column("session_version")
    op.drop_table("reviewers")
