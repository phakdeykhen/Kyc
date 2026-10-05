BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001_phase1

CREATE TABLE organizations (
    name VARCHAR(200) NOT NULL, 
    pii_retention_days INTEGER DEFAULT '30' NOT NULL, 
    capture_retention_hours INTEGER DEFAULT '24' NOT NULL, 
    template_retention_hours INTEGER DEFAULT '24' NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_organizations PRIMARY KEY (id), 
    CONSTRAINT ck_organizations_retention_positive CHECK (pii_retention_days > 0 AND capture_retention_hours > 0 AND template_retention_hours > 0)
);

CREATE TABLE audit_logs (
    organization_id UUID NOT NULL, 
    session_id UUID, 
    actor_id VARCHAR(128) NOT NULL, 
    action VARCHAR(80) NOT NULL, 
    reason_codes JSONB NOT NULL, 
    from_status VARCHAR(32), 
    to_status VARCHAR(32), 
    request_id UUID NOT NULL, 
    event_metadata JSONB NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_audit_logs PRIMARY KEY (id), 
    CONSTRAINT fk_audit_logs_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
);

CREATE INDEX ix_audit_logs_org_session_created ON audit_logs (organization_id, session_id, created_at);

CREATE TABLE kyc_sessions (
    organization_id UUID NOT NULL, 
    user_id VARCHAR(128) NOT NULL, 
    country VARCHAR(2) NOT NULL, 
    expected_document_type VARCHAR(15) NOT NULL, 
    verification_level VARCHAR(26) NOT NULL, 
    status VARCHAR(19) NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    version INTEGER DEFAULT '1' NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_kyc_sessions PRIMARY KEY (id), 
    CONSTRAINT ck_kyc_sessions_expiry_after_creation CHECK (expires_at > created_at), 
    CONSTRAINT ck_kyc_sessions_country_code CHECK (length(country) = 2 AND country = upper(country)), 
    CONSTRAINT ck_kyc_sessions_version_positive CHECK (version > 0), 
    CONSTRAINT fk_kyc_sessions_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_kyc_sessions_scope_id UNIQUE (organization_id, id), 
    CONSTRAINT ck_kyc_sessions_documenttype CHECK (expected_document_type IN ('KH_NATIONAL_ID', 'KH_PASSPORT', 'KH_NSSF', 'PASSPORT', 'NATIONAL_ID', 'RESIDENCE_CARD', 'DRIVING_LICENSE', 'UNKNOWN')), 
    CONSTRAINT ck_kyc_sessions_verificationlevel CHECK (verification_level IN ('DOCUMENT_ONLY', 'DOCUMENT_FACE', 'DOCUMENT_FACE_LIVENESS', 'DOCUMENT_FACE_LIVENESS_NFC')), 
    CONSTRAINT ck_kyc_sessions_sessionstatus CHECK (status IN ('CREATED', 'DOCUMENT_REQUIRED', 'DOCUMENT_PROCESSING', 'SELFIE_REQUIRED', 'LIVENESS_REQUIRED', 'NFC_REQUIRED', 'PROCESSING', 'MANUAL_REVIEW', 'VERIFIED', 'REJECTED', 'EXPIRED'))
);

CREATE INDEX ix_kyc_sessions_org_expires ON kyc_sessions (organization_id, expires_at);

CREATE INDEX ix_kyc_sessions_org_status_created ON kyc_sessions (organization_id, status, created_at);

CREATE TABLE biometric_templates (
    source VARCHAR(32) NOT NULL, 
    model_name VARCHAR(120) NOT NULL, 
    model_version VARCHAR(120) NOT NULL, 
    template_ciphertext BYTEA NOT NULL, 
    key_version VARCHAR(256) NOT NULL, 
    delete_after TIMESTAMP WITH TIME ZONE NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_biometric_templates PRIMARY KEY (id), 
    CONSTRAINT ck_biometric_templates_template_source CHECK (source IN ('DOCUMENT_PORTRAIT', 'CHIP_PORTRAIT', 'LIVE_SELFIE')), 
    CONSTRAINT fk_biometric_templates_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_biometric_templates_scope_id UNIQUE (organization_id, session_id, id)
);

CREATE TABLE consents (
    user_id VARCHAR(128) NOT NULL, 
    scope VARCHAR(80) NOT NULL, 
    policy_version VARCHAR(80) NOT NULL, 
    granted BOOLEAN NOT NULL, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_consents PRIMARY KEY (id), 
    CONSTRAINT fk_consents_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_consents_scope_id UNIQUE (organization_id, session_id, id)
);

CREATE TABLE fraud_signals (
    signal VARCHAR(120) NOT NULL, 
    severity VARCHAR(8) NOT NULL, 
    evidence_metadata JSONB NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_fraud_signals PRIMARY KEY (id), 
    CONSTRAINT ck_fraud_signals_severity_level CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')), 
    CONSTRAINT fk_fraud_signals_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_fraud_signals_scope_id UNIQUE (organization_id, session_id, id)
);

CREATE TABLE identity_documents (
    document_type VARCHAR(15) NOT NULL, 
    issuing_country VARCHAR(2), 
    document_version VARCHAR(80), 
    document_number_hmac VARCHAR(64), 
    encrypted_identity_ref VARCHAR(1024), 
    classification_confidence FLOAT, 
    delete_after TIMESTAMP WITH TIME ZONE NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_identity_documents PRIMARY KEY (id), 
    CONSTRAINT ck_identity_documents_confidence_range CHECK (classification_confidence IS NULL OR classification_confidence BETWEEN 0 AND 1), 
    CONSTRAINT fk_identity_documents_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_identity_documents_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT ck_identity_documents_documenttype CHECK (document_type IN ('KH_NATIONAL_ID', 'KH_PASSPORT', 'KH_NSSF', 'PASSPORT', 'NATIONAL_ID', 'RESIDENCE_CARD', 'DRIVING_LICENSE', 'UNKNOWN'))
);

CREATE INDEX ix_identity_documents_org_number_hmac ON identity_documents (organization_id, document_number_hmac);

CREATE TABLE liveness_checks (
    method VARCHAR(24) NOT NULL, 
    result VARCHAR(14) NOT NULL, 
    score FLOAT, 
    attack_type VARCHAR(80), 
    model_name VARCHAR(120) NOT NULL, 
    model_version VARCHAR(120) NOT NULL, 
    challenge_hash VARCHAR(64), 
    evidence_reference VARCHAR(1024), 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_liveness_checks PRIMARY KEY (id), 
    CONSTRAINT ck_liveness_checks_liveness_method CHECK (method IN ('PASSIVE_LIVENESS', 'ACTIVE_LIVENESS')), 
    CONSTRAINT ck_liveness_checks_score_range CHECK (score IS NULL OR score BETWEEN 0 AND 1), 
    CONSTRAINT fk_liveness_checks_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_liveness_checks_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT ck_liveness_checks_checkresult CHECK (result IN ('PASS', 'REVIEW', 'FAIL', 'NOT_APPLICABLE', 'UNAVAILABLE'))
);

CREATE TABLE manual_reviews (
    reviewer_id VARCHAR(128) NOT NULL, 
    action VARCHAR(17) NOT NULL, 
    reason_code VARCHAR(80) NOT NULL, 
    reason_ciphertext BYTEA NOT NULL, 
    key_version VARCHAR(256) NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_manual_reviews PRIMARY KEY (id), 
    CONSTRAINT ck_manual_reviews_reason_required CHECK (length(reason_code) > 0), 
    CONSTRAINT fk_manual_reviews_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_manual_reviews_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT ck_manual_reviews_reviewaction CHECK (action IN ('APPROVE', 'REJECT', 'REQUEST_RECAPTURE'))
);

CREATE TABLE risk_assessments (
    decision VARCHAR(6) NOT NULL, 
    policy_version VARCHAR(120) NOT NULL, 
    reason_codes JSONB NOT NULL, 
    check_summary JSONB NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_risk_assessments PRIMARY KEY (id), 
    CONSTRAINT fk_risk_assessments_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_risk_assessments_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT ck_risk_assessments_riskdecision CHECK (decision IN ('PASS', 'REVIEW', 'FAIL'))
);

CREATE TABLE barcode_results (
    document_id UUID NOT NULL, 
    symbology VARCHAR(40) NOT NULL, 
    decoded BOOLEAN NOT NULL, 
    format_valid BOOLEAN, 
    signature_present BOOLEAN NOT NULL, 
    signature_valid BOOLEAN, 
    data_consistency JSONB NOT NULL, 
    payload_ciphertext BYTEA, 
    key_version VARCHAR(256), 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_barcode_results PRIMARY KEY (id), 
    CONSTRAINT ck_barcode_results_payload_key_required CHECK (payload_ciphertext IS NULL OR key_version IS NOT NULL), 
    CONSTRAINT ck_barcode_results_signature_presence CHECK (signature_valid IS NULL OR signature_present), 
    CONSTRAINT fk_barcode_results_organization_id_identity_documents FOREIGN KEY(organization_id, session_id, document_id) REFERENCES identity_documents (organization_id, session_id, id) ON DELETE CASCADE, 
    CONSTRAINT fk_barcode_results_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_barcode_results_scope_id UNIQUE (organization_id, session_id, id)
);

CREATE INDEX ix_barcode_results_document_scope ON barcode_results (organization_id, session_id, document_id);

CREATE TABLE document_checks (
    document_id UUID NOT NULL, 
    check_type VARCHAR(80) NOT NULL, 
    result VARCHAR(14) NOT NULL, 
    evidence_metadata JSONB NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_document_checks PRIMARY KEY (id), 
    CONSTRAINT fk_document_checks_organization_id_identity_documents FOREIGN KEY(organization_id, session_id, document_id) REFERENCES identity_documents (organization_id, session_id, id) ON DELETE CASCADE, 
    CONSTRAINT fk_document_checks_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_document_checks_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT ck_document_checks_checkresult CHECK (result IN ('PASS', 'REVIEW', 'FAIL', 'NOT_APPLICABLE', 'UNAVAILABLE'))
);

CREATE INDEX ix_document_checks_document_scope ON document_checks (organization_id, session_id, document_id);

CREATE TABLE document_fields (
    document_id UUID NOT NULL, 
    field_name VARCHAR(80) NOT NULL, 
    raw_value_ciphertext BYTEA, 
    normalized_value_ciphertext BYTEA, 
    key_version VARCHAR(256), 
    confidence FLOAT NOT NULL, 
    bounding_box JSONB, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_document_fields PRIMARY KEY (id), 
    CONSTRAINT ck_document_fields_ciphertext_key_required CHECK ((raw_value_ciphertext IS NULL AND normalized_value_ciphertext IS NULL) OR key_version IS NOT NULL), 
    CONSTRAINT ck_document_fields_confidence_range CHECK (confidence BETWEEN 0 AND 1), 
    CONSTRAINT fk_document_fields_organization_id_identity_documents FOREIGN KEY(organization_id, session_id, document_id) REFERENCES identity_documents (organization_id, session_id, id) ON DELETE CASCADE, 
    CONSTRAINT fk_document_fields_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_document_fields_scope_id UNIQUE (organization_id, session_id, id)
);

CREATE INDEX ix_document_fields_document_scope ON document_fields (organization_id, session_id, document_id);

CREATE TABLE document_images (
    document_id UUID NOT NULL, 
    side VARCHAR(20) NOT NULL, 
    encrypted_object_ref VARCHAR(1024) NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    quality_scores JSONB NOT NULL, 
    delete_after TIMESTAMP WITH TIME ZONE NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_document_images PRIMARY KEY (id), 
    CONSTRAINT ck_document_images_document_side CHECK (side IN ('FRONT', 'BACK', 'DATA_PAGE', 'PORTRAIT')), 
    CONSTRAINT fk_document_images_organization_id_identity_documents FOREIGN KEY(organization_id, session_id, document_id) REFERENCES identity_documents (organization_id, session_id, id) ON DELETE CASCADE, 
    CONSTRAINT fk_document_images_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_document_images_scope_id UNIQUE (organization_id, session_id, id)
);

CREATE INDEX ix_document_images_document_scope ON document_images (organization_id, session_id, document_id);

CREATE TABLE face_comparisons (
    reference_template_id UUID NOT NULL, 
    live_template_id UUID NOT NULL, 
    model_name VARCHAR(120) NOT NULL, 
    model_version VARCHAR(120) NOT NULL, 
    threshold_policy_version VARCHAR(120) NOT NULL, 
    comparison_score FLOAT NOT NULL, 
    result VARCHAR(14) NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_face_comparisons PRIMARY KEY (id), 
    CONSTRAINT fk_face_comparisons_live_template FOREIGN KEY(organization_id, session_id, live_template_id) REFERENCES biometric_templates (organization_id, session_id, id) ON DELETE CASCADE, 
    CONSTRAINT fk_face_comparisons_reference_template FOREIGN KEY(organization_id, session_id, reference_template_id) REFERENCES biometric_templates (organization_id, session_id, id) ON DELETE CASCADE, 
    CONSTRAINT fk_face_comparisons_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_face_comparisons_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT ck_face_comparisons_checkresult CHECK (result IN ('PASS', 'REVIEW', 'FAIL', 'NOT_APPLICABLE', 'UNAVAILABLE'))
);

CREATE INDEX ix_face_comparisons_live_scope ON face_comparisons (organization_id, session_id, live_template_id);

CREATE INDEX ix_face_comparisons_reference_scope ON face_comparisons (organization_id, session_id, reference_template_id);

CREATE TABLE mrz_results (
    document_id UUID NOT NULL, 
    format VARCHAR(8) NOT NULL, 
    mrz_valid BOOLEAN NOT NULL, 
    check_digit_results JSONB NOT NULL, 
    field_consistency JSONB NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_mrz_results PRIMARY KEY (id), 
    CONSTRAINT ck_mrz_results_mrz_format CHECK (format IN ('TD1', 'TD2', 'TD3', 'UNKNOWN')), 
    CONSTRAINT fk_mrz_results_organization_id_identity_documents FOREIGN KEY(organization_id, session_id, document_id) REFERENCES identity_documents (organization_id, session_id, id) ON DELETE CASCADE, 
    CONSTRAINT fk_mrz_results_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_mrz_results_scope_id UNIQUE (organization_id, session_id, id)
);

CREATE INDEX ix_mrz_results_document_scope ON mrz_results (organization_id, session_id, document_id);

CREATE TABLE nfc_results (
    document_id UUID NOT NULL, 
    status VARCHAR(17) NOT NULL, 
    passive_authentication BOOLEAN, 
    chip_authentication BOOLEAN, 
    trust_store_version VARCHAR(120), 
    data_group_checks JSONB NOT NULL, 
    evidence_metadata JSONB NOT NULL, 
    organization_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP) NOT NULL, 
    CONSTRAINT pk_nfc_results PRIMARY KEY (id), 
    CONSTRAINT fk_nfc_results_organization_id_identity_documents FOREIGN KEY(organization_id, session_id, document_id) REFERENCES identity_documents (organization_id, session_id, id) ON DELETE CASCADE, 
    CONSTRAINT fk_nfc_results_organization_id_kyc_sessions FOREIGN KEY(organization_id, session_id) REFERENCES kyc_sessions (organization_id, id) ON DELETE CASCADE, 
    CONSTRAINT uq_nfc_results_scope_id UNIQUE (organization_id, session_id, id), 
    CONSTRAINT ck_nfc_results_nfcstatus CHECK (status IN ('NFC_NOT_SUPPORTED', 'NFC_NOT_AVAILABLE', 'NFC_FAILED', 'NFC_READ', 'NFC_VERIFIED'))
);

CREATE INDEX ix_nfc_results_document_scope ON nfc_results (organization_id, session_id, document_id);

ALTER TABLE "organizations" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "organizations" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "organizations" USING (id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "kyc_sessions" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "kyc_sessions" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "kyc_sessions" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "identity_documents" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "identity_documents" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "identity_documents" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "document_images" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "document_images" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "document_images" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "document_fields" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "document_fields" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "document_fields" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "document_checks" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "document_checks" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "document_checks" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "mrz_results" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "mrz_results" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "mrz_results" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "barcode_results" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "barcode_results" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "barcode_results" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "nfc_results" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "nfc_results" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "nfc_results" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "biometric_templates" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "biometric_templates" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "biometric_templates" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "face_comparisons" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "face_comparisons" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "face_comparisons" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "liveness_checks" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "liveness_checks" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "liveness_checks" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "fraud_signals" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "fraud_signals" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "fraud_signals" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "risk_assessments" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "risk_assessments" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "risk_assessments" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "manual_reviews" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "manual_reviews" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "manual_reviews" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "consents" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "consents" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "consents" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

ALTER TABLE "audit_logs" ENABLE ROW LEVEL SECURITY;

ALTER TABLE "audit_logs" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "audit_logs" USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid) WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

INSERT INTO alembic_version (version_num) VALUES ('0001_phase1') RETURNING alembic_version.version_num;

COMMIT;

