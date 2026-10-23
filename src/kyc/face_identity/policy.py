"""Signed, expiring 1:N approval. No built-in identification thresholds or readiness inheritance."""

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field

from kyc.biometrics.types import SFACE_NAME, SFACE_VERSION, SFACE_SHA256


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class IdentityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(min_length=1, max_length=80)
    populations: list[Literal["STAFF", "CUSTOMER"]] = Field(min_length=1, max_length=2)
    model_name: str = SFACE_NAME
    model_version: str = SFACE_VERSION
    model_sha256: str = SFACE_SHA256
    identification_threshold: float = Field(gt=0, lt=1)
    ambiguity_margin: float = Field(gt=0, lt=1)
    duplicate_threshold: float = Field(gt=0, lt=1)
    verification_threshold: float = Field(gt=0, lt=1)
    movement: float = Field(gt=0, lt=1)
    max_population: int = Field(ge=1, le=10000)
    retention_days: int = Field(ge=1, le=90)
    backup_retention_days: int = Field(default=30, ge=1, le=90)
    notice_version: str = Field(min_length=1, max_length=80)
    visibility_provider: str = Field(min_length=1, max_length=80)
    session_seconds: int = Field(default=120, ge=30, le=120)

    @property
    def model_id(self):
        return f"{self.model_name}:{self.model_version}:{self.model_sha256}"

    @property
    def binding(self):
        # Even an accidentally reused human version label cannot change a live
        # session/template's calibrated decision parameters underneath it.
        return sha(self.model_dump())


RELEASE_EVIDENCE = ("device_validation", "pad_validation", "occlusion_validation",
                    "backup_restore_validation", "security_review", "enrollment_verification_validation")


def evidence_reference(value):
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def load_policy(bundle_path: Path | None, public_key_path: Path | None):
    """Operator-controlled key signs the exact policy, measured report and release evidence.

    Approval is a governance boundary, not proof that the supplied measurements are true.
    Evaluation must be independently reviewed; unit tests never generate a release approval.
    """
    if bundle_path is None or public_key_path is None:
        raise ValueError("APPROVED_IDENTIFICATION_POLICY_REQUIRED")
    if bundle_path.stat().st_size > 1024 * 1024:
        raise ValueError("APPROVAL_TOO_LARGE")
    bundle = json.loads(bundle_path.read_text())
    payload = bundle["payload"]
    key = load_pem_public_key(public_key_path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("ED25519_APPROVAL_KEY_REQUIRED")
    key.verify(base64.b64decode(bundle["signature"], validate=True), canonical(payload))
    policy = IdentityPolicy.model_validate(payload["policy"])
    approval, report = payload["approval"], payload["evaluation"]
    expiry = datetime.fromisoformat(approval["expires_at"])
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise ValueError("IDENTIFICATION_APPROVAL_EXPIRED")
    if (approval.get("scope") != "face-identification" or not approval.get("approved_by")
            or approval.get("evaluation_sha256") != sha(report)
            or any(not evidence_reference(approval.get(item)) for item in RELEASE_EVIDENCE)
            or report.get("policy_sha256") != sha(payload["policy"])
            or report.get("ready") is not True or report.get("synthetic") is not False
            or report.get("identity_disjoint") is not True or report.get("criteria_predeclared") is not True
            or report.get("max_population_tested", 0) < policy.max_population
            or not set(policy.populations) <= set(report.get("populations_tested", []))
            or report.get("search_method") != "EXACT"
            or report.get("candidate_recall") != 1.0):
        raise ValueError("IDENTIFICATION_EVIDENCE_INCOMPLETE")
    for metric in ("fpir", "fnir", "ambiguity_rate", "retry_rate", "pad_attack_acceptance", "pad_genuine_rejection",
                   "verification_false_acceptance", "verification_false_rejection"):
        measurement = report.get("metrics", {}).get(metric, {})
        if (type(measurement.get("trials")) is not int or measurement["trials"] < 1 or measurement.get("passed") is not True
                or not math.isfinite(measurement.get("upper_95", float("nan")))):
            raise ValueError("IDENTIFICATION_MEASUREMENT_MISSING")
    for population in policy.populations:
        measurements = report.get("population_results", {}).get(population, {})
        if measurements.get("gallery_size", 0) < policy.max_population:
            raise ValueError("POPULATION_VALIDATION_MISSING")
        for metric in ("fpir", "fnir"):
            measured = measurements.get("metrics", {}).get(metric, {})
            if (measured.get("passed") is not True or measured.get("trials", 0) < 1
                    or not math.isfinite(measured.get("upper_95", float("nan")))):
                raise ValueError("POPULATION_VALIDATION_MISSING")
    if (policy.model_name, policy.model_version, policy.model_sha256) != (SFACE_NAME, SFACE_VERSION, SFACE_SHA256):
        raise ValueError("MODEL_MIGRATION_REQUIRED")
    return policy, expiry


def decide(scores, policy):
    """Scores are per distinct enrolled subject, never per correlated video frame."""
    ranked = sorted(scores, key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < policy.identification_threshold:
        return "NO_MATCH", None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < policy.ambiguity_margin:
        return "AMBIGUOUS", None
    return "MATCHED", ranked[0][0]
