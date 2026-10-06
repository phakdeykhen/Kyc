"""Phase 17: API key network allow-lists, reviewer token expiry, and erasure markers.

Revision ID: 0011_phase17
Revises: 0010_phase16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_phase17"
down_revision = "0010_phase16"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    with op.batch_alter_table("api_keys") as batch:
        batch.add_column(sa.Column("allowed_cidrs", JSON, server_default="[]", nullable=False))
    with op.batch_alter_table("reviewers") as batch:
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("kyc_sessions") as batch:
        batch.add_column(sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True))
    # Data-subject requests find every session of one integrator user reference.
    op.create_index("ix_kyc_sessions_org_user", "kyc_sessions", ["organization_id", "user_id"])


def downgrade():
    op.drop_index("ix_kyc_sessions_org_user", table_name="kyc_sessions")
    with op.batch_alter_table("kyc_sessions") as batch:
        batch.drop_column("erased_at")
    with op.batch_alter_table("reviewers") as batch:
        batch.drop_column("expires_at")
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("allowed_cidrs")
