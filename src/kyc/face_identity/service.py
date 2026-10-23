"""Tenant-serialized enrollment, live capture, exact identification and bounded disclosure.

Raw camera pixels never persist. Accepted sample embeddings/geometry are encrypted until
completion/expiry, then destroyed. Every session and reference belongs to one actor.
"""

import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
import numpy as np
from PIL import Image
import sqlalchemy as sa

from kyc.biometrics.embeddings import serialize_embedding, deserialize_embedding
from kyc.biometrics.types import FaceEmbedding, FaceEngineUnavailable, InvalidFaceEmbedding
from kyc.core.crypto import decode_key
from kyc.db.models import (BiometricTemplate, FaceActor, FaceCaptureSession, FaceConsent, FaceDeletion,
    FaceEnrollment, FaceRecognitionEvent, FaceSampleReceipt, FaceSubject, FaceTemplate, KYCSession)
from kyc.engines.capture_quality import CaptureRejected, decode_capture
from kyc.liveness.active import ActiveLivenessPolicy, guide
from kyc.liveness.challenge import issue, INSTRUCTIONS
from kyc.liveness.geometry import PoseSample, median_pose, pose
from kyc.services.biometrics import _template_context
from kyc.services.sessions import aware
from kyc.storage.biometrics import BiometricCipher
from kyc.db.session import build_engine
from sqlalchemy.orm import sessionmaker
from .ledger import DeletionLedger
from .policy import decide, load_policy, canonical

PROFILE_FIELDS = frozenset({"display_name", "internal_reference", "department", "membership_status", "kyc_status"})
KIOSK_FIELDS = frozenset({"display_name", "internal_reference", "department", "kyc_status"})
PURPOSES = {"PERSON": {"self_profile"}, "KIOSK": {"staff_lookup"},
            "OPERATOR": {"customer_service", "duplicate_resolution"}}
OUTCOMES = {"MATCHED", "NO_MATCH", "AMBIGUOUS", "RETRY_REQUIRED", "LIVENESS_FAILED", "EXPIRED", "TECHNICAL_ERROR"}
RECOVERY = "We could not confirm your identity. Try again or use your QR code."


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def error(status, code):
    # Coded errors contain no submitted identifiers, profile fields, or model scores.
    raise HTTPException(status, detail=code)


def context(row, purpose, model="v1"):
    return f"biometric/{row.organization_id}/{row.id}/face-identity/{purpose}/{model}/sealed/v1"


def profile_context(subject):
    return f"face-profile/{subject.organization_id}/{subject.id}/permitted/v1"


def pack_pose(sample):
    return [sample.a, sample.b, sample.aspect, sample.scale, *sample.centre]


def unpack_pose(values):
    return PoseSample(*values[:4], tuple(values[4:]))


class FaceIdentityService:
    def __init__(self, settings, state):
        self.settings, self.state = settings, state
        try:
            self.cipher = BiometricCipher(settings.face_recognition_keys.get_secret_value()) if settings.face_recognition_keys else None
        except ValueError:
            self.cipher = None  # Optional feature misconfiguration must not break ordinary KYC.
        try:
            self.ledger = (DeletionLedger(settings.face_recognition_ledger_path,
                           decode_key(settings.face_recognition_ledger_key.get_secret_value()))
                           if settings.face_recognition_ledger_path and settings.face_recognition_ledger_key else None)
        except ValueError:
            self.ledger = None
        self.policy, self.approval_expiry = None, None
        self.metrics = Counter()  # labels are fixed outcome codes only; never actor/subject/device IDs
        self.engine = None
        self.database_configuration_error = False
        self.factory = state.session_factory
        if settings.face_recognition_database_url:
            try:
                self.engine = build_engine(settings.model_copy(update={"database_url": settings.face_recognition_database_url}))
                self.factory = sessionmaker(self.engine, expire_on_commit=False)
            except (ValueError, sa.exc.ArgumentError):
                self.database_configuration_error = True

    def readiness(self):
        reasons = []
        if not self.settings.face_recognition_enabled:
            reasons.append("FEATURE_DISABLED")
        if self.settings.face_recognition_policy:
            try:
                self.policy, self.approval_expiry = load_policy(self.settings.face_recognition_policy,
                                                              self.settings.face_recognition_approval_key)
            except Exception:
                self.policy = None
                reasons.append("APPROVED_IDENTIFICATION_POLICY_REQUIRED")
        if self.policy is None or self.approval_expiry is None or self.approval_expiry <= now():
            reasons.append("APPROVED_IDENTIFICATION_POLICY_REQUIRED")
        if self.cipher is None or self.state.field_cipher is None:
            reasons.append("INDEPENDENT_ENCRYPTION_KEYS_REQUIRED")
        if self.settings.environment != "test" and not self.settings.face_recognition_database_url:
            reasons.append("BIOMETRIC_SERVICE_DATABASE_ROLE_REQUIRED")
        if self.database_configuration_error:
            reasons.append("BIOMETRIC_DATABASE_UNAVAILABLE")
        if self.engine is not None:
            try:
                with self.engine.connect() as connection:
                    role = connection.execute(sa.text("SELECT current_user, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                        "FROM pg_roles WHERE rolname = current_user")).first()
                    if role is None or role[0] != "kyc_biometrics" or any(role[1:]):
                        reasons.append("BIOMETRIC_SERVICE_DATABASE_ROLE_REQUIRED")
                    if self.settings.environment == "production" and not connection.scalar(sa.text(
                        "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")):
                        reasons.append("BIOMETRIC_DATABASE_TLS_REQUIRED")
            except Exception:
                reasons.append("BIOMETRIC_DATABASE_UNAVAILABLE")
        if self.cipher:
            face_keys = set(self.cipher.keys.values())
            for existing in (self.state.biometric_cipher, self.state.field_cipher, self.state.capture_store):
                if existing and face_keys.intersection(getattr(existing, "keys", {}).values()):
                    reasons.append("INDEPENDENT_ENCRYPTION_KEYS_REQUIRED")
            if self.ledger and self.ledger.key in face_keys:
                reasons.append("INDEPENDENT_LEDGER_KEY_REQUIRED")
        engine = self.state.face_engine
        if (not callable(getattr(engine, "visibility", None)) or not getattr(engine, "occlusion_supported", False)
                or self.policy and getattr(engine, "visibility_provider", None) != self.policy.visibility_provider):
            reasons.append("VALIDATED_VISIBILITY_PROVIDER_REQUIRED")
        if engine is None or engine.unavailable_reason():
            reasons.append("FACE_MODELS_UNAVAILABLE")
        try:
            if self.ledger is None:
                raise ValueError()
            self.ledger.entries()
        except Exception:
            reasons.append("INDEPENDENT_DELETION_LEDGER_REQUIRED")
        return {"ready": not reasons, "reason_codes": sorted(set(reasons)),
                "production_ready": not reasons and self.settings.environment == "production",
                "feature": "optional-face-identification"}

    def require_ready(self):
        if not self.readiness()["ready"]:
            error(503, "FACE_IDENTIFICATION_NOT_READY")
        return self.policy

    def _seal(self, row, value, purpose):
        return self.cipher.seal(canonical(value), context(row, purpose))

    def _open(self, row, value, key, purpose):
        return json.loads(self.cipher.open(value, key, context(row, purpose)))

    def subject(self, db, actor, *, verified=True):
        subject = db.scalar(sa.select(FaceSubject).where(FaceSubject.organization_id == actor.organization_id,
                                                       FaceSubject.id == actor.subject_id))
        if subject is None or not subject.active:
            error(403, "SUBJECT_NOT_AVAILABLE")
        kyc = db.scalar(sa.select(KYCSession).where(KYCSession.organization_id == actor.organization_id,
                                                   KYCSession.id == subject.kyc_session_id))
        if verified and (kyc is None or kyc.status.value != "VERIFIED" or kyc.erased_at):
            error(403, "VERIFIED_KYC_REQUIRED")
        return subject, kyc

    def _event(self, db, actor, session, outcome, reasons=(), evidence=None):
        event = FaceRecognitionEvent(id=uuid4(), organization_id=actor.organization_id, actor_id=actor.id,
            capture_session_id=session.id if session else None, outcome=outcome, reason_codes=list(reasons),
            purpose=session.purpose if session else "consent_management",
            policy_version=session.policy_version if session else "lifecycle-v1",
            model_version=self.policy.model_id if self.policy else "unavailable")
        if evidence is not None:
            event.evidence_ciphertext, event.evidence_key_version = self._seal(event, evidence, "review-evidence")
        db.add(event)
        if outcome in OUTCOMES:
            self.metrics[outcome] += 1

    def capabilities(self, db, actor):
        subject, kyc = self.subject(db, actor) if actor.kind == "PERSON" else (None, None)
        readiness = self.readiness()
        policy = self.policy
        if policy and not set(actor.populations) <= set(policy.populations):
            readiness = {**readiness, "ready": False, "production_ready": False,
                         "reason_codes": readiness["reason_codes"] + ["POPULATION_NOT_VALIDATED"]}
        return {**readiness, "actor_kind": actor.kind, "kyc_status": kyc.status.value if kyc else None,
            "enrollment_allowed": actor.kind == "PERSON" and bool(subject) and readiness["ready"],
            "retention_days": policy.retention_days if policy else None,
            "backup_retention_days": policy.backup_retention_days if policy else None,
            "notice_version": policy.notice_version if policy else None,
            "permitted_fields": sorted(set(actor.profile_fields) & PROFILE_FIELDS),
            "purposes": actor.purposes, "alternative": "QR_CODE"}

    def create(self, db, actor, kind, purpose, notice=None, consent=False):
        policy = self.require_ready()
        if not set(actor.populations) <= set(policy.populations):
            error(503, "POPULATION_NOT_VALIDATED")
        consent_record = None
        if kind == "ENROLLMENT":
            if actor.kind != "PERSON" or (now() - aware(actor.created_at)).total_seconds() > 600:
                error(403, "FRESH_PERSON_AUTHENTICATION_REQUIRED")
            subject, _ = self.subject(db, actor)
            if consent is not True or notice != policy.notice_version:
                error(422, "CURRENT_OPTIONAL_CONSENT_REQUIRED")
            # Pending/retried sessions cannot reuse a withdrawn grant.
            consent_record = FaceConsent(id=uuid4(), organization_id=actor.organization_id, subject_id=subject.id,
                actor_id=actor.id, purpose="FUTURE_FACE_RECOGNITION", notice_version=notice,
                expires_at=now() + timedelta(days=policy.retention_days))
            db.add(consent_record)
            db.flush()
            purpose = "self_profile"
        elif purpose not in PURPOSES.get(actor.kind, set()) or purpose not in actor.purposes or purpose == "duplicate_resolution":
            error(403, "PURPOSE_NOT_AUTHORIZED")
        recent = db.scalar(sa.select(sa.func.count()).select_from(FaceCaptureSession).where(
            FaceCaptureSession.organization_id == actor.organization_id, FaceCaptureSession.actor_id == actor.id,
            FaceCaptureSession.created_at >= now() - timedelta(minutes=1)))
        if recent >= 5:
            error(429, "FACE_ATTEMPTS_RATE_LIMITED")
        ticket = secrets.token_urlsafe(32)
        session = FaceCaptureSession(id=uuid4(), organization_id=actor.organization_id, actor_id=actor.id,
            consent_id=consent_record.id if consent_record else None, kind=kind, purpose=purpose,
            policy_version=policy.binding, steps=list(issue().steps), step_index=0, status="CAPTURING",
            ticket_hash=digest(ticket), ticket_issued_at=now(), expires_at=now() + timedelta(seconds=policy.session_seconds))
        db.add(session)
        db.flush()
        self._event(db, actor, session, "SESSION_CREATED")
        return self.capture_view(session, ticket, "POSITIONING", 0)

    def session(self, db, actor, identifier, kind):
        session = db.scalar(sa.select(FaceCaptureSession).where(FaceCaptureSession.organization_id == actor.organization_id,
            FaceCaptureSession.actor_id == actor.id, FaceCaptureSession.id == identifier, FaceCaptureSession.kind == kind)
            .with_for_update())
        if session is None:
            error(404, "FACE_SESSION_NOT_FOUND")
        return session

    @staticmethod
    def capture_view(session, ticket, state, held, instructions=()):
        step = session.steps[session.step_index] if session.step_index < len(session.steps) else None
        return {"session_id": str(session.id), "expires_at": aware(session.expires_at).isoformat(),
            "step": step, "instruction": INSTRUCTIONS.get(step, "Capture complete."),
            "step_index": session.step_index, "steps_total": len(session.steps), "held": held,
            "required": 2, "state": state, "ticket": ticket, "instructions": list(instructions)}

    def _expired(self, db, actor, session):
        if session.status == "EXPIRED" and session.result:
            return session.result
        session.status = "EXPIRED"
        session.sample_ciphertext = session.sample_key_version = None
        session.reference_hash = None
        session.result = {"outcome": "EXPIRED", "message": RECOVERY}
        self._event(db, actor, session, "EXPIRED", ["SESSION_EXPIRED"])
        return session.result

    def frame(self, db, actor, session, ticket, data):
        policy = self.require_ready()
        if aware(session.expires_at) <= now():
            return self._expired(db, actor, session)
        if session.status != "CAPTURING" or session.step_index >= len(session.steps):
            error(409, "CAPTURE_SESSION_CLOSED")
        if session.policy_version != policy.binding:
            error(409, "POLICY_CHANGED_RESTART_REQUIRED")
        if not hmac.compare_digest(session.ticket_hash, digest(ticket)):
            error(409, "FRAME_TICKET_USED_OR_INVALID")
        # Tickets rotate after EVERY checked frame, including recovery responses.
        old_ticket_time = aware(session.ticket_issued_at)
        next_ticket = secrets.token_urlsafe(32)
        session.ticket_hash, session.ticket_issued_at = digest(next_ticket), now()
        if now() - old_ticket_time > timedelta(seconds=15):
            return self.capture_view(session, next_ticket, "RETRY_REQUIRED", 0, ["FRESH_CAPTURE_REQUIRED"])
        if session.last_frame_at and now() - aware(session.last_frame_at) < timedelta(milliseconds=200):
            return self.capture_view(session, next_ticket, "RETRY_REQUIRED", 0, ["HOLD_STILL"])
        sample_digest = hmac.new(self.ledger.key, str(actor.organization_id).encode() + b"/frame/" + data,
                                 hashlib.sha256).hexdigest()
        reused = db.scalar(sa.select(FaceSampleReceipt.id).where(FaceSampleReceipt.organization_id == actor.organization_id,
            FaceSampleReceipt.digest == sample_digest))
        if reused:
            session.status = "LIVENESS_FAILED"
            session.sample_ciphertext = session.sample_key_version = None
            session.result = {"outcome": "LIVENESS_FAILED", "message": RECOVERY}
            self._event(db, actor, session, "LIVENESS_FAILED", ["REUSED_CAMERA_FRAME"])
            return session.result
        db.add(FaceSampleReceipt(organization_id=actor.organization_id, digest=sample_digest,
                                expires_at=now() + timedelta(hours=24)))
        session.frame_count += 1
        if session.frame_count > 96:
            return self._expired(db, actor, session)
        payload = self._open(session, session.sample_ciphertext, session.sample_key_version, "capture") if session.sample_ciphertext else {"completed": [], "held": []}
        if session.last_frame_at and now() - aware(session.last_frame_at) > timedelta(seconds=2):
            payload["held"] = []
        session.last_frame_at = now()
        instructions, state = [], "RETRY_REQUIRED"
        try:
            capture = decode_capture(data, max_bytes=self.settings.max_selfie_bytes, max_pixels=self.settings.max_selfie_pixels)
            image = Image.fromarray(capture.pixels)
            faces = self.state.face_engine.detect(image)
            if len(faces) != 1:
                instructions = ["ONE_FACE_ONLY" if faces else "CENTER_FACE"]
            else:
                name = session.steps[session.step_index]
                quality = self.state.face_engine.visibility(image, faces[0], frontal=name == "LOOK_STRAIGHT")
                if not quality.accepted or quality.unverified_checks:
                    instructions = list(quality.instructions) or ["FACE_VISIBILITY_UNVERIFIED"]
                else:
                    sample = pose(faces[0].landmarks)
                    base_samples = payload["completed"][0] if payload["completed"] else payload["held"]
                    baseline = median_pose([unpack_pose(item["pose"]) for item in base_samples]) if base_samples else sample
                    advice = guide(baseline, sample, name, ActiveLivenessPolicy(movement=policy.movement))
                    state = advice.state
                    if name == "LOOK_STRAIGHT" and abs(sample.a) > policy.movement / 2:
                        state = "NOT_CENTERED"
                    if state == ("CENTERED" if name == "LOOK_STRAIGHT" else "DONE"):
                        embedding = self.state.face_engine.embed(image, faces[0])
                        if f"{embedding.model_name}:{embedding.model_version}:{embedding.model_sha256}" != policy.model_id:
                            raise InvalidFaceEmbedding("Model mismatch")
                        payload["held"].append({"pose": pack_pose(sample),
                            "embedding": base64.b64encode(serialize_embedding(embedding)).decode(),
                            "quality": dict(quality.scores)})
        except CaptureRejected as rejected:
            instructions = [rejected.reason_code]
        except (FaceEngineUnavailable, InvalidFaceEmbedding, ValueError):
            instructions = ["CAMERA_CHECK_UNAVAILABLE"]
            state = "TECHNICAL_ERROR"
        if aware(session.expires_at) <= now():
            return self._expired(db, actor, session)
        if state not in ("CENTERED", "DONE"):
            payload["held"] = []
        held = len(payload["held"])
        if held >= 2:
            payload["completed"].append(payload["held"])
            payload["held"] = []
            session.step_index += 1
            if session.step_index == len(session.steps):
                session.status = "READY"
                state = "READY_TO_COMPLETE"
            else:
                state = "STEP_COMPLETE"
        session.sample_ciphertext, session.sample_key_version = self._seal(session, payload, "capture")
        return self.capture_view(session, next_ticket, state, held, instructions)

    def _eligible(self, db, tenant, actor=None):
        policy = self.policy
        denied = self.ledger.denied(tenant)
        pending = db.scalars(sa.select(FaceDeletion.enrollment_id).where(FaceDeletion.organization_id == tenant,
                                                                       FaceDeletion.ledger_synced.is_(False)))
        if any(str(identifier) not in denied for identifier in pending):
            error(503, "DELETION_LEDGER_SYNC_REQUIRED")
        population_scope = [FaceSubject.population.in_(policy.populations)]
        if actor:
            population_scope.append(FaceSubject.population.in_(actor.populations))
        if actor and actor.kind == "PERSON":
            population_scope.append(FaceSubject.id == actor.subject_id)
        if actor and actor.application_id is not None:
            from kyc.db.models import VerificationBinding
            # Public integration recognition never searches synthetic or legacy/unbound enrollments.
            population_scope.append(sa.exists(sa.select(VerificationBinding.id).where(
                VerificationBinding.organization_id == tenant, VerificationBinding.session_id == KYCSession.id,
                VerificationBinding.mode == "production", VerificationBinding.technical_error.is_(False))))
        rows = db.execute(sa.select(FaceEnrollment, FaceTemplate).join(FaceTemplate, sa.and_(
            FaceTemplate.organization_id == FaceEnrollment.organization_id, FaceTemplate.enrollment_id == FaceEnrollment.id))
            .join(FaceConsent, sa.and_(FaceConsent.organization_id == FaceEnrollment.organization_id, FaceConsent.id == FaceEnrollment.consent_id))
            .join(FaceSubject, sa.and_(FaceSubject.organization_id == FaceEnrollment.organization_id, FaceSubject.id == FaceEnrollment.subject_id))
            .join(KYCSession, sa.and_(KYCSession.organization_id == FaceSubject.organization_id, KYCSession.id == FaceSubject.kyc_session_id))
            .where(FaceEnrollment.organization_id == tenant, FaceEnrollment.status == "ACTIVE",
                FaceEnrollment.expires_at > now(), FaceEnrollment.model_version == policy.model_id,
                FaceEnrollment.policy_version == policy.binding, FaceConsent.withdrawn_at.is_(None),
                FaceConsent.subject_id == FaceEnrollment.subject_id,
                FaceEnrollment.id.not_in([UUID(identifier) for identifier in denied]),
                FaceConsent.expires_at > now(), FaceConsent.notice_version == policy.notice_version,
                FaceSubject.active.is_(True), KYCSession.status == "VERIFIED", KYCSession.erased_at.is_(None), *population_scope)
            .limit(policy.max_population + 1)).all()
        rows = [(enrollment, template) for enrollment, template in rows if str(enrollment.id) not in denied]
        if len(rows) > policy.max_population:
            error(503, "POPULATION_EXCEEDS_VALIDATED_CAPACITY")
        return rows

    def _vector(self, sample):
        return deserialize_embedding(base64.b64decode(sample["embedding"], validate=True))

    def _template(self, enrollment, template):
        embedding = deserialize_embedding(self.cipher.open(template.ciphertext, template.key_version,
                                                           context(enrollment, "enrollment", enrollment.model_version)))
        if f"{embedding.model_name}:{embedding.model_version}:{embedding.model_sha256}" != self.policy.model_id:
            raise InvalidFaceEmbedding("Model mismatch")
        return embedding

    def _finish(self, db, actor, session, outcome, reasons=(), evidence=None):
        session.status = "COMPLETED"
        session.sample_ciphertext = session.sample_key_version = None
        session.result = {"outcome": outcome, "message": "Face recognized." if outcome == "MATCHED" else RECOVERY}
        self._event(db, actor, session, outcome, reasons, evidence)
        return session.result

    def complete(self, db, actor, session, key):
        policy = self.require_ready()
        if aware(session.expires_at) <= now():
            return self._expired(db, actor, session)
        if session.completion_key:
            if session.completion_key != key:
                error(409, "COMPLETION_KEY_CONFLICT")
            return session.result
        if session.policy_version != policy.binding:
            error(409, "POLICY_CHANGED_RESTART_REQUIRED")
        if session.status != "READY" or session.last_frame_at is None or now() - aware(session.last_frame_at) > timedelta(seconds=10):
            error(409, "FRESH_COMPLETE_CAPTURE_REQUIRED")
        session.completion_key = key
        try:
            payload = self._open(session, session.sample_ciphertext, session.sample_key_version, "capture")
            samples = payload["completed"]
            baseline = median_pose([unpack_pose(item["pose"]) for item in samples[0]])
            if len(samples) != len(session.steps) or any(len(group) != 2 for group in samples):
                return self._finish(db, actor, session, "LIVENESS_FAILED", ["INCOMPLETE_CHALLENGE"])
            for name, group in zip(session.steps, samples):
                if any(guide(baseline, unpack_pose(item["pose"]), name, ActiveLivenessPolicy(movement=policy.movement)).state
                       != ("CENTERED" if name == "LOOK_STRAIGHT" else "DONE") for item in group):
                    return self._finish(db, actor, session, "LIVENESS_FAILED", ["MOVEMENT_NOT_CONFIRMED"])
            # One best frontal vector is the probe. Repeated correlated frames do
            # not multiply confidence or get counted as independent votes.
            chosen = max(samples[0], key=lambda item: min(item["quality"].values(), default=0))
            probe = self._vector(chosen)
            vectors = [self._vector(item) for group in samples for item in group]
            if any(float(np.dot(probe.vector, vector.vector)) < policy.verification_threshold for vector in vectors):
                return self._finish(db, actor, session, "LIVENESS_FAILED", ["IDENTITY_CONTINUITY_FAILED"])
            scores, candidates = [], {}
            # Duplicate quarantine is a private tenant-wide biometric-service check.
            # Identification and every subsequent disclosure use the actor's population.
            for enrollment, template in self._eligible(db, actor.organization_id,
                                                       actor if session.kind == "RECOGNITION" else None):
                score = float(np.dot(probe.vector, self._template(enrollment, template).vector))
                scores.append((enrollment.id, score))
                candidates[enrollment.id] = enrollment
            if session.kind == "ENROLLMENT":
                return self._enroll(db, actor, session, probe, chosen["quality"], scores, candidates)
            outcome, identifier = decide(scores, policy)
            if identifier and actor.kind == "PERSON" and candidates[identifier].subject_id != actor.subject_id:
                outcome, identifier = "NO_MATCH", None
            session.matched_enrollment_id = identifier
            return self._finish(db, actor, session, outcome, [outcome],
                {"candidates": [[str(identifier), score] for identifier, score in scores],
                 "capture_frames": len(vectors), "probe_policy": "BEST_FRONTAL_SINGLE_PROBE"})
        except (InvalidTag, InvalidFaceEmbedding, ValueError, KeyError, TypeError):
            return self._finish(db, actor, session, "TECHNICAL_ERROR", ["BIOMETRIC_EVIDENCE_UNAVAILABLE"])

    def _enroll(self, db, actor, session, probe, quality, scores, candidates):
        subject, kyc = self.subject(db, actor)
        consent = db.scalar(sa.select(FaceConsent).where(FaceConsent.organization_id == actor.organization_id,
                                                       FaceConsent.id == session.consent_id))
        if consent is None or consent.subject_id != subject.id or consent.withdrawn_at or aware(consent.expires_at) <= now():
            error(403, "ACTIVE_CONSENT_REQUIRED")
        reference = db.scalar(sa.select(BiometricTemplate).where(BiometricTemplate.organization_id == actor.organization_id,
            BiometricTemplate.session_id == subject.kyc_session_id, BiometricTemplate.source == "DOCUMENT_PORTRAIT",
            BiometricTemplate.delete_after > now()).order_by(BiometricTemplate.created_at.desc()).limit(1))
        if reference is None or self.state.biometric_cipher is None:
            return self._finish(db, actor, session, "RETRY_REQUIRED", ["VERIFIED_REFERENCE_UNAVAILABLE"])
        embedding = deserialize_embedding(self.state.biometric_cipher.open(reference.template_ciphertext, reference.key_version,
            _template_context(kyc, reference.id, "DOCUMENT_PORTRAIT", reference)))
        if (embedding.model_name, embedding.model_version, embedding.model_sha256) != (probe.model_name, probe.model_version, probe.model_sha256):
            return self._finish(db, actor, session, "TECHNICAL_ERROR", ["REFERENCE_MODEL_INCOMPATIBLE"])
        if float(np.dot(probe.vector, embedding.vector)) < self.policy.verification_threshold:
            return self._finish(db, actor, session, "NO_MATCH", ["VERIFIED_REFERENCE_NOT_MATCHED"])
        duplicate = any(score >= self.policy.duplicate_threshold and candidates[identifier].subject_id != subject.id
                        for identifier, score in scores)
        enrollment = FaceEnrollment(id=uuid4(), organization_id=actor.organization_id, subject_id=subject.id,
            consent_id=consent.id, status="DUPLICATE_REVIEW" if duplicate else "ACTIVE", model_version=self.policy.model_id,
            policy_version=self.policy.binding, expires_at=consent.expires_at)
        if not duplicate:
            self._replace(db, actor, subject.id)
        db.add(enrollment)
        db.flush()
        ciphertext, version = self.cipher.seal(serialize_embedding(probe), context(enrollment, "enrollment", enrollment.model_version))
        db.add(FaceTemplate(organization_id=actor.organization_id, enrollment_id=enrollment.id, ciphertext=ciphertext,
                            key_version=version, quality_metadata=quality))
        session.sample_ciphertext = session.sample_key_version = None
        session.status = "COMPLETED"
        session.result = {"status": enrollment.status,
            "message": "Your enrollment needs an authorized review." if duplicate else "Face recognition enabled.",
            "expires_at": aware(enrollment.expires_at).isoformat()}
        self._event(db, actor, session, "DUPLICATE_REVIEW" if duplicate else "ENROLLED", [],
                    {"enrollment": str(enrollment.id), "duplicate_candidates": [[str(i), s] for i, s in scores
                     if candidates[i].subject_id != subject.id and s >= self.policy.duplicate_threshold]})
        return session.result

    def revoke(self, db, enrollment, reason="WITHDRAWAL", status="REVOKED"):
        synced = False
        try:
            if self.ledger:
                self.ledger.append(enrollment.organization_id, enrollment.id, reason)
                synced = True
        except Exception:
            pass  # Still disable/delete immediately; readiness denies an unavailable ledger.
        tombstone = db.scalar(sa.select(FaceDeletion).where(FaceDeletion.organization_id == enrollment.organization_id,
                                                          FaceDeletion.enrollment_id == enrollment.id))
        if tombstone is None:
            db.add(FaceDeletion(organization_id=enrollment.organization_id, enrollment_id=enrollment.id,
                                reason=reason, ledger_synced=synced))
        enrollment.status = status
        db.execute(sa.delete(FaceTemplate).where(FaceTemplate.organization_id == enrollment.organization_id,
                                                FaceTemplate.enrollment_id == enrollment.id))
        db.execute(sa.update(FaceCaptureSession).where(FaceCaptureSession.organization_id == enrollment.organization_id,
            FaceCaptureSession.matched_enrollment_id == enrollment.id).values(reference_hash=None))

    def _replace(self, db, actor, subject_id):
        for old in db.scalars(sa.select(FaceEnrollment).where(FaceEnrollment.organization_id == actor.organization_id,
            FaceEnrollment.subject_id == subject_id, FaceEnrollment.status == "ACTIVE")):
            self.revoke(db, old, "REENROLLMENT", "SUPERSEDED")
        db.flush()

    def withdraw(self, db, actor):
        if actor.kind != "PERSON":
            error(403, "PERSON_AUTHENTICATION_REQUIRED")
        for consent in db.scalars(sa.select(FaceConsent).where(FaceConsent.organization_id == actor.organization_id,
            FaceConsent.subject_id == actor.subject_id, FaceConsent.withdrawn_at.is_(None))):
            consent.withdrawn_at = now()
        for enrollment in db.scalars(sa.select(FaceEnrollment).where(FaceEnrollment.organization_id == actor.organization_id,
            FaceEnrollment.subject_id == actor.subject_id, FaceEnrollment.status.in_(["ACTIVE", "DUPLICATE_REVIEW"]))):
            self.revoke(db, enrollment)
        # A consent withdrawal also invalidates every outstanding enrollment session for this person.
        actors = sa.select(FaceActor.id).where(FaceActor.organization_id == actor.organization_id, FaceActor.subject_id == actor.subject_id)
        db.execute(sa.update(FaceCaptureSession).where(FaceCaptureSession.organization_id == actor.organization_id,
            FaceCaptureSession.actor_id.in_(actors), FaceCaptureSession.kind == "ENROLLMENT",
            FaceCaptureSession.status.in_(["CAPTURING", "READY"])).values(status="REVOKED", sample_ciphertext=None, sample_key_version=None))
        self._event(db, actor, None, "CONSENT_WITHDRAWN")
        return {"status": "DISABLED", "message": "Face recognition disabled. Your KYC result is unchanged."}

    def enrollment_status(self, db, actor):
        if actor.kind != "PERSON":
            error(403, "PERSON_AUTHENTICATION_REQUIRED")
        row = db.scalar(sa.select(FaceEnrollment).where(FaceEnrollment.organization_id == actor.organization_id,
            FaceEnrollment.subject_id == actor.subject_id).order_by(FaceEnrollment.created_at.desc()).limit(1))
        active = row is not None and row.status == "ACTIVE" and aware(row.expires_at) > now()
        if active:
            try:
                active = str(row.id) not in self.ledger.denied(actor.organization_id)
            except Exception:
                active = False
        return {"status": row.status if row else "NOT_ENROLLED", "active": active,
                "expires_at": aware(row.expires_at).isoformat() if row else None}

    def result(self, db, actor, session):
        self.require_ready()
        if aware(session.expires_at) <= now():
            return self._expired(db, actor, session)
        if session.result is None:
            return {"outcome": "RETRY_REQUIRED", "message": RECOVERY}
        if session.result.get("outcome") != "MATCHED":
            return session.result
        eligible = {enrollment.id for enrollment, _ in self._eligible(db, actor.organization_id, actor)}
        if session.matched_enrollment_id not in eligible:
            return {"outcome": "NO_MATCH", "message": RECOVERY}
        token = secrets.token_urlsafe(32)
        session.reference_hash, session.reference_expires_at = digest(token), min(now() + timedelta(seconds=30), aware(session.expires_at))
        session.disclosure_key = None
        return {**session.result, "recognition_reference": token, "expires_at": session.reference_expires_at.isoformat()}

    def disclose(self, db, actor, session, token, key):
        self.require_ready()
        if (session.policy_version != self.policy.binding or session.result is None or session.result.get("outcome") != "MATCHED"
                or not session.reference_hash or not hmac.compare_digest(session.reference_hash, digest(token))
                or session.reference_expires_at is None or aware(session.reference_expires_at) <= now()
                or aware(session.expires_at) <= now() or session.purpose not in actor.purposes):
            error(403, "RECOGNITION_REFERENCE_UNUSABLE")
        if session.disclosure_key and session.disclosure_key != key:
            error(409, "RECOGNITION_REFERENCE_ALREADY_USED")
        eligible = {enrollment.id: enrollment for enrollment, _ in self._eligible(db, actor.organization_id, actor)}
        enrollment = eligible.get(session.matched_enrollment_id)
        if enrollment is None or actor.kind == "PERSON" and actor.subject_id != enrollment.subject_id:
            error(403, "PROFILE_NOT_AUTHORIZED")
        subject = db.scalar(sa.select(FaceSubject).where(FaceSubject.organization_id == actor.organization_id,
                                                       FaceSubject.id == enrollment.subject_id))
        if subject.profile_expires_at is None or aware(subject.profile_expires_at) <= now():
            error(503, "PERMITTED_PROFILE_EXPIRED")
        permitted = set(actor.profile_fields) & (KIOSK_FIELDS if actor.kind == "KIOSK" else PROFILE_FIELDS)
        try:
            profile = json.loads(self.state.field_cipher.open(subject.profile_ciphertext, subject.profile_key_version,
                                                              profile_context(subject)))
        except Exception:
            error(503, "PERMITTED_PROFILE_UNAVAILABLE")
        profile["kyc_status"] = "VERIFIED"
        session.disclosure_key = key
        self._event(db, actor, session, "PROFILE_DISCLOSED", ["MINIMAL_PROFILE_ONLY"])
        return {"profile": {field: profile[field] for field in permitted if field in profile},
                "clear_after_seconds": 15 if actor.kind == "KIOSK" else 30}
