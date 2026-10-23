"""Optional face identity: independent consent, credentials, encrypted templates and tenant RLS.

Frozen schema snapshot. Does not change KYC verdicts or enrollment state for existing users.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import Text

revision = "0013_face_identity"
down_revision = "0012_phase17_finalize"
branch_labels = None
depends_on = None

FACE_TABLES = ('face_actors', 'face_capture_sessions', 'face_consents', 'face_deletions', 'face_enrollments', 'face_recognition_events', 'face_sample_receipts', 'face_subjects', 'face_templates')


def _erasure_function():
    op.execute("""
    DO $migration$
    DECLARE app_schema text := current_schema();
    BEGIN
      EXECUTE format($definition$
        CREATE FUNCTION %I.erase_face_identity(target_session uuid)
        RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, %I, pg_temp
        AS $function$
        DECLARE tenant uuid := NULLIF(current_setting('app.organization_id', true), '')::uuid;
                affected integer;
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM kyc_sessions WHERE id = target_session
                         AND organization_id = tenant AND erased_at IS NOT NULL) THEN
            RETURN 0;
          END IF;
          INSERT INTO face_deletions (id, created_at, organization_id, enrollment_id, reason, ledger_synced)
            SELECT gen_random_uuid(), now(), tenant, e.id, 'KYC_ERASURE', false
              FROM face_enrollments e JOIN face_subjects s
                ON s.organization_id = e.organization_id AND s.id = e.subject_id
             WHERE s.organization_id = tenant AND s.kyc_session_id = target_session
            ON CONFLICT (organization_id, enrollment_id) DO NOTHING;
          DELETE FROM face_templates WHERE organization_id = tenant AND enrollment_id IN
            (SELECT e.id FROM face_enrollments e JOIN face_subjects s
              ON s.organization_id = e.organization_id AND s.id = e.subject_id
              WHERE s.organization_id = tenant AND s.kyc_session_id = target_session);
          GET DIAGNOSTICS affected = ROW_COUNT;
          UPDATE face_enrollments SET status = 'REVOKED' WHERE organization_id = tenant AND subject_id IN
            (SELECT id FROM face_subjects WHERE organization_id = tenant AND kyc_session_id = target_session);
          UPDATE face_capture_sessions SET status = 'REVOKED', sample_ciphertext = NULL,
                 sample_key_version = NULL, reference_hash = NULL
            WHERE organization_id = tenant AND actor_id IN
              (SELECT a.id FROM face_actors a JOIN face_subjects s
                 ON s.organization_id = a.organization_id AND s.id = a.subject_id
               WHERE s.organization_id = tenant AND s.kyc_session_id = target_session);
          UPDATE face_recognition_events SET evidence_ciphertext = NULL, evidence_key_version = NULL
            WHERE organization_id = tenant AND actor_id IN
              (SELECT a.id FROM face_actors a JOIN face_subjects s
                 ON s.organization_id = a.organization_id AND s.id = a.subject_id
               WHERE s.organization_id = tenant AND s.kyc_session_id = target_session);
          UPDATE face_consents SET withdrawn_at = COALESCE(withdrawn_at, now()) WHERE organization_id = tenant AND subject_id IN
            (SELECT id FROM face_subjects WHERE organization_id = tenant AND kyc_session_id = target_session);
          UPDATE face_actors SET revoked_at = now() WHERE organization_id = tenant AND subject_id IN
            (SELECT id FROM face_subjects WHERE organization_id = tenant AND kyc_session_id = target_session);
          UPDATE face_subjects SET active = false, profile_ciphertext = NULL, profile_key_version = NULL
            WHERE organization_id = tenant AND kyc_session_id = target_session;
          RETURN affected;
        END;
        $function$
      $definition$, app_schema, app_schema);
      EXECUTE format('REVOKE ALL ON FUNCTION %I.erase_face_identity(uuid) FROM PUBLIC', app_schema);
      IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kyc_app') THEN
        EXECUTE format('GRANT EXECUTE ON FUNCTION %I.erase_face_identity(uuid) TO kyc_app', app_schema);
      END IF;
    END;
    $migration$;
    """)

def upgrade():
    # Frozen schema for the optional biometric service.
    op.create_table('face_deletions',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('enrollment_id', sa.Uuid(), nullable=False),
    sa.Column('reason', sa.String(length=40), nullable=False),
    sa.Column('ledger_synced', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_deletions_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_deletions')),
    sa.UniqueConstraint('organization_id', 'enrollment_id', name=op.f('uq_face_deletions_organization_id')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_deletions_scope_id')
    )
    op.create_table('face_sample_receipts',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_sample_receipts_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_sample_receipts')),
    sa.UniqueConstraint('organization_id', 'digest', name=op.f('uq_face_sample_receipts_organization_id')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_sample_receipts_scope_id')
    )
    op.create_index('ix_face_sample_receipts_expiry', 'face_sample_receipts', ['organization_id', 'expires_at'], unique=False)
    op.create_table('face_subjects',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('kyc_session_id', sa.Uuid(), nullable=False),
    sa.Column('population', sa.String(length=16), server_default='CUSTOMER', nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('profile_ciphertext', sa.LargeBinary(), nullable=True),
    sa.Column('profile_key_version', sa.String(length=64), nullable=True),
    sa.Column('profile_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'kyc_session_id'], ['kyc_sessions.organization_id', 'kyc_sessions.id'], name=op.f('fk_face_subjects_organization_id_kyc_sessions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_subjects_organization_id_organizations'), ondelete='RESTRICT'),
    sa.CheckConstraint("population IN ('STAFF', 'CUSTOMER')", name=op.f('ck_face_subjects_subject_population')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_subjects')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_subjects_scope_id'),
    sa.UniqueConstraint('organization_id', 'kyc_session_id', name=op.f('uq_face_subjects_organization_id'))
    )
    op.create_index('ix_face_subjects_population', 'face_subjects', ['organization_id', 'population', 'active'], unique=False)
    op.create_table('face_actors',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('subject_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('purposes', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('populations', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), server_default='["CUSTOMER"]', nullable=False),
    sa.Column('profile_fields', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('branch', sa.String(length=64), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("(kind = 'PERSON' AND subject_id IS NOT NULL) OR (kind <> 'PERSON' AND subject_id IS NULL)", name=op.f('ck_face_actors_actor_subject')),
    sa.CheckConstraint("kind IN ('PERSON', 'KIOSK', 'OPERATOR')", name=op.f('ck_face_actors_actor_kind')),
    sa.ForeignKeyConstraint(['organization_id', 'subject_id'], ['face_subjects.organization_id', 'face_subjects.id'], name=op.f('fk_face_actors_organization_id_face_subjects'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_actors_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_actors')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_actors_scope_id'),
    sa.UniqueConstraint('organization_id', 'token_hash', name=op.f('uq_face_actors_organization_id'))
    )
    op.create_index('ix_face_actors_subject', 'face_actors', ['organization_id', 'subject_id'], unique=False)
    op.create_table('face_consents',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('subject_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('purpose', sa.String(length=40), nullable=False),
    sa.Column('notice_version', sa.String(length=80), nullable=False),
    sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'actor_id'], ['face_actors.organization_id', 'face_actors.id'], name=op.f('fk_face_consents_organization_id_face_actors'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'subject_id'], ['face_subjects.organization_id', 'face_subjects.id'], name=op.f('fk_face_consents_organization_id_face_subjects'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_consents_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_consents')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_consents_scope_id')
    )
    op.create_index('ix_face_consents_subject', 'face_consents', ['organization_id', 'subject_id'], unique=False)
    op.create_table('face_recognition_events',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('capture_session_id', sa.Uuid(), nullable=True),
    sa.Column('outcome', sa.String(length=40), nullable=False),
    sa.Column('reason_codes', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('purpose', sa.String(length=40), nullable=False),
    sa.Column('policy_version', sa.String(length=80), nullable=False),
    sa.Column('model_version', sa.String(length=160), nullable=False),
    sa.Column('evidence_ciphertext', sa.LargeBinary(), nullable=True),
    sa.Column('evidence_key_version', sa.String(length=64), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'actor_id'], ['face_actors.organization_id', 'face_actors.id'], name=op.f('fk_face_recognition_events_organization_id_face_actors'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_recognition_events_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_recognition_events')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_recognition_events_scope_id')
    )
    op.create_index('ix_face_recognition_events_session', 'face_recognition_events', ['organization_id', 'capture_session_id', 'created_at'], unique=False)
    op.create_table('face_enrollments',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('subject_id', sa.Uuid(), nullable=False),
    sa.Column('consent_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('model_version', sa.String(length=160), nullable=False),
    sa.Column('policy_version', sa.String(length=80), nullable=False),
    sa.Column('resolution_key', sa.String(length=64), nullable=True),
    sa.Column('resolution_fingerprint', sa.String(length=64), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("status IN ('ACTIVE', 'DUPLICATE_REVIEW', 'REVOKED', 'EXPIRED', 'SUPERSEDED', 'REJECTED')", name=op.f('ck_face_enrollments_enrollment_status')),
    sa.ForeignKeyConstraint(['organization_id', 'consent_id'], ['face_consents.organization_id', 'face_consents.id'], name=op.f('fk_face_enrollments_organization_id_face_consents'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'subject_id'], ['face_subjects.organization_id', 'face_subjects.id'], name=op.f('fk_face_enrollments_organization_id_face_subjects'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_enrollments_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_enrollments')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_enrollments_scope_id')
    )
    op.create_index('ix_face_enrollments_population', 'face_enrollments', ['organization_id', 'model_version', 'status', 'expires_at'], unique=False)
    op.create_index('ix_face_enrollments_subject', 'face_enrollments', ['organization_id', 'subject_id'], unique=False)
    op.create_index('uq_face_enrollments_active_subject', 'face_enrollments', ['organization_id', 'subject_id'], unique=True, postgresql_where=sa.text("status = 'ACTIVE'"), sqlite_where=sa.text("status = 'ACTIVE'"))
    op.create_table('face_capture_sessions',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=False),
    sa.Column('consent_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('purpose', sa.String(length=40), nullable=False),
    sa.Column('policy_version', sa.String(length=80), nullable=False),
    sa.Column('steps', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('step_index', sa.Integer(), server_default='0', nullable=False),
    sa.Column('ticket_hash', sa.String(length=64), nullable=False),
    sa.Column('ticket_issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('sample_ciphertext', sa.LargeBinary(), nullable=True),
    sa.Column('sample_key_version', sa.String(length=64), nullable=True),
    sa.Column('last_frame_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('frame_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('completion_key', sa.String(length=128), nullable=True),
    sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=True),
    sa.Column('matched_enrollment_id', sa.Uuid(), nullable=True),
    sa.Column('reference_hash', sa.String(length=64), nullable=True),
    sa.Column('reference_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disclosure_key', sa.String(length=128), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("kind IN ('ENROLLMENT', 'RECOGNITION')", name=op.f('ck_face_capture_sessions_capture_kind')),
    sa.ForeignKeyConstraint(['organization_id', 'actor_id'], ['face_actors.organization_id', 'face_actors.id'], name=op.f('fk_face_capture_sessions_organization_id_face_actors'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'consent_id'], ['face_consents.organization_id', 'face_consents.id'], name=op.f('fk_face_capture_sessions_organization_id_face_consents'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id', 'matched_enrollment_id'], ['face_enrollments.organization_id', 'face_enrollments.id'], name=op.f('fk_face_capture_sessions_organization_id_face_enrollments'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_capture_sessions_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_capture_sessions')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_capture_sessions_scope_id')
    )
    op.create_index('ix_face_capture_sessions_actor', 'face_capture_sessions', ['organization_id', 'actor_id', 'created_at'], unique=False)
    op.create_index('ix_face_capture_sessions_expiry', 'face_capture_sessions', ['organization_id', 'expires_at'], unique=False)
    op.create_table('face_templates',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('enrollment_id', sa.Uuid(), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('key_version', sa.String(length=64), nullable=False),
    sa.Column('quality_metadata', sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'enrollment_id'], ['face_enrollments.organization_id', 'face_enrollments.id'], name=op.f('fk_face_templates_organization_id_face_enrollments'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_face_templates_organization_id_organizations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_face_templates')),
    sa.UniqueConstraint('organization_id', 'enrollment_id', name=op.f('uq_face_templates_organization_id')),
    sa.UniqueConstraint('organization_id', 'id', name='uq_face_templates_scope_id')
    )

    if op.get_bind().dialect.name == "postgresql":
        scope = "organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid"
        for name in FACE_TABLES:
            op.execute(f'ALTER TABLE "{name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{name}" FORCE ROW LEVEL SECURITY')
            op.execute(f'CREATE POLICY tenant_isolation ON "{name}" USING ({scope}) WITH CHECK ({scope})')
        _erasure_function()


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS erase_face_identity(uuid)")

    op.drop_table('face_templates')
    op.drop_index('ix_face_capture_sessions_expiry', table_name='face_capture_sessions')
    op.drop_index('ix_face_capture_sessions_actor', table_name='face_capture_sessions')
    op.drop_table('face_capture_sessions')
    op.drop_index('uq_face_enrollments_active_subject', table_name='face_enrollments', postgresql_where=sa.text("status = 'ACTIVE'"), sqlite_where=sa.text("status = 'ACTIVE'"))
    op.drop_index('ix_face_enrollments_subject', table_name='face_enrollments')
    op.drop_index('ix_face_enrollments_population', table_name='face_enrollments')
    op.drop_table('face_enrollments')
    op.drop_index('ix_face_recognition_events_session', table_name='face_recognition_events')
    op.drop_table('face_recognition_events')
    op.drop_index('ix_face_consents_subject', table_name='face_consents')
    op.drop_table('face_consents')
    op.drop_index('ix_face_actors_subject', table_name='face_actors')
    op.drop_table('face_actors')
    op.drop_index('ix_face_subjects_population', table_name='face_subjects')
    op.drop_table('face_subjects')
    op.drop_index('ix_face_sample_receipts_expiry', table_name='face_sample_receipts')
    op.drop_table('face_sample_receipts')
    op.drop_table('face_deletions')

