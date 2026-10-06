"""Phase 15: per-organization API keys, idempotent session creation, tenant suspension and rate limits.

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

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TABLES = ("api_keys", "idempotency_keys")


def upgrade():
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False))
        batch.add_column(sa.Column("api_rate_limit_per_minute", sa.Integer(), server_default="120", nullable=False))
        batch.create_check_constraint(op.f("ck_organizations_rate_limit_range"), "api_rate_limit_per_minute BETWEEN 1 AND 100000")
    op.create_table(
        "api_keys",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("secret_sha256", sa.String(length=64), nullable=False),
        sa.Column("scopes", JSON_VALUE, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("length(secret_sha256) = 64", name=op.f("ck_api_keys_secret_hash_length")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name=op.f("fk_api_keys_organization_id_organizations"),
                                ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_prefix", name="uq_api_keys_key_prefix"),
        sa.UniqueConstraint("organization_id", "id", name="uq_api_keys_scope_id"),
    )
    op.create_index("ix_api_keys_org_created", "api_keys", ["organization_id", "created_at"])
    op.create_table(
        "idempotency_keys",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name=op.f("fk_idempotency_keys_organization_id_organizations"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "session_id"], ["kyc_sessions.organization_id", "kyc_sessions.id"],
                                name=op.f("fk_idempotency_keys_organization_id_kyc_sessions"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_idempotency_keys_org_key"),
    )
    op.create_index("ix_idempotency_keys_org_created", "idempotency_keys", ["organization_id", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        scope = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        for table in TABLES:
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation ON "{table}" USING ({scope}) WITH CHECK ({scope})')


def downgrade():
    op.drop_index("ix_idempotency_keys_org_created", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_api_keys_org_created", table_name="api_keys")
    op.drop_table("api_keys")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_constraint(op.f("ck_organizations_rate_limit_range"), type_="check")
        batch.drop_column("api_rate_limit_per_minute")
        batch.drop_column("active")
