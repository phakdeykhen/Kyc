"""Measured calibration for face matching and validation for liveness.

Thresholds are never typed in: they come from a report computed on labelled data.

Face matching: genuine pairs (document portrait and live face of the same person) and
impostor pairs (different people) are scored with the production model. The MATCH
threshold is the lowest score whose measured false-acceptance rate stays within the target;
the NO_MATCH threshold is the score below which only the target share of genuine pairs fall.
Scores between the two are BORDERLINE (review).

Liveness: labelled sessions (genuine people and named presentation attacks) are run through
the production liveness policy. The report records genuine-pass and attack-detection rates
per attack type, and the policy may only be treated as validated when every required attack
type was tested and met its target.

The loaders refuse reports that are too small, were made for another model or policy, or do
not meet their own targets, so a configuration flag alone can never turn calibration on.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

REPORT_SCHEMA = "kyc-calibration/1"
# Floors below which a measured rate means little. Operators may require more, never less.
MIN_GENUINE_PAIRS = 100
MIN_IMPOSTOR_PAIRS = 1000
MAX_TARGET_FAR = 0.01
MIN_LIVENESS_GENUINE = 50
MIN_ATTACKS_PER_TYPE = 20
REQUIRED_ATTACK_TYPES = ("PRINTED_PHOTO", "PHOTO_ON_SCREEN", "VIDEO_REPLAY")


class CalibrationError(ValueError):
    pass


def _rate(count: int, total: int) -> float:
    return count / total if total else math.nan


def roc_auc(genuine: list[float], impostor: list[float]) -> float:
    """Probability that a random genuine pair outscores a random impostor pair (ties count half)."""
    ordered = sorted([(score, 1) for score in genuine] + [(score, 0) for score in impostor])
    rank_sum, index = 0.0, 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[index][0]:
            end += 1
        average_rank = (index + end) / 2 + 1
        rank_sum += average_rank * sum(label for _, label in ordered[index:end + 1])
        index = end + 1
    positives, negatives = len(genuine), len(impostor)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def histogram(scores: list[float], bins: int = 20) -> list[dict]:
    edges = [-1 + 2 * index / bins for index in range(bins + 1)]
    counts = [0] * bins
    for score in scores:
        counts[min(bins - 1, max(0, int((score + 1) / 2 * bins)))] += 1
    return [{"from": round(edges[i], 3), "to": round(edges[i + 1], 3), "count": counts[i]} for i in range(bins)]


def calibrate_face(genuine: list[float], impostor: list[float], *, target_far: float, target_frr_at_no_match: float,
                   model: dict, version: str, dataset_sha256: str, conditions: dict | None = None) -> dict:
    """Choose MATCH / NO_MATCH thresholds from measured score distributions."""
    if not 0 < target_far <= MAX_TARGET_FAR:
        raise CalibrationError(f"target FAR must be in (0, {MAX_TARGET_FAR}]")
    if not 0 < target_frr_at_no_match < 0.5:
        raise CalibrationError("target FRR at the NO_MATCH threshold must be in (0, 0.5)")
    if len(genuine) < MIN_GENUINE_PAIRS or len(impostor) < MIN_IMPOSTOR_PAIRS:
        raise CalibrationError(f"need at least {MIN_GENUINE_PAIRS} genuine and {MIN_IMPOSTOR_PAIRS} impostor pairs "
                               f"(have {len(genuine)} and {len(impostor)})")
    if not all(math.isfinite(s) and -1 <= s <= 1 for s in genuine + impostor):
        raise CalibrationError("scores must be finite cosine similarities in [-1, 1]")
    impostor_sorted = sorted(impostor)
    allowed = math.floor(target_far * len(impostor))  # impostors permitted at or above the threshold
    # Lowest threshold with at most `allowed` impostors at or above it: just above the (allowed+1)-th highest.
    boundary = impostor_sorted[-(allowed + 1)]
    # Thresholds are stored to 6 decimals: round MATCH up and NO_MATCH down, which only makes each stricter.
    pass_threshold = math.ceil(math.nextafter(boundary, 2.0) * 1e6) / 1e6
    genuine_sorted = sorted(genuine)
    fail_index = math.floor(target_frr_at_no_match * len(genuine))
    fail_threshold = math.floor(min(genuine_sorted[fail_index], pass_threshold) * 1e6) / 1e6
    if fail_threshold >= pass_threshold:
        fail_threshold = round(pass_threshold - 1e-6, 6)
    if pass_threshold > 1:
        raise CalibrationError("impostor scores leave no usable MATCH threshold")
    far = _rate(sum(s >= pass_threshold for s in impostor), len(impostor))
    frr = _rate(sum(s < pass_threshold for s in genuine), len(genuine))
    return {
        "schema": REPORT_SCHEMA, "kind": "FACE_MATCH", "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model, "metric": "COSINE_SIMILARITY", "dataset_sha256": dataset_sha256,
        "thresholds": {"match": pass_threshold, "no_match": fail_threshold},
        "targets": {"far": target_far, "frr_at_no_match": target_frr_at_no_match},
        "measured": {
            "genuine_pairs": len(genuine), "impostor_pairs": len(impostor),
            "far_at_match": round(far, 6), "frr_at_match": round(frr, 6), "tar_at_match": round(1 - frr, 6),
            "genuine_below_no_match": round(_rate(sum(s < fail_threshold for s in genuine), len(genuine)), 6),
            "impostor_borderline": round(_rate(sum(fail_threshold <= s < pass_threshold for s in impostor), len(impostor)), 6),
            "roc_auc": round(roc_auc(genuine, impostor), 6),
            "genuine_distribution": histogram(genuine), "impostor_distribution": histogram(impostor)},
        "conditions": conditions or {},
    }


def validate_liveness(results: list[dict], *, policy_version: str, dataset_sha256: str,
                      target_attack_detection: float = 0.95, target_genuine_pass: float = 0.90) -> dict:
    """results: {"label": "GENUINE" | <attack type>, "result": PASS | REVIEW | FAIL} per labelled session.

    An attack counts as detected when the policy did not PASS it; a genuine person counts as
    passed only on PASS. The report never claims coverage for attack types it was not tested on.
    """
    genuine = [item["result"] for item in results if item["label"] == "GENUINE"]
    attacks: dict[str, list[str]] = {}
    for item in results:
        if item["label"] != "GENUINE":
            attacks.setdefault(item["label"], []).append(item["result"])
    per_type = {name: {"sessions": len(values), "detected": sum(v != "PASS" for v in values),
                       "attack_detection_rate": round(_rate(sum(v != "PASS" for v in values), len(values)), 6)}
                for name, values in sorted(attacks.items())}
    genuine_pass = _rate(sum(v == "PASS" for v in genuine), len(genuine))
    problems = []
    if len(genuine) < MIN_LIVENESS_GENUINE:
        problems.append(f"need at least {MIN_LIVENESS_GENUINE} genuine sessions")
    for name in REQUIRED_ATTACK_TYPES:
        stats = per_type.get(name)
        if not stats or stats["sessions"] < MIN_ATTACKS_PER_TYPE:
            problems.append(f"need at least {MIN_ATTACKS_PER_TYPE} {name} attacks")
        elif stats["attack_detection_rate"] < target_attack_detection:
            problems.append(f"{name} detection {stats['attack_detection_rate']} is below {target_attack_detection}")
    if genuine and genuine_pass < target_genuine_pass:
        problems.append(f"genuine pass rate {round(genuine_pass, 4)} is below {target_genuine_pass}")
    return {
        "schema": REPORT_SCHEMA, "kind": "LIVENESS", "version": policy_version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "dataset_sha256": dataset_sha256,
        "targets": {"attack_detection": target_attack_detection, "genuine_pass": target_genuine_pass},
        "measured": {"genuine_sessions": len(genuine), "genuine_pass_rate": round(genuine_pass, 6) if genuine else None,
                     "attacks": per_type,
                     "untested_attack_types": [n for n in REQUIRED_ATTACK_TYPES if n not in per_type]},
        "validated": not problems, "problems": problems,
    }


@dataclass(frozen=True)
class FaceCalibration:
    version: str
    match_threshold: float
    no_match_threshold: float
    reference: str


@dataclass(frozen=True)
class LivenessValidation:
    version: str
    reference: str


def _load(path: Path, kind: str) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    try:
        report = json.loads(raw)
    except ValueError as error:
        raise CalibrationError(f"{kind} calibration report is not JSON") from error
    if report.get("schema") != REPORT_SCHEMA or report.get("kind") != kind:
        raise CalibrationError(f"not a {kind} calibration report")
    return report, f"{Path(path).name}#sha256:{hashlib.sha256(raw).hexdigest()[:16]}"


def load_face_calibration(path: Path, model: dict) -> FaceCalibration:
    report, reference = _load(path, "FACE_MATCH")
    if report.get("model") != model:
        raise CalibrationError("face calibration was measured with a different model")
    measured, targets, thresholds = report.get("measured", {}), report.get("targets", {}), report.get("thresholds", {})
    if measured.get("genuine_pairs", 0) < MIN_GENUINE_PAIRS or measured.get("impostor_pairs", 0) < MIN_IMPOSTOR_PAIRS:
        raise CalibrationError("face calibration dataset is below the minimum size")
    if not 0 < targets.get("far", 1) <= MAX_TARGET_FAR or measured.get("far_at_match", 1) > targets["far"]:
        raise CalibrationError("face calibration does not meet its false-acceptance target")
    match, no_match = thresholds.get("match"), thresholds.get("no_match")
    if not (isinstance(match, (int, float)) and isinstance(no_match, (int, float)) and -1 <= no_match < match <= 1):
        raise CalibrationError("face calibration thresholds are invalid")
    version = str(report.get("version") or "")
    if not version or "UNCALIBRATED" in version.upper():
        raise CalibrationError("face calibration needs a calibrated policy version")
    return FaceCalibration(version, float(match), float(no_match), reference)


def load_liveness_validation(path: Path, policy_version: str) -> LivenessValidation:
    report, reference = _load(path, "LIVENESS")
    if report.get("version") != policy_version:
        raise CalibrationError("liveness validation was measured for a different liveness policy version")
    if report.get("validated") is not True or report.get("problems"):
        raise CalibrationError("liveness validation did not meet its targets")
    measured = report.get("measured", {})
    if measured.get("genuine_sessions", 0) < MIN_LIVENESS_GENUINE or any(
            measured.get("attacks", {}).get(name, {}).get("sessions", 0) < MIN_ATTACKS_PER_TYPE
            for name in REQUIRED_ATTACK_TYPES):
        raise CalibrationError("liveness validation dataset is below the minimum size")
    return LivenessValidation(policy_version, reference)
