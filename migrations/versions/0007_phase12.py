"""Phase 12: fraud signal categories and duplicate-capture lookups.

Revision ID: 0007_phase12
Revises: 0006_phase11
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_phase12"
down_revision = "0006_phase11"
branch_labels = None
depends_on = None

CATEGORIES = "'CONSISTENCY', 'TAMPER', 'VALIDITY', 'DUPLICATE', 'METADATA', 'PORTRAIT', 'CONTEXT'"


def upgrade():
    with op.batch_alter_table("fraud_signals") as batch:
        batch.add_column(sa.Column("category", sa.String(length=20), server_default="CONSISTENCY", nullable=False))
        batch.create_check_constraint(op.f("ck_fraud_signals_signal_category"), f"category IN ({CATEGORIES})")
    op.create_index("ix_document_images_org_sha256", "document_images", ["organization_id", "sha256"], unique=False)
    op.create_index("ix_selfie_captures_org_sha256", "selfie_captures", ["organization_id", "sha256"], unique=False)


def downgrade():
    op.drop_index("ix_selfie_captures_org_sha256", table_name="selfie_captures")
    op.drop_index("ix_document_images_org_sha256", table_name="document_images")
    with op.batch_alter_table("fraud_signals") as batch:
        batch.drop_constraint(op.f("ck_fraud_signals_signal_category"), type_="check")
        batch.drop_column("category")
