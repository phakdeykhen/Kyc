"""Phase 16: webhook endpoints and the delivery outbox.

Revision ID: 0010_phase16
Revises: 0009_phase15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_phase16"
down_revision = "0009_phase15"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    op.create_table(
        "webhook_endpoints",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("event_types", JSON, nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.String(length=256), nullable=False),
        sa.Column("previous_secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("previous_key_version", sa.String(length=256), nullable=True),
        sa.Column("previous_secret_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("previous_secret_ciphertext IS NULL OR previous_key_version IS NOT NULL",
                           name=op.f("ck_webhook_endpoints_previous_key_required")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"],
                                name=op.f("fk_webhook_endpoints_organization_id_organizations"), ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_endpoints")),
        sa.UniqueConstraint("organization_id", "id", name="uq_webhook_endpoints_scope_id"),
    )
    op.create_index("ix_webhook_endpoints_org_active", "webhook_endpoints", ["organization_id", "active"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("payload", JSON, nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=40), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint("status IN ('PENDING', 'DELIVERED', 'ABANDONED')", name=op.f("ck_webhook_deliveries_delivery_status")),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_webhook_deliveries_attempts_non_negative")),
        sa.ForeignKeyConstraint(["organization_id", "endpoint_id"], ["webhook_endpoints.organization_id", "webhook_endpoints.id"],
                                name="fk_webhook_deliveries_endpoint", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
        sa.UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_deliveries_endpoint_event"),
    )
    op.create_index("ix_webhook_deliveries_due", "webhook_deliveries", ["organization_id", "status", "next_attempt_at"])
    op.create_index("ix_webhook_deliveries_endpoint_created", "webhook_deliveries",
                    ["organization_id", "endpoint_id", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        scope = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        for table in ("webhook_endpoints", "webhook_deliveries"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation ON "{table}" USING ({scope}) WITH CHECK ({scope})')


def downgrade():
    op.drop_index("ix_webhook_deliveries_endpoint_created", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_due", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_endpoints_org_active", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
