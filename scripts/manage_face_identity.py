"""Trusted integration/operator entry point; never expose this CLI as a public provisioning API.

Person credentials must be delivered ONLY after your account system authenticates that
same user. A KYC applicant link alone is not authentication. Output files are mode 0600.
See docs/face-identity.md for key generation, role setup, restore and retention procedures.
"""

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import UUID, uuid4
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa
from sqlalchemy.orm import Session
from kyc.core.config import get_settings
from kyc.db.models import FaceActor, FaceEnrollment, FaceSubject, KYCSession, Organization
from kyc.db.privileges import FACE_ROLE, face_grant_statements
from kyc.db.session import set_tenant
from kyc.face_identity.service import PROFILE_FIELDS, PURPOSES, FaceIdentityService, digest, now, profile_context
from kyc.face_identity.lifecycle import maintain
from kyc.main import build_field_cipher


def private_write(path, document):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(document, stream, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init-ledger", "init-role", "person", "device", "operator", "revoke", "suspend", "resume", "maintain", "verify-restore", "checkpoint", "rotate-templates"])
    parser.add_argument("--organization", type=UUID)
    parser.add_argument("--kyc-session", type=UUID)
    parser.add_argument("--authenticated-user-id")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--subject", type=UUID)
    parser.add_argument("--actor", type=UUID)
    parser.add_argument("--branch")
    parser.add_argument("--population", choices=["STAFF", "CUSTOMER"], default="CUSTOMER",
                        help="Trusted subject classification; never accepted from a scanning client")
    parser.add_argument("--operator-populations", nargs="+", choices=["STAFF", "CUSTOMER"], default=["CUSTOMER"])
    parser.add_argument("--duplicate-review", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trusted-checkpoint", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    state = SimpleNamespace(field_cipher=build_field_cipher(settings), biometric_cipher=None,
                            face_engine=None, session_factory=None, capture_store=None)
    service = FaceIdentityService(settings, state)
    try:
        if args.action == "init-ledger":
            if not service.ledger:
                parser.error("Configure FACE_RECOGNITION_LEDGER_PATH and FACE_RECOGNITION_LEDGER_KEY first.")
            service.ledger.initialize()
            print("Independent deletion ledger initialized. Keep it outside database backup/restore sets.")
            return
        if args.action == "checkpoint":
            if not args.output:
                parser.error("--output is required")
            service.ledger.entries()
            checkpoint = json.loads(service.ledger.path.with_suffix(service.ledger.path.suffix + '.checkpoint').read_text())
            private_write(args.output, checkpoint)
            print("Exported checkpoint. Store it in independently immutable operator storage.")
            return
        if not settings.migration_database_url:
            parser.error("MIGRATION_DATABASE_URL is required for this trusted administration tool.")
        engine = sa.create_engine(settings.migration_database_url.get_secret_value(), pool_pre_ping=True, hide_parameters=True)
        try:
            if args.action == "init-role":
                if engine.dialect.name != "postgresql" or not settings.face_recognition_database_url:
                    parser.error("Configure the separate FACE_RECOGNITION_DATABASE_URL first.")
                url = sa.engine.make_url(settings.face_recognition_database_url.get_secret_value())
                if url.username != FACE_ROLE or not url.password or len(url.password) < 32:
                    parser.error("Use the kyc_biometrics login and a generated password of at least 32 characters.")
                with engine.begin() as connection:
                    exists = connection.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": FACE_ROLE})
                    statement = ("ALTER" if exists else "CREATE") + f' ROLE "{FACE_ROLE}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %s'
                    connection.exec_driver_sql(statement, (url.password,))
                    for statement in face_grant_statements():
                        connection.exec_driver_sql(statement)
                print("Restricted biometric database login configured. Run the security checks before deployment.")
                return
            if not args.organization:
                parser.error("--organization is required")
            with Session(engine) as db, db.begin():
                set_tenant(db, args.organization)
                if engine.dialect.name == "postgresql":
                    db.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                               {"scope": "face-identity/" + str(args.organization)})
                if args.action in {"maintain", "verify-restore"}:
                    if args.action == "verify-restore":
                        if not args.trusted_checkpoint:
                            parser.error("--trusted-checkpoint from independent immutable storage is required.")
                        trusted = json.loads(args.trusted_checkpoint.read_text())
                        entries = service.ledger.entries()
                        if len(entries) < trusted["sequence"]:
                            raise SystemExit("Restore refused: deletion journal is older than the trusted checkpoint.")
                        # Verify the externally trusted checkpoint against the HMAC chain.
                        if trusted["sequence"]:
                            lines = service.ledger.path.read_bytes().splitlines()
                            if json.loads(lines[trusted["sequence"] - 1])["mac"] != trusted["mac"]:
                                raise SystemExit("Restore refused: deletion journal checkpoint differs.")
                    print(json.dumps(maintain(db, service, args.organization)))
                    return
                if args.action == "rotate-templates":
                    from kyc.db.models import FaceTemplate
                    count = 0
                    for enrollment, template in db.execute(sa.select(FaceEnrollment, FaceTemplate).join(FaceTemplate,
                        sa.and_(FaceTemplate.organization_id == FaceEnrollment.organization_id, FaceTemplate.enrollment_id == FaceEnrollment.id))
                        .where(FaceEnrollment.organization_id == args.organization)):
                        from kyc.face_identity.service import context
                        aad = context(enrollment, "enrollment", enrollment.model_version)
                        payload = service.cipher.open(template.ciphertext, template.key_version, aad)
                        template.ciphertext, template.key_version = service.cipher.seal(payload, aad)
                        count += 1
                    print(json.dumps({"resealed_templates": count}))
                    return
                if args.action == "revoke":
                    actor = db.scalar(sa.select(FaceActor).where(FaceActor.organization_id == args.organization, FaceActor.id == args.actor))
                    if actor is None:
                        parser.error("No such registered actor in this tenant.")
                    actor.revoked_at = now()
                    print("Actor/device credential revoked.")
                    return
                if args.action in {"suspend", "resume"}:
                    subject = db.scalar(sa.select(FaceSubject).where(FaceSubject.organization_id == args.organization, FaceSubject.id == args.subject))
                    if subject is None:
                        parser.error("No such subject in this tenant.")
                    subject.active = args.action == "resume"
                    print("Subject status updated.")
                    return
                if not args.output:
                    parser.error("--output is required; credentials are never printed.")
                subject = None
                if args.action == "person":
                    kyc = db.scalar(sa.select(KYCSession).where(KYCSession.organization_id == args.organization,
                                                              KYCSession.id == args.kyc_session))
                    if (kyc is None or kyc.status.value != "VERIFIED" or kyc.erased_at
                            or not args.authenticated_user_id or kyc.user_id != args.authenticated_user_id):
                        parser.error("A VERIFIED KYC session belonging to the authenticated account is required.")
                    subject = db.scalar(sa.select(FaceSubject).where(FaceSubject.organization_id == args.organization,
                                                                    FaceSubject.kyc_session_id == kyc.id))
                    if subject is None:
                        subject = FaceSubject(id=uuid4(), organization_id=args.organization, kyc_session_id=kyc.id,
                                              population=args.population)
                        db.add(subject)
                    elif subject.population != args.population:
                        parser.error("Existing subject classification differs; use an authorized reviewed migration, not a scan request.")
                    if args.profile:
                        profile = json.loads(args.profile.read_text())
                        if (not isinstance(profile, dict) or set(profile) - (PROFILE_FIELDS - {"kyc_status"})
                                or any(not isinstance(value, str) or len(value) > 200 for value in profile.values())):
                            parser.error("Profile accepts only minimal permitted fields; no document numbers, dates, addresses or images.")
                        if not state.field_cipher:
                            parser.error("Configure the existing PII field encryption keyring first.")
                        subject.profile_ciphertext, subject.profile_key_version = state.field_cipher.seal(json.dumps(profile), profile_context(subject))
                        subject.profile_expires_at = now() + timedelta(days=db.get(Organization, args.organization).pii_retention_days)
                    db.flush()
                kind = {"person": "PERSON", "device": "KIOSK", "operator": "OPERATOR"}[args.action]
                if kind == "KIOSK" and not args.branch:
                    parser.error("Registered kiosks require --branch.")
                purposes = list(PURPOSES[kind] - {"duplicate_resolution"})
                if kind == "OPERATOR" and args.duplicate_review:
                    purposes.append("duplicate_resolution")
                populations = ([subject.population] if kind == "PERSON" else ["STAFF"] if kind == "KIOSK"
                               else ["STAFF", "CUSTOMER"] if args.duplicate_review else args.operator_populations)
                token = "fri_" + secrets.token_urlsafe(32)
                actor = FaceActor(id=uuid4(), organization_id=args.organization, subject_id=subject.id if subject else None,
                    kind=kind, token_hash=digest(token), purposes=purposes, populations=populations, profile_fields=sorted(PROFILE_FIELDS),
                    branch=args.branch, expires_at=now() + timedelta(minutes=10 if kind == "PERSON" else 480 if kind == "KIOSK" else 60))
                db.add(actor)
                private_write(args.output, {"organizationId": str(args.organization), "token": token,
                                           "actorId": str(actor.id), "expiresAt": actor.expires_at.isoformat()})
                print("Credential written privately. Deliver it only through the authenticated account/device channel.")
        finally:
            engine.dispose()
    finally:
        if service.engine is not None:
            service.engine.dispose()


if __name__ == "__main__":
    main()
