"""Phase 17: erase encrypted review notes through a scoped database function.

Revision ID: 0012_phase17_finalize
Revises: 0011_phase17
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_phase17_finalize"
down_revision = "0011_phase17"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("manual_reviews") as batch:
        batch.alter_column("reason_ciphertext", existing_type=sa.LargeBinary(), nullable=True)
        batch.alter_column("key_version", existing_type=sa.String(256), nullable=True)
        batch.create_check_constraint(op.f("ck_manual_reviews_review_note_key_pair"),
                                      "(reason_ciphertext IS NULL) = (key_version IS NULL)")
    if op.get_bind().dialect.name == "postgresql":
        # Resolve the migration's schema at execution time, including offline SQL
        # and isolated test schemas. Every relation is qualified and pg_temp is
        # last, so temporary objects cannot shadow the privileged function's reads.
        op.execute("""
        DO $migration$
        DECLARE app_schema text := current_schema();
        BEGIN
          EXECUTE format($definition$
            CREATE FUNCTION %I.erase_review_notes(target_session uuid)
            RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, %I, pg_temp
            AS $function$
            DECLARE affected integer;
            BEGIN
              UPDATE %I.manual_reviews AS review
                 SET reason_ciphertext = NULL, key_version = NULL
                FROM %I.kyc_sessions AS session
               WHERE review.session_id = target_session
                 AND session.id = review.session_id
                 AND session.organization_id = review.organization_id
                 AND review.organization_id =
                     NULLIF(current_setting('app.organization_id', true), '')::uuid
                 AND session.erased_at IS NOT NULL
                 AND review.reason_ciphertext IS NOT NULL;
              GET DIAGNOSTICS affected = ROW_COUNT;
              RETURN affected;
            END;
            $function$
          $definition$, app_schema, app_schema, app_schema, app_schema);
          EXECUTE format('REVOKE ALL ON FUNCTION %I.erase_review_notes(uuid) FROM PUBLIC', app_schema);
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kyc_app') THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %I.erase_review_notes(uuid) TO kyc_app', app_schema);
          END IF;
        END;
        $migration$;
        """)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""DO $migration$
        BEGIN
          EXECUTE format('DROP FUNCTION %I.erase_review_notes(uuid)', current_schema());
        END;
        $migration$;""")
    # A downgrade with erased notes fails instead of recreating deleted personal
    # data. Deployments that have erased notes must keep the nullable columns.
    with op.batch_alter_table("manual_reviews") as batch:
        batch.drop_constraint(op.f("ck_manual_reviews_review_note_key_pair"), type_="check")
        batch.alter_column("reason_ciphertext", existing_type=sa.LargeBinary(), nullable=False)
        batch.alter_column("key_version", existing_type=sa.String(256), nullable=False)
