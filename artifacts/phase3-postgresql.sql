BEGIN;

-- Running upgrade 0002_phase2 -> 0003_phase3

ALTER TABLE document_fields ADD COLUMN side VARCHAR(20);

ALTER TABLE document_fields ADD COLUMN source VARCHAR(20) DEFAULT 'OCR' NOT NULL;

ALTER TABLE document_fields ADD COLUMN flags JSONB DEFAULT '[]' NOT NULL;

ALTER TABLE document_fields ADD CONSTRAINT ck_document_fields_field_source CHECK (source IN ('OCR', 'DERIVED', 'MRZ', 'BARCODE', 'NFC'));

ALTER TABLE document_fields ADD CONSTRAINT uq_document_fields_document_field UNIQUE (organization_id, session_id, document_id, field_name);

ALTER TABLE identity_documents ADD COLUMN side_classification JSONB DEFAULT '{}' NOT NULL;

ALTER TABLE identity_documents ADD COLUMN extraction_version VARCHAR(200);

ALTER TABLE identity_documents ADD COLUMN processed_at TIMESTAMP WITH TIME ZONE;

UPDATE alembic_version SET version_num='0003_phase3' WHERE alembic_version.version_num = '0002_phase2';

COMMIT;

