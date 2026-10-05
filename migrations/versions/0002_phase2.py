"""Phase 2 capture pipeline: capture key/media/policy metadata and one image per side.

Revision ID: 0002_phase2
Revises: 0001_phase1
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_phase2"
down_revision = "0001_phase1"
branch_labels = None
depends_on = None

# Phase 1 had no write path for document_images, so the defaults below only
# satisfy NOT NULL during the ALTER and are dropped immediately.
NEW_IMAGE_COLUMNS = (
    ("key_version", sa.String(length=256), "unknown"),
    ("media_type", sa.String(length=40), "application/octet-stream"),
    ("quality_policy_version", sa.String(length=80), "unknown"),
)


def upgrade() -> None:
    with op.batch_alter_table("document_images") as batch:
        for name, column_type, default in NEW_IMAGE_COLUMNS:
            batch.add_column(sa.Column(name, column_type, nullable=False, server_default=default))
    with op.batch_alter_table("document_images") as batch:
        for name, column_type, _ in NEW_IMAGE_COLUMNS:
            batch.alter_column(name, existing_type=column_type, server_default=None)
        batch.create_unique_constraint("uq_document_images_document_side", ["organization_id", "session_id", "document_id", "side"])
    op.create_index("ix_document_images_org_delete_after", "document_images", ["organization_id", "delete_after"], unique=False)
    op.create_index("ix_document_checks_session_type", "document_checks", ["organization_id", "session_id", "check_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_document_checks_session_type", table_name="document_checks")
    op.drop_index("ix_document_images_org_delete_after", table_name="document_images")
    with op.batch_alter_table("document_images") as batch:
        batch.drop_constraint("uq_document_images_document_side", type_="unique")
        for name, _, _ in reversed(NEW_IMAGE_COLUMNS):
            batch.drop_column(name)
