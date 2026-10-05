"""Optional live PostgreSQL test in a fresh schema and a temporary restricted role."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
from uuid import uuid4

from alembic import command
from alembic.config import Config
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
TEST_URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URL is not set; live PostgreSQL is unavailable in this runner.")
class PostgreSQLIsolationTests(unittest.TestCase):
    def test_migration_and_database_enforced_tenant_isolation(self):
        parsed = sa.engine.make_url(TEST_URL)
        if not parsed.database or not parsed.database.endswith("_test"):
            self.fail("Use an isolated database whose name ends with _test.")
        engine = sa.create_engine(TEST_URL)
        suffix = uuid4().hex[:16]
        schema, role = f"kyc_test_{suffix}", f"kyc_test_role_{suffix}"
        schema_created = role_created = False
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                schema_created = True
                # Keep the live deployment's public alembic_version and evidence out
                # of this test. Otherwise Alembic may see a migrated public schema
                # and skip creating this test's fresh tables.
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                config = Config(str(ROOT / "alembic.ini"))
                config.attributes.update(connection=connection, database_url=TEST_URL)
                command.upgrade(config, "head")
                connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                role_created = True
                connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
                connection.exec_driver_sql(f'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
                org_a, org_b = uuid4(), uuid4()
                sessions = {}
                for organization in [org_a, org_b]:
                    connection.execute(sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": str(organization)})
                    connection.execute(sa.text("INSERT INTO organizations (id, name) VALUES (:id, 'PostgreSQL test')"), {"id": organization})
                    now = datetime.now(timezone.utc)
                    sessions[organization] = uuid4()
                    connection.execute(sa.text("""INSERT INTO kyc_sessions
                      (id, organization_id, user_id, country, expected_document_type, verification_level, status, created_at, updated_at, expires_at)
                      VALUES (:id, :org, 'postgres-test', 'KH', 'KH_NATIONAL_ID', 'DOCUMENT_ONLY', 'CREATED', :created, :created, :expires)"""),
                      {"id": sessions[organization], "org": organization, "created": now, "expires": now + timedelta(minutes=10)})
                connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                connection.execute(sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": str(org_a)})
                visible = connection.execute(sa.text("SELECT organization_id FROM kyc_sessions")).scalars().all()
                self.assertEqual(visible, [org_a])
                self.assertEqual(connection.execute(sa.text("SELECT count(*) FROM organizations")).scalar_one(), 1)
                document = uuid4()
                connection.execute(sa.text("""INSERT INTO identity_documents (id, organization_id, session_id, document_type, delete_after)
                  VALUES (:id, :org, :session, 'KH_NATIONAL_ID', now() + interval '1 day')"""),
                  {"id": document, "org": org_a, "session": sessions[org_a]})
                connection.execute(sa.text("""INSERT INTO document_images (id, organization_id, session_id, document_id, side,
                  encrypted_object_ref, key_version, media_type, sha256, quality_scores, quality_policy_version, delete_after)
                  VALUES (:id, :org, :session, :document, 'FRONT', 'local://ref', 'v1', 'image/jpeg', :sha, '{}', 'test', now() + interval '1 hour')"""),
                  {"id": uuid4(), "org": org_a, "session": sessions[org_a], "document": document, "sha": "0" * 64})
                connection.execute(sa.text("""INSERT INTO mrz_results
                  (id, organization_id, session_id, document_id, format, mrz_valid, check_digit_results, field_consistency)
                  VALUES (:id, :org, :session, :document, 'TD1', true, '{}', '{}')"""),
                  {"id": uuid4(), "org": org_a, "session": sessions[org_a], "document": document})
                self.assertEqual(connection.execute(sa.text("SELECT count(*) FROM mrz_results")).scalar_one(), 1)
                # Phases 8-9 exercise the real restricted role on each new or
                # extended biometric table. These bytes are test-only opaque
                # ciphertext; no photograph or actual embedding enters this test.
                face_inserts = {
                    "selfie_captures": sa.text("""INSERT INTO selfie_captures
                      (id, organization_id, session_id, encrypted_object_ref, key_version, media_type,
                       sha256, quality_scores, quality_policy_version, delete_after)
                      VALUES (:id, :org, :session, 'biometric-test://no-image', 'test-v1', 'image/jpeg',
                              :sha, '{}', 'test-quality-v1', now() + interval '1 hour')"""),
                    "face_quality_checks": sa.text("""INSERT INTO face_quality_checks
                      (id, organization_id, session_id, source, result, evidence_metadata, delete_after)
                      VALUES (:id, :org, :session, 'LIVE_SELFIE', 'REVIEW', '{}', now() + interval '1 hour')"""),
                    "biometric_templates": sa.text("""INSERT INTO biometric_templates
                      (id, organization_id, session_id, source, model_name, model_version, model_sha256,
                       embedding_dimension, document_id, template_ciphertext, key_version, delete_after)
                      VALUES (:id, :org, :session, :source, 'PostgreSQL test model', 'test-v1', :sha,
                              128, :document, :ciphertext, 'test-v1', now() + interval '1 hour')"""),
                    "face_comparisons": sa.text("""INSERT INTO face_comparisons
                      (id, organization_id, session_id, reference_template_id, live_template_id,
                       model_name, model_version, model_sha256, comparison_metric, evidence_metadata,
                       threshold_policy_version, comparison_score, result)
                      VALUES (:id, :org, :session, :reference, :live, 'PostgreSQL test model', 'test-v1',
                              :sha, 'COSINE_SIMILARITY', '{}', 'uncalibrated-test-v1', 0.0, 'REVIEW')"""),
                    "consents": sa.text("""INSERT INTO consents
                      (id, organization_id, session_id, user_id, scope, policy_version, granted)
                      VALUES (:id, :org, :session, 'postgres-test', 'BIOMETRIC_PROCESSING', 'consent-test-v1', true)"""),
                }

                def insert_face_artifacts(organization, session, document_id):
                    reference, live = uuid4(), uuid4()
                    parameters = {
                        "org": organization, "session": session, "document": document_id,
                        "sha": "0" * 64, "source": "DOCUMENT_PORTRAIT",
                        "ciphertext": b"test-only-opaque-ciphertext", "reference": reference, "live": live,
                    }
                    identifiers = {}
                    for table, statement in face_inserts.items():
                        if table == "biometric_templates":
                            connection.execute(statement, {**parameters, "id": reference})
                            connection.execute(statement, {**parameters, "id": live, "source": "LIVE_SELFIE", "document": None})
                            identifiers[table] = {reference, live}
                        else:
                            identifier = uuid4()
                            connection.execute(statement, {**parameters, "id": identifier})
                            identifiers[table] = {identifier}
                    return parameters, identifiers

                face_parameters_a, face_identifiers_a = insert_face_artifacts(org_a, sessions[org_a], document)
                connection.execute(sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": str(org_b)})
                document_b = uuid4()
                connection.execute(sa.text("""INSERT INTO identity_documents (id, organization_id, session_id, document_type, delete_after)
                  VALUES (:id, :org, :session, 'KH_NATIONAL_ID', now() + interval '1 day')"""),
                  {"id": document_b, "org": org_b, "session": sessions[org_b]})
                face_parameters_b, face_identifiers_b = insert_face_artifacts(org_b, sessions[org_b], document_b)
                for organization, identifiers in ((org_a, face_identifiers_a), (org_b, face_identifiers_b)):
                    connection.execute(sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": str(organization)})
                    for table, expected_ids in identifiers.items():
                        with self.subTest(table=table, tenant=str(organization)):
                            self.assertEqual(set(connection.execute(sa.text(f"SELECT id FROM {table}")).scalars()), expected_ids)
                connection.execute(sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": str(org_a)})
                for table, statement in face_inserts.items():
                    with self.subTest(table=table, violation="tenant row policy"):
                        with self.assertRaises(sa.exc.ProgrammingError), connection.begin_nested():
                            connection.execute(statement, {**face_parameters_b, "id": uuid4()})
                    # Matching tenant identifiers pass RLS, then composite FKs
                    # must reject a session that belongs to another tenant.
                    with self.subTest(table=table, violation="cross-tenant session foreign key"):
                        with self.assertRaises(sa.exc.IntegrityError), connection.begin_nested():
                            connection.execute(statement, {**face_parameters_a, "id": uuid4(), "session": sessions[org_b]})

                secondary_session, secondary_document, secondary_template = uuid4(), uuid4(), uuid4()
                now = datetime.now(timezone.utc)
                connection.execute(sa.text("""INSERT INTO kyc_sessions
                  (id, organization_id, user_id, country, expected_document_type, verification_level, status, created_at, updated_at, expires_at)
                  VALUES (:id, :org, 'postgres-test', 'KH', 'KH_NATIONAL_ID', 'DOCUMENT_ONLY', 'CREATED', :created, :created, :expires)"""),
                  {"id": secondary_session, "org": org_a, "created": now, "expires": now + timedelta(minutes=10)})
                connection.execute(sa.text("""INSERT INTO identity_documents (id, organization_id, session_id, document_type, delete_after)
                  VALUES (:id, :org, :session, 'KH_NATIONAL_ID', now() + interval '1 day')"""),
                  {"id": secondary_document, "org": org_a, "session": secondary_session})
                connection.execute(face_inserts["biometric_templates"],
                                   {**face_parameters_a, "id": secondary_template, "session": secondary_session,
                                    "document": secondary_document})
                for wrong_document in (document_b, secondary_document):
                    with self.subTest(violation="document template foreign key", document=str(wrong_document)):
                        with self.assertRaises(sa.exc.IntegrityError), connection.begin_nested():
                            connection.execute(face_inserts["biometric_templates"],
                                               {**face_parameters_a, "id": uuid4(), "document": wrong_document})
                for parameter in ("reference", "live"):
                    for wrong_template in (face_parameters_b[parameter], secondary_template):
                        with self.subTest(violation="comparison template foreign key", parameter=parameter):
                            with self.assertRaises(sa.exc.IntegrityError), connection.begin_nested():
                                connection.execute(face_inserts["face_comparisons"],
                                                   {**face_parameters_a, "id": uuid4(), parameter: wrong_template})
                # Row policy WITH CHECK refuses evidence written under another tenant's identifier.
                with self.assertRaises(sa.exc.ProgrammingError), connection.begin_nested():
                    connection.execute(sa.text("""INSERT INTO identity_documents (id, organization_id, session_id, document_type, delete_after)
                      VALUES (:id, :org, :session, 'KH_NATIONAL_ID', now() + interval '1 day')"""),
                      {"id": uuid4(), "org": org_b, "session": sessions[org_b]})
                with self.assertRaises(sa.exc.ProgrammingError), connection.begin_nested():
                    connection.execute(sa.text("""INSERT INTO mrz_results
                      (id, organization_id, session_id, document_id, format, mrz_valid, check_digit_results, field_consistency)
                      VALUES (:id, :org, :session, :document, 'TD1', true, '{}', '{}')"""),
                      {"id": uuid4(), "org": org_b, "session": sessions[org_a], "document": document})
                connection.execute(sa.text("SELECT set_config('app.organization_id', :org, true)"), {"org": str(org_b)})
                self.assertEqual(connection.execute(sa.text("SELECT count(*) FROM document_images")).scalar_one(), 0)
                self.assertEqual(connection.execute(sa.text("SELECT count(*) FROM mrz_results")).scalar_one(), 0)
                for table in face_inserts:
                    self.assertEqual(set(connection.execute(sa.text(f"SELECT id FROM {table}")).scalars()), face_identifiers_b[table])
                connection.execute(sa.text("SELECT set_config('app.organization_id', '', true)"))
                self.assertEqual(connection.execute(sa.text("SELECT count(*) FROM kyc_sessions")).scalar_one(), 0)
                self.assertEqual(connection.execute(sa.text("SELECT count(*) FROM mrz_results")).scalar_one(), 0)
                for table, statement in face_inserts.items():
                    with self.subTest(table=table, violation="missing tenant context"):
                        self.assertEqual(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one(), 0)
                        with self.assertRaises(sa.exc.ProgrammingError), connection.begin_nested():
                            connection.execute(statement, {**face_parameters_a, "id": uuid4()})
                connection.exec_driver_sql("RESET ROLE")
        finally:
            with engine.begin() as connection:
                if schema_created:
                    connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                if role_created:
                    connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
            engine.dispose()
