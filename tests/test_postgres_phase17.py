"""Optional Phase 17 regression against migrated PostgreSQL and a restricted API role.

Only a database ending in ``_test`` is accepted. All objects and privileges belong
to a fresh schema and temporary role, which are removed even when a check fails.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import unittest
from uuid import uuid4

from alembic import command
from alembic.config import Config
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from kyc.api.dependencies import TenantContext
from kyc.db.models import (AuditLog, BiometricTemplate, Consent, DocumentField, DocumentImage, IdentityDocument,
                           KYCSession, ManualReview, Organization, RiskAssessmentRecord, SelfieCapture)
from kyc.db.privileges import APPEND_ONLY, grant_statements
from kyc.domain.enums import DocumentType, ReviewAction, RiskDecision, SessionStatus, VerificationLevel
from kyc.services.erasure import ERASED_USER, SESSION_TABLES, erase_session
from scripts.security_check import catalog_checks

ROOT = Path(__file__).resolve().parents[1]
TEST_URL = os.environ.get("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URL is not set; live PostgreSQL is unavailable in this runner.")
class PostgreSQLPrivacyHardeningTests(unittest.TestCase):
    def _catalog(self, connection, role, schema):
        return {item["check"]: item for item in catalog_checks(connection, role=role, schema=schema)}

    def _assert_catalog_clean(self, connection, role, schema):
        checks = self._catalog(connection, role, schema)
        self.assertEqual([item for item in checks.values() if item["result"] != "PASS"], [])

    def _tenant(self, connection, organization=None):
        connection.execute(sa.text("SELECT set_config('app.organization_id', :organization, true)"),
                           {"organization": str(organization) if organization is not None else ""})

    def _erase_notes(self, connection, session_id):
        return connection.execute(sa.text("SELECT erase_review_notes(:session_id)"),
                                  {"session_id": session_id}).scalar_one()

    def _seed_case(self, connection, organization, user_id):
        """Opaque synthetic ciphertext only; no actual identity or image enters this test."""
        self._tenant(connection, organization)
        now = datetime.now(timezone.utc)
        with Session(connection, join_transaction_mode="create_savepoint") as db, db.begin():
            record = KYCSession(id=uuid4(), organization_id=organization, user_id=user_id, country="KH",
                                expected_document_type=DocumentType.KH_NATIONAL_ID,
                                verification_level=VerificationLevel.DOCUMENT_ONLY, status=SessionStatus.REJECTED,
                                created_at=now, updated_at=now, expires_at=now + timedelta(hours=1), version=3,
                                idempotency_key=f"key-{user_id}", request_fingerprint="f" * 64,
                                client_token_sha256="c" * 64)
            db.add(record)
            db.flush()
            document = IdentityDocument(id=uuid4(), organization_id=organization, session_id=record.id,
                                        document_type=DocumentType.KH_NATIONAL_ID, delete_after=now + timedelta(days=1))
            db.add(document)
            db.flush()
            review = ManualReview(id=uuid4(), organization_id=organization, session_id=record.id,
                                  reviewer_id="synthetic-reviewer", action=ReviewAction.REJECT,
                                  reason_code="IDENTITY_MISUSE_SUSPECTED", reason_ciphertext=b"opaque-review-note",
                                  key_version="test-v1", session_version=2)
            risk = RiskAssessmentRecord(id=uuid4(), organization_id=organization, session_id=record.id,
                                        decision=RiskDecision.FAIL, policy_version="test-risk-v1",
                                        reason_codes=["DOCUMENT_AUTHENTICITY_FAILED"], check_summary={})
            audit = AuditLog(id=uuid4(), organization_id=organization, session_id=record.id,
                             actor_id="synthetic-reviewer", action="REVIEW_REJECTED", request_id=uuid4(),
                             reason_codes=["IDENTITY_MISUSE_SUSPECTED"], event_metadata={})
            db.add_all([review, risk, audit,
                DocumentImage(organization_id=organization, session_id=record.id, document_id=document.id,
                              side="FRONT", encrypted_object_ref=f"test://{record.id}/document", key_version="test-v1",
                              media_type="image/jpeg", sha256="a" * 64, quality_scores={}, quality_policy_version="test-v1",
                              delete_after=now + timedelta(hours=1)),
                DocumentField(organization_id=organization, session_id=record.id, document_id=document.id,
                              field_name="full_name", normalized_value_ciphertext=b"opaque-identity-field",
                              key_version="test-v1", confidence=0.9, source="OCR"),
                SelfieCapture(organization_id=organization, session_id=record.id,
                              encrypted_object_ref=f"test://{record.id}/selfie", key_version="test-v1",
                              media_type="image/jpeg", sha256="b" * 64, quality_scores={}, quality_policy_version="test-v1",
                              delete_after=now + timedelta(hours=1)),
                BiometricTemplate(organization_id=organization, session_id=record.id, source="LIVE_SELFIE",
                                  model_name="synthetic", model_version="test-v1", model_sha256="d" * 64,
                                  embedding_dimension=128, template_ciphertext=b"opaque-biometric-template",
                                  key_version="test-v1", delete_after=now + timedelta(hours=1)),
            ])
            for scope in ("DOCUMENT_PROCESSING", "BIOMETRIC_VERIFICATION"):
                db.add(Consent(organization_id=organization, session_id=record.id, user_id=user_id,
                               scope=scope, policy_version="test-consent-v1", granted=True))
            db.flush()
            return {"session": record.id, "review": review.id, "risk": risk.id, "audit": audit.id}

    def test_restricted_erasure_preserves_decisions_and_enforces_tenant_scope(self):
        parsed = sa.engine.make_url(TEST_URL)
        if parsed.get_backend_name() != "postgresql" or not parsed.database or not parsed.database.endswith("_test"):
            self.fail("Use an isolated PostgreSQL database whose name ends with _test.")
        engine = sa.create_engine(TEST_URL, poolclass=NullPool)
        suffix = uuid4().hex[:16]
        schema, role = f"kyc_privacy_test_{suffix}", f"kyc_privacy_role_{suffix}"
        membership_role = f"kyc_privacy_member_{suffix}"
        schema_created = role_created = membership_role_created = False
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                schema_created = True
                # Do not let an existing public alembic_version hide this fresh schema.
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                config = Config(str(ROOT / "alembic.ini"))
                config.attributes.update(connection=connection, database_url=TEST_URL)
                command.upgrade(config, "head")
                connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                role_created = True
                # A previous grant must not survive privilege normalization, including
                # a column ACL which a table-level REVOKE alone would leave intact.
                connection.exec_driver_sql(f'GRANT UPDATE (reason_code) ON "{schema}".manual_reviews TO "{role}"')
                for statement in grant_statements(role=role, schema=schema):
                    connection.execute(sa.text(statement))
                connection.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION "{schema}".erase_review_notes(uuid) TO "{role}"')
                self._assert_catalog_clean(connection, role, schema)

                # Effective permissions must include PUBLIC, even when the API role
                # itself has received no extra table or column privilege.
                connection.exec_driver_sql(f'GRANT UPDATE (reason_code) ON "{schema}".manual_reviews TO PUBLIC')
                checks = self._catalog(connection, role, schema)
                self.assertEqual(checks["no_column_privileges_beyond_matrix"]["result"], "FAIL")
                self.assertIn("manual_reviews.reason_code:UPDATE",
                              checks["no_column_privileges_beyond_matrix"]["detail"])
                self.assertEqual(checks["no_privileges_beyond_matrix"]["result"], "PASS")
                for statement in grant_statements(role=role, schema=schema):
                    connection.execute(sa.text(statement))
                self._assert_catalog_clean(connection, role, schema)

                connection.exec_driver_sql(f'GRANT UPDATE ON "{schema}".manual_reviews TO PUBLIC')
                checks = self._catalog(connection, role, schema)
                self.assertEqual(checks["no_privileges_beyond_matrix"]["result"], "FAIL")
                self.assertEqual(checks["no_privileges_beyond_matrix"]["detail"], {"manual_reviews": ["UPDATE"]})
                for statement in grant_statements(role=role, schema=schema):
                    connection.execute(sa.text(statement))
                self._assert_catalog_clean(connection, role, schema)

                # Inheritance widens effective table rights. NOINHERIT removes those
                # implicit rights but retains membership and the ability to SET ROLE.
                connection.exec_driver_sql(f'CREATE ROLE "{membership_role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
                membership_role_created = True
                connection.exec_driver_sql(f'GRANT UPDATE ON "{schema}".manual_reviews TO "{membership_role}"')
                connection.exec_driver_sql(f'GRANT "{membership_role}" TO "{role}"')
                checks = self._catalog(connection, role, schema)
                self.assertEqual(checks["api_role_has_no_memberships"]["result"], "FAIL")
                self.assertEqual(checks["no_privileges_beyond_matrix"]["result"], "FAIL")
                connection.exec_driver_sql(f'ALTER ROLE "{role}" NOINHERIT')
                # PostgreSQL 16+ stores inheritance on the membership itself, so
                # revoke and regrant after changing the role's inheritance default.
                connection.exec_driver_sql(f'REVOKE "{membership_role}" FROM "{role}"')
                connection.exec_driver_sql(f'GRANT "{membership_role}" TO "{role}"')
                checks = self._catalog(connection, role, schema)
                self.assertEqual(checks["api_role_has_no_memberships"]["result"], "FAIL")
                self.assertIn(membership_role, checks["api_role_has_no_memberships"]["detail"])
                self.assertEqual(checks["no_privileges_beyond_matrix"]["result"], "PASS")
                connection.exec_driver_sql(f'REVOKE "{membership_role}" FROM "{role}"')
                self._assert_catalog_clean(connection, role, schema)

                public_execute = connection.execute(sa.text("""
                    SELECT EXISTS (
                      SELECT 1 FROM pg_proc AS procedure
                      JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
                      CROSS JOIN LATERAL aclexplode(COALESCE(procedure.proacl,
                                                            acldefault('f', procedure.proowner))) AS privilege
                      WHERE namespace.nspname = :schema AND procedure.proname = 'erase_review_notes'
                        AND privilege.grantee = 0 AND privilege.privilege_type = 'EXECUTE'
                    )
                """), {"schema": schema}).scalar_one()
                self.assertFalse(public_execute, "The privileged erasure function must not be executable by PUBLIC")
                function = connection.execute(sa.text("""SELECT procedure.prosecdef, procedure.proconfig
                    FROM pg_proc AS procedure JOIN pg_namespace AS namespace ON namespace.oid = procedure.pronamespace
                    WHERE namespace.nspname = :schema AND procedure.proname = 'erase_review_notes'
                """), {"schema": schema}).one()
                self.assertTrue(function.prosecdef)
                self.assertIn(f"search_path=pg_catalog, {schema}, pg_temp", function.proconfig)

                org_a, org_b = uuid4(), uuid4()
                with Session(connection, join_transaction_mode="create_savepoint") as db, db.begin():
                    for organization in (org_a, org_b):
                        self._tenant(connection, organization)
                        db.add(Organization(id=organization, name="Phase 17 isolated test"))
                        db.flush()
                case_a = self._seed_case(connection, org_a, "tenant-a-function")
                service_case = self._seed_case(connection, org_a, "tenant-a-service")
                case_b = self._seed_case(connection, org_b, "tenant-b-kept")

                connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                self._tenant(connection, org_a)
                self.assertEqual(self._erase_notes(connection, case_a["session"]), 0, "An unerased session keeps its note")
                self.assertEqual(self._erase_notes(connection, case_b["session"]), 0)
                self.assertEqual(bytes(connection.execute(sa.text("SELECT reason_ciphertext FROM manual_reviews WHERE id = :id"),
                                                          {"id": case_a["review"]}).scalar_one()), b"opaque-review-note")

                # Mark both sessions erased as the administrator. Tenant A must still
                # be unable to erase tenant B's note, even when B's marker is present.
                connection.exec_driver_sql("RESET ROLE")
                for organization, case in ((org_a, case_a), (org_b, case_b)):
                    self._tenant(connection, organization)
                    connection.execute(sa.text("UPDATE kyc_sessions SET erased_at = now() WHERE id = :id"),
                                       {"id": case["session"]})
                connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                self._tenant(connection, org_a)
                self.assertEqual(self._erase_notes(connection, case_b["session"]), 0)
                self.assertEqual(self._erase_notes(connection, case_a["session"]), 1)
                self.assertEqual(self._erase_notes(connection, case_a["session"]), 0, "Repeated note erasure is harmless")
                note = connection.execute(sa.text("""SELECT reason_ciphertext, key_version, action, reason_code
                    FROM manual_reviews WHERE id = :id"""), {"id": case_a["review"]}).one()
                self.assertEqual(tuple(note), (None, None, "REJECT", "IDENTITY_MISUSE_SUSPECTED"))

                self._tenant(connection)
                for table in ("kyc_sessions", "manual_reviews", "consents", "audit_logs", "risk_assessments"):
                    self.assertEqual(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one(), 0)
                self.assertEqual(self._erase_notes(connection, case_b["session"]), 0, "Missing tenant context denies erasure")

                self._tenant(connection, org_a)
                # Exercise the actual service, including its flushed erased_at marker
                # before invoking the restricted SECURITY DEFINER note cleanup.
                with Session(connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as db, db.begin():
                    record = db.scalar(sa.select(KYCSession).where(KYCSession.id == service_case["session"]).with_for_update())
                    report = erase_session(db, TenantContext(org_a, actor_id="phase17-test"), record, uuid4())
                    self.assertEqual(report.deleted["review_notes"], 1)
                    self.assertEqual(set(report.object_refs), {f"test://{record.id}/document", f"test://{record.id}/selfie"})
                    self.assertEqual(record.status, SessionStatus.REJECTED)
                    self.assertEqual(record.user_id, ERASED_USER)
                    self.assertIsNotNone(record.erased_at)
                    self.assertIsNone(record.idempotency_key)
                    self.assertIsNone(record.request_fingerprint)
                    self.assertIsNone(record.client_token_sha256)
                    for model in (*SESSION_TABLES, DocumentImage, DocumentField):
                        self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(model)
                                                   .where(model.session_id == record.id)), 0, model.__tablename__)
                    consents = list(db.scalars(sa.select(Consent).where(Consent.session_id == record.id)))
                    self.assertEqual({row.scope for row in consents}, {"DOCUMENT_PROCESSING", "BIOMETRIC_VERIFICATION"})
                    self.assertTrue(all(row.user_id == ERASED_USER and row.revoked_at is not None for row in consents))
                    self.assertTrue(all(row.policy_version == "test-consent-v1" for row in consents))
                    review = db.get(ManualReview, service_case["review"])
                    self.assertEqual((review.reason_ciphertext, review.key_version), (None, None))
                    self.assertEqual((review.action, review.reason_code), (ReviewAction.REJECT, "IDENTITY_MISUSE_SUSPECTED"))
                    risk = db.get(RiskAssessmentRecord, service_case["risk"])
                    self.assertEqual((risk.decision, risk.reason_codes), (RiskDecision.FAIL, ["DOCUMENT_AUTHENTICITY_FAILED"]))
                    actions = db.scalars(sa.select(AuditLog.action).where(AuditLog.session_id == record.id)).all()
                    self.assertCountEqual(actions, ["REVIEW_REJECTED", "SESSION_DATA_ERASED"])

                # Neither broad table writes nor the stale column ACL can rewrite or
                # remove the append-only audit and decision records.
                columns = {"manual_reviews": "reason_code", "risk_assessments": "policy_version", "audit_logs": "action"}
                identifiers = {"manual_reviews": service_case["review"], "risk_assessments": service_case["risk"],
                               "audit_logs": service_case["audit"]}
                for table in APPEND_ONLY:
                    column = columns[table]
                    for sql in (f"UPDATE {table} SET {column} = {column} WHERE id = :id", f"DELETE FROM {table} WHERE id = :id"):
                        with self.subTest(table=table, sql=sql):
                            with self.assertRaises(sa.exc.ProgrammingError) as refused, connection.begin_nested():
                                connection.execute(sa.text(sql), {"id": identifiers[table]})
                            self.assertEqual(refused.exception.orig.pgcode, "42501")

                connection.exec_driver_sql("RESET ROLE")
                self._tenant(connection, org_b)
                with Session(connection, join_transaction_mode="create_savepoint") as db:
                    self.assertEqual(db.get(ManualReview, case_b["review"]).reason_ciphertext, b"opaque-review-note")
                    self.assertEqual(db.get(KYCSession, case_b["session"]).user_id, "tenant-b-kept")
                    self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(IdentityDocument)
                                               .where(IdentityDocument.session_id == case_b["session"])), 1)
                    consents = list(db.scalars(sa.select(Consent).where(Consent.session_id == case_b["session"])))
                    self.assertTrue(all(row.user_id == "tenant-b-kept" and row.revoked_at is None for row in consents))
        finally:
            try:
                with engine.begin() as connection:
                    if schema_created:
                        connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    if membership_role_created:
                        connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{membership_role}"')
                    if role_created:
                        connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
            finally:
                engine.dispose()
