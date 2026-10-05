BEGIN;

-- Running upgrade 0001_phase1 -> 0002_phase2

ALTER TABLE document_images ADD COLUMN key_version VARCHAR(256) DEFAULT 'unknown' NOT NULL;

ALTER TABLE document_images ADD COLUMN media_type VARCHAR(40) DEFAULT 'application/octet-stream' NOT NULL;

ALTER TABLE document_images ADD COLUMN quality_policy_version VARCHAR(80) DEFAULT 'unknown' NOT NULL;

ALTER TABLE document_images ALTER COLUMN key_version DROP DEFAULT;

ALTER TABLE document_images ALTER COLUMN media_type DROP DEFAULT;

ALTER TABLE document_images ALTER COLUMN quality_policy_version DROP DEFAULT;

ALTER TABLE document_images ADD CONSTRAINT uq_document_images_document_side UNIQUE (organization_id, session_id, document_id, side);

CREATE INDEX ix_document_images_org_delete_after ON document_images (organization_id, delete_after);

CREATE INDEX ix_document_checks_session_type ON document_checks (organization_id, session_id, check_type);

UPDATE alembic_version SET version_num='0002_phase2' WHERE alembic_version.version_num = '0001_phase1';

COMMIT;

