"""Phase 1 frozen identity-platform schema and PostgreSQL tenant policies.

Revision ID: 0001_phase1
Revises: None
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_phase1"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = ('organizations', 'kyc_sessions', 'identity_documents', 'document_images', 'document_fields', 'document_checks', 'mrz_results', 'barcode_results', 'nfc_results', 'biometric_templates', 'face_comparisons', 'liveness_checks', 'fraud_signals', 'risk_assessments', 'manual_reviews', 'consents', 'audit_logs')


def upgrade() -> None:
    # Frozen Phase 1 DDL; append future migrations instead of importing live models.
    op.create_table('organizations',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('pii_retention_days', sa.Integer(), server_default='30', nullable=False),
    sa.Column('capture_retention_hours', sa.Integer(), server_default='24', nullable=False),
    sa.Column('template_retention_hours', sa.Integer(), server_default='24', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('pii_retention_days > 0 AND capture_retention_hours > 0 AND template_retention_hours > 0', name=op.f('ck_organizations_retention_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations'))
    )
    op.create_table('audit_logs',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=True),
    sa.Column('actor_id', sa.String(length=128), nullable=False),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('reason_codes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('from_status', sa.String(length=32), nullable=True),
    sa.Column('to_status', sa.String(length=32), nullable=True),
    sa.Column('request_id', sa.Uuid(), nullable=False),
    sa.Column('event_metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_audit_logs_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index('ix_audit_logs_org_session_created', 'audit_logs', ['organization_id', 'session_id', 'created_at'], unique=False)
    op.create_table('kyc_sessions',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('expected_document_type', sa.Enum('KH_NATIONAL_ID', 'KH_PASSPORT', 'KH_NSSF', 'PASSPORT', 'NATIONAL_ID', 'RESIDENCE_CARD', 'DRIVING_LICENSE', 'UNKNOWN', name='documenttype', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('verification_level', sa.Enum('DOCUMENT_ONLY', 'DOCUMENT_FACE', 'DOCUMENT_FACE_LIVENESS', 'DOCUMENT_FACE_LIVENESS_NFC', name='verificationlevel', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('status', sa.Enum('CREATED', 'DOCUMENT_REQUIRED', 'DOCUMENT_PROCESSING', 'SELFIE_REQUIRED', 'LIVENESS_REQUIRED', 'NFC_REQUIRED', 'PROCESSING', 'MANUAL_REVIEW', 'VERIFIED', 'REJECTED', 'EXPIRED', name='sessionstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_kyc_sessions_expiry_after_creation')),
    sa.CheckConstraint('length(country) = 2 AND country = upper(country)', name=op.f('ck_kyc_sessions_country_code')),
    sa.CheckConstraint('version > 0', name=op.f('ck_kyc_sessions_version_positive')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_kyc_sessions_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kyc_sessions')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_kyc_sessions_scope_id')
    )
    op.create_index('ix_kyc_sessions_org_expires', 'kyc_sessions', ['organization_id', 'expires_at'], unique=False)
    op.create_index('ix_kyc_sessions_org_status_created', 'kyc_sessions', ['organization_id', 'status', 'created_at'], unique=False)
    op.create_table('biometric_templates',
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('model_name', sa.String(length=120), nullable=False),
    sa.Column('model_version', sa.String(length=120), nullable=False),
    sa.Column('template_ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('key_version', sa.String(length=256), nullable=False),
    sa.Column('delete_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("source IN ('DOCUMENT_PORTRAIT', 'CHIP_PORTRAIT', 'LIVE_SELFIE')", name=op.f('ck_biometric_templates_template_source')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_biometric_templates_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_biometric_templates')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_biometric_templates_scope_id')
    )
    op.create_table('consents',
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('scope', sa.String(length=80), nullable=False),
    sa.Column('policy_version', sa.String(length=80), nullable=False),
    sa.Column('granted', sa.Boolean(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_consents_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consents')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_consents_scope_id')
    )
    op.create_table('fraud_signals',
    sa.Column('signal', sa.String(length=120), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('evidence_metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("severity IN ('LOW', 'MEDIUM', 'HIGH')", name=op.f('ck_fraud_signals_severity_level')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_fraud_signals_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fraud_signals')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_fraud_signals_scope_id')
    )
    op.create_table('identity_documents',
    sa.Column('document_type', sa.Enum('KH_NATIONAL_ID', 'KH_PASSPORT', 'KH_NSSF', 'PASSPORT', 'NATIONAL_ID', 'RESIDENCE_CARD', 'DRIVING_LICENSE', 'UNKNOWN', name='documenttype', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('issuing_country', sa.String(length=2), nullable=True),
    sa.Column('document_version', sa.String(length=80), nullable=True),
    sa.Column('document_number_hmac', sa.String(length=64), nullable=True),
    sa.Column('encrypted_identity_ref', sa.String(length=1024), nullable=True),
    sa.Column('classification_confidence', sa.Float(), nullable=True),
    sa.Column('delete_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('classification_confidence IS NULL OR classification_confidence BETWEEN 0 AND 1', name=op.f('ck_identity_documents_confidence_range')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_identity_documents_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_identity_documents')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_identity_documents_scope_id')
    )
    op.create_index('ix_identity_documents_org_number_hmac', 'identity_documents', ['organization_id', 'document_number_hmac'], unique=False)
    op.create_table('liveness_checks',
    sa.Column('method', sa.String(length=24), nullable=False),
    sa.Column('result', sa.Enum('PASS', 'REVIEW', 'FAIL', 'NOT_APPLICABLE', 'UNAVAILABLE', name='checkresult', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('attack_type', sa.String(length=80), nullable=True),
    sa.Column('model_name', sa.String(length=120), nullable=False),
    sa.Column('model_version', sa.String(length=120), nullable=False),
    sa.Column('challenge_hash', sa.String(length=64), nullable=True),
    sa.Column('evidence_reference', sa.String(length=1024), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("method IN ('PASSIVE_LIVENESS', 'ACTIVE_LIVENESS')", name=op.f('ck_liveness_checks_liveness_method')),
    sa.CheckConstraint('score IS NULL OR score BETWEEN 0 AND 1', name=op.f('ck_liveness_checks_score_range')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_liveness_checks_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_liveness_checks')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_liveness_checks_scope_id')
    )
    op.create_table('manual_reviews',
    sa.Column('reviewer_id', sa.String(length=128), nullable=False),
    sa.Column('action', sa.Enum('APPROVE', 'REJECT', 'REQUEST_RECAPTURE', name='reviewaction', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('reason_code', sa.String(length=80), nullable=False),
    sa.Column('reason_ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('key_version', sa.String(length=256), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('length(reason_code) > 0', name=op.f('ck_manual_reviews_reason_required')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_manual_reviews_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_reviews')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_manual_reviews_scope_id')
    )
    op.create_table('risk_assessments',
    sa.Column('decision', sa.Enum('PASS', 'REVIEW', 'FAIL', name='riskdecision', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('policy_version', sa.String(length=120), nullable=False),
    sa.Column('reason_codes', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('check_summary', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_risk_assessments_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_risk_assessments')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_risk_assessments_scope_id')
    )
    op.create_table('barcode_results',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('symbology', sa.String(length=40), nullable=False),
    sa.Column('decoded', sa.Boolean(), nullable=False),
    sa.Column('format_valid', sa.Boolean(), nullable=True),
    sa.Column('signature_present', sa.Boolean(), nullable=False),
    sa.Column('signature_valid', sa.Boolean(), nullable=True),
    sa.Column('data_consistency', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('payload_ciphertext', sa.LargeBinary(), nullable=True),
    sa.Column('key_version', sa.String(length=256), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('payload_ciphertext IS NULL OR key_version IS NOT NULL', name=op.f('ck_barcode_results_payload_key_required')),
    sa.CheckConstraint('signature_valid IS NULL OR signature_present', name=op.f('ck_barcode_results_signature_presence')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id', 'document_id'], ['identity_documents.organization_id', 'identity_documents.session_id', 'identity_documents.id'], name=op.f('fk_barcode_results_organization_id_identity_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_barcode_results_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_barcode_results')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_barcode_results_scope_id')
    )
    op.create_index('ix_barcode_results_document_scope', 'barcode_results', ['organization_id', 'session_id', 'document_id'], unique=False)
    op.create_table('document_checks',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('check_type', sa.String(length=80), nullable=False),
    sa.Column('result', sa.Enum('PASS', 'REVIEW', 'FAIL', 'NOT_APPLICABLE', 'UNAVAILABLE', name='checkresult', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('evidence_metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'session_id', 'document_id'], ['identity_documents.organization_id', 'identity_documents.session_id', 'identity_documents.id'], name=op.f('fk_document_checks_organization_id_identity_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_document_checks_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_checks')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_document_checks_scope_id')
    )
    op.create_index('ix_document_checks_document_scope', 'document_checks', ['organization_id', 'session_id', 'document_id'], unique=False)
    op.create_table('document_fields',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('field_name', sa.String(length=80), nullable=False),
    sa.Column('raw_value_ciphertext', sa.LargeBinary(), nullable=True),
    sa.Column('normalized_value_ciphertext', sa.LargeBinary(), nullable=True),
    sa.Column('key_version', sa.String(length=256), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('bounding_box', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('(raw_value_ciphertext IS NULL AND normalized_value_ciphertext IS NULL) OR key_version IS NOT NULL', name=op.f('ck_document_fields_ciphertext_key_required')),
    sa.CheckConstraint('confidence BETWEEN 0 AND 1', name=op.f('ck_document_fields_confidence_range')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id', 'document_id'], ['identity_documents.organization_id', 'identity_documents.session_id', 'identity_documents.id'], name=op.f('fk_document_fields_organization_id_identity_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_document_fields_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_fields')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_document_fields_scope_id')
    )
    op.create_index('ix_document_fields_document_scope', 'document_fields', ['organization_id', 'session_id', 'document_id'], unique=False)
    op.create_table('document_images',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('side', sa.String(length=20), nullable=False),
    sa.Column('encrypted_object_ref', sa.String(length=1024), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('quality_scores', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('delete_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("side IN ('FRONT', 'BACK', 'DATA_PAGE', 'PORTRAIT')", name=op.f('ck_document_images_document_side')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id', 'document_id'], ['identity_documents.organization_id', 'identity_documents.session_id', 'identity_documents.id'], name=op.f('fk_document_images_organization_id_identity_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_document_images_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_images')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_document_images_scope_id')
    )
    op.create_index('ix_document_images_document_scope', 'document_images', ['organization_id', 'session_id', 'document_id'], unique=False)
    op.create_table('face_comparisons',
    sa.Column('reference_template_id', sa.Uuid(), nullable=False),
    sa.Column('live_template_id', sa.Uuid(), nullable=False),
    sa.Column('model_name', sa.String(length=120), nullable=False),
    sa.Column('model_version', sa.String(length=120), nullable=False),
    sa.Column('threshold_policy_version', sa.String(length=120), nullable=False),
    sa.Column('comparison_score', sa.Float(), nullable=False),
    sa.Column('result', sa.Enum('PASS', 'REVIEW', 'FAIL', 'NOT_APPLICABLE', 'UNAVAILABLE', name='checkresult', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'session_id', 'live_template_id'], ['biometric_templates.organization_id', 'biometric_templates.session_id', 'biometric_templates.id'], name='fk_face_comparisons_live_template', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'session_id', 'reference_template_id'], ['biometric_templates.organization_id', 'biometric_templates.session_id', 'biometric_templates.id'], name='fk_face_comparisons_reference_template', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_face_comparisons_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_comparisons')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_face_comparisons_scope_id')
    )
    op.create_index('ix_face_comparisons_live_scope', 'face_comparisons', ['organization_id', 'session_id', 'live_template_id'], unique=False)
    op.create_index('ix_face_comparisons_reference_scope', 'face_comparisons', ['organization_id', 'session_id', 'reference_template_id'], unique=False)
    op.create_table('mrz_results',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('format', sa.String(length=8), nullable=False),
    sa.Column('mrz_valid', sa.Boolean(), nullable=False),
    sa.Column('check_digit_results', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('field_consistency', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("format IN ('TD1', 'TD2', 'TD3', 'UNKNOWN')", name=op.f('ck_mrz_results_mrz_format')),
    sa.ForeignKeyConstraint(['organization_id', 'session_id', 'document_id'], ['identity_documents.organization_id', 'identity_documents.session_id', 'identity_documents.id'], name=op.f('fk_mrz_results_organization_id_identity_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_mrz_results_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mrz_results')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_mrz_results_scope_id')
    )
    op.create_index('ix_mrz_results_document_scope', 'mrz_results', ['organization_id', 'session_id', 'document_id'], unique=False)
    op.create_table('nfc_results',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('NFC_NOT_SUPPORTED', 'NFC_NOT_AVAILABLE', 'NFC_FAILED', 'NFC_READ', 'NFC_VERIFIED', name='nfcstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('passive_authentication', sa.Boolean(), nullable=True),
    sa.Column('chip_authentication', sa.Boolean(), nullable=True),
    sa.Column('trust_store_version', sa.String(length=120), nullable=True),
    sa.Column('data_group_checks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('evidence_metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'session_id', 'document_id'], ['identity_documents.organization_id', 'identity_documents.session_id', 'identity_documents.id'], name=op.f('fk_nfc_results_organization_id_identity_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id', 'session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_nfc_results_organization_id_kyc_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_nfc_results')),
    sa.UniqueConstraint('organization_id', 'session_id', 'id', name='uq_nfc_results_scope_id')
    )
    op.create_index('ix_nfc_results_document_scope', 'nfc_results', ['organization_id', 'session_id', 'document_id'], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            column = "id" if table == "organizations" else "organization_id"
            scope = f"{column} = NULLIF(current_setting('app.organization_id', true), '')::uuid"
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation ON "{table}" USING ({scope}) WITH CHECK ({scope})')


def downgrade() -> None:
    # Frozen Phase 1 DDL; append future migrations instead of importing live models.
    op.drop_index('ix_nfc_results_document_scope', table_name='nfc_results')
    op.drop_table('nfc_results')
    op.drop_index('ix_mrz_results_document_scope', table_name='mrz_results')
    op.drop_table('mrz_results')
    op.drop_index('ix_face_comparisons_reference_scope', table_name='face_comparisons')
    op.drop_index('ix_face_comparisons_live_scope', table_name='face_comparisons')
    op.drop_table('face_comparisons')
    op.drop_index('ix_document_images_document_scope', table_name='document_images')
    op.drop_table('document_images')
    op.drop_index('ix_document_fields_document_scope', table_name='document_fields')
    op.drop_table('document_fields')
    op.drop_index('ix_document_checks_document_scope', table_name='document_checks')
    op.drop_table('document_checks')
    op.drop_index('ix_barcode_results_document_scope', table_name='barcode_results')
    op.drop_table('barcode_results')
    op.drop_table('risk_assessments')
    op.drop_table('manual_reviews')
    op.drop_table('liveness_checks')
    op.drop_index('ix_identity_documents_org_number_hmac', table_name='identity_documents')
    op.drop_table('identity_documents')
    op.drop_table('fraud_signals')
    op.drop_table('consents')
    op.drop_table('biometric_templates')
    op.drop_index('ix_kyc_sessions_org_status_created', table_name='kyc_sessions')
    op.drop_index('ix_kyc_sessions_org_expires', table_name='kyc_sessions')
    op.drop_table('kyc_sessions')
    op.drop_index('ix_audit_logs_org_session_created', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('organizations')

