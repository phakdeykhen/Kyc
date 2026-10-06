"""Phase 15: tenant API keys, organization suspension, idempotent sessions and session client tokens.

Revision ID: 0009_phase15
Revises: 0008_phase14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_phase15"
down_revision = "0008_phase14"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_keys",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_sha256", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), server_default="600", nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("length(key_sha256) = 64", name=op.f("ck_api_keys_key_hash_length")),
        sa.CheckConstraint("rate_limit_per_minute BETWEEN 1 AND 100000", name=op.f("ck_api_keys_rate_limit_range")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_api_keys_organization_id_organizations"),
                                ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_sha256", name="uq_api_keys_key_sha256"),
        sa.UniqueConstraint("organization_id", "id", name="uq_api_keys_scope_id"),
    )
    op.create_index("ix_api_keys_org_created", "api_keys", ["organization_id", "created_at"])
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False))
    with op.batch_alter_table("kyc_sessions") as batch:
        batch.add_column(sa.Column("created_by", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("client_token_sha256", sa.String(length=64), nullable=True))
        batch.create_unique_constraint("uq_kyc_sessions_idempotency_key", ["organization_id", "idempotency_key"])
    if op.get_bind().dialect.name == "postgresql":
        scope = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        op.execute('ALTER TABLE "api_keys" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "api_keys" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_isolation ON "api_keys" USING ({scope}) WITH CHECK ({scope})')


def downgrade():
    with op.batch_alter_table("kyc_sessions") as batch:
        batch.drop_constraint("uq_kyc_sessions_idempotency_key", type_="unique")
        batch.drop_column("client_token_sha256")
        batch.drop_column("request_fingerprint")
        batch.drop_column("idempotency_key")
        batch.drop_column("created_by")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_column("active")
    op.drop_index("ix_api_keys_org_created", table_name="api_keys")
    op.drop_table("api_keys")
