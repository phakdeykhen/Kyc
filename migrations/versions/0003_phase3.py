"""Phase 3 document extraction: field provenance/flags and document processing metadata.

Revision ID: 0003_phase3
Revises: 0002_phase2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_phase3"
down_revision = "0002_phase2"
branch_labels = None
depends_on = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # Server defaults stay so direct SQL writers (migrations, tests, tools) get safe values.
    with op.batch_alter_table("document_fields") as batch:
        batch.add_column(sa.Column("side", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("source", sa.String(length=20), nullable=False, server_default="OCR"))
        batch.add_column(sa.Column("flags", JSON_VALUE, nullable=False, server_default="[]"))
    with op.batch_alter_table("document_fields") as batch:
        batch.create_check_constraint("field_source", "source IN ('OCR', 'DERIVED', 'MRZ', 'BARCODE', 'NFC')")
        batch.create_unique_constraint("uq_document_fields_document_field", ["organization_id", "session_id", "document_id", "field_name"])
    with op.batch_alter_table("identity_documents") as batch:
        batch.add_column(sa.Column("side_classification", JSON_VALUE, nullable=False, server_default="{}"))
        batch.add_column(sa.Column("extraction_version", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("identity_documents") as batch:
        for name in ("processed_at", "extraction_version", "side_classification"):
            batch.drop_column(name)
    with op.batch_alter_table("document_fields") as batch:
        batch.drop_constraint("uq_document_fields_document_field", type_="unique")
        batch.drop_constraint(op.f("ck_document_fields_field_source"), type_="check")
        for name in ("flags", "source", "side"):
            batch.drop_column(name)
