"""Phase 11: ePassport Active Authentication challenges and NFC result fields.

Revision ID: 0006_phase11
Revises: 0005_phase10
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_phase11"
down_revision = "0005_phase10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nfc_challenges",
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("length(challenge) = 8", name=op.f("ck_nfc_challenges_challenge_length")),
        sa.ForeignKeyConstraint(["organization_id", "session_id"], ["kyc_sessions.organization_id", "kyc_sessions.id"],
                                name=op.f("fk_nfc_challenges_organization_id_kyc_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_nfc_challenges")),
        sa.UniqueConstraint("organization_id", "session_id", "id", name="uq_nfc_challenges_scope_id"),
    )
    op.create_index("ix_nfc_challenges_session_created", "nfc_challenges", ["organization_id", "session_id", "created_at"], unique=False)
    with op.batch_alter_table("nfc_results") as batch:
        batch.add_column(sa.Column("active_authentication", sa.Boolean(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        scope = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        op.execute('ALTER TABLE "nfc_challenges" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "nfc_challenges" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation ON "nfc_challenges" USING ({scope}) WITH CHECK ({scope})')


def downgrade():
    with op.batch_alter_table("nfc_results") as batch:
        batch.drop_column("active_authentication")
    op.drop_index("ix_nfc_challenges_session_created", table_name="nfc_challenges")
    op.drop_table("nfc_challenges")
