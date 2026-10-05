BEGIN;

-- Running upgrade 0003_phase3 -> 0004_phase8_9

CREATE TABLE selfie_captures (
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    encrypted_object_ref VARCHAR(1024) NOT NULL, 
    key_version VARCHAR(256) NOT NULL, 
    media_type VARCHAR(40) NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    quality_scores JSONB NOT NULL, 
    quality_policy_version VARCHAR(80) NOT NULL, 
    delete_after TIMESTAMP WITH TIME ZONE NOT NULL, 
    CONSTRAINT pk_selfie_captures PRIMARY KEY (id), 
    CONSTRAINT fk_selfie_captures_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_selfie_captures_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT uq_selfie_captures_session UNIQUE (organization_id, session_id), 
    CONSTRAINT ck_selfie_captures_sha256_length CHECK (length(sha256) = 64)
);

CREATE INDEX ix_selfie_captures_org_delete_after ON selfie_captures (organization_id, delete_after);

CREATE TABLE face_quality_checks (
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    source VARCHAR(32) NOT NULL, 
    result VARCHAR(14) NOT NULL, 
    evidence_metadata JSONB NOT NULL, 
    delete_after TIMESTAMP WITH TIME ZONE NOT NULL, 
    CONSTRAINT pk_face_quality_checks PRIMARY KEY (id), 
    CONSTRAINT fk_face_quality_checks_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_face_quality_checks_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT ck_face_quality_checks_face_quality_source CHECK (source IN ('DOCUMENT_PORTRAIT', 'LIVE_SELFIE')), 
    CONSTRAINT ck_face_quality_checks_checkresult CHECK (result IN ('PASS', 'REVIEW', 'FAIL', 'NOT_APPLICABLE', 'UNAVAILABLE'))
);

CREATE INDEX ix_face_quality_checks_session_source ON face_quality_checks (organization_id, session_id, source, created_at);

CREATE INDEX ix_face_quality_checks_org_delete_after ON face_quality_checks (organization_id, delete_after);

ALTER TABLE biometric_templates ADD COLUMN model_sha256 VARCHAR(64) DEFAULT '0000000000000000000000000000000000000000000000000000000000000000' NOT NULL;

ALTER TABLE biometric_templates ADD COLUMN embedding_dimension INTEGER DEFAULT '128' NOT NULL;

ALTER TABLE biometric_templates ADD COLUMN document_id UUID;

ALTER TABLE biometric_templates ADD CONSTRAINT ck_biometric_templates_model_sha256_length CHECK (length(model_sha256) = 64);

ALTER TABLE biometric_templates ADD CONSTRAINT ck_biometric_templates_embedding_dimension CHECK (embedding_dimension BETWEEN 1 AND 4096);

ALTER TABLE biometric_templates ADD CONSTRAINT fk_biometric_templates_document FOREIGN KEY(organization_id, session_id, document_id) REFERENCES identity_documents (organization_id, session_id, id) ON DELETE CASCADE;

CREATE INDEX ix_biometric_templates_org_delete_after ON biometric_templates (organization_id, delete_after);

CREATE INDEX ix_biometric_templates_reference ON biometric_templates (organization_id, session_id, source, document_id);

ALTER TABLE face_comparisons ADD COLUMN model_sha256 VARCHAR(64) DEFAULT '0000000000000000000000000000000000000000000000000000000000000000' NOT NULL;

ALTER TABLE face_comparisons ADD COLUMN comparison_metric VARCHAR(40) DEFAULT 'COSINE_SIMILARITY' NOT NULL;

ALTER TABLE face_comparisons ADD COLUMN evidence_metadata JSONB DEFAULT '{}' NOT NULL;

ALTER TABLE face_comparisons ADD CONSTRAINT ck_face_comparisons_model_sha256_length CHECK (length(model_sha256) = 64);

ALTER TABLE face_comparisons ADD CONSTRAINT ck_face_comparisons_comparison_metric CHECK (comparison_metric = 'COSINE_SIMILARITY');

ALTER TABLE face_comparisons ADD CONSTRAINT ck_face_comparisons_cosine_score_range CHECK (comparison_score BETWEEN -1 AND 1);

ALTER TABLE "selfie_captures" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "selfie_captures" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "selfie_captures" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "face_quality_checks" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "face_quality_checks" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "face_quality_checks" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

UPDATE alembic_version SET version_num='0004_phase8_9' WHERE alembic_version.version_num = '0003_phase3';

COMMIT;

