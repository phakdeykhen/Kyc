"""Active liveness assessment: challenge completion, 3D geometry, replay and identity continuity.

Frames must be raw, unmirrored camera frames. The policy is versioned and, until it is
calibrated on presentation-attack data, can return at best REVIEW; it can still return
FAIL for byte-identical replay. Flat geometry is a heuristic, not proof of a photo:
uncalibrated findings go to review. Thresholds never leave the server.
"""

from dataclasses import dataclass, field
import statistics

from PIL import Image

from kyc.biometrics import compare_embeddings
from kyc.biometrics.types import FaceEmbedding, FaceMatchPolicy
from kyc.biometrics.quality import assess_face_quality
from kyc.domain.enums import CheckResult
from kyc.liveness.challenge import BASELINE
from kyc.liveness.geometry import PoseSample, pose

# Expected sign of the pose change for each movement (frames are unmirrored). The nose tip stands in
# front of the eye/mouth plane: looking up lifts it toward the eye line (b falls), looking down lowers
# it toward the mouth (b rises).
DIRECTIONS = {"TURN_LEFT": ("a", 1), "TURN_RIGHT": ("a", -1), "LOOK_UP": ("b", -1), "LOOK_DOWN": ("b", 1)}
# What this method can and cannot detect, reported with every result (spec §14 "where supported").
COVERAGE = {
    "PRINTED_PHOTO": "PARTIAL_GEOMETRY_HEURISTIC",
    "PHOTO_ON_SCREEN": "PARTIAL_GEOMETRY_HEURISTIC",
    "VIDEO_REPLAY": "PARTIAL_RANDOM_CHALLENGE_AND_NONCE",
    "SCREEN_OR_DEVICE_REPLAY": "PARTIAL_RANDOM_CHALLENGE_AND_NONCE",
    "MASK_3D": "NOT_SUPPORTED",
    "VIRTUAL_CAMERA_INJECTION": "PARTIAL_SINGLE_USE_NONCE_AND_TTL",
    "AI_GENERATED_MEDIA": "NOT_SUPPORTED",
}


@dataclass(frozen=True)
class ActiveLivenessPolicy:
    version: str = "ACTIVE-GEOMETRY-2026.10.4"
    calibrated: bool = False
    movement: float = 0.08           # minimum directed change of a/b (≈10° head turn)
    planar_deformation: float = 0.12  # eye/mouth triangle aspect change that should move the nose
    planar_invariance: float = 0.06   # …while the nose stays within this (flat-face signature)
    min_frames: int = 4
    max_frames: int = 12
    stable_frames_per_step: int = 2


@dataclass
class LivenessOutcome:
    result: CheckResult
    score: float
    reason_codes: list[str]
    retryable: bool
    attack_type: str | None = None
    steps: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    instructions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Guidance:
    """Advisory, per-frame feedback while the person follows a step; never evidence."""
    state: str        # DONE | KEEP_GOING | WRONG_DIRECTION for a movement; CENTERED | NOT_CENTERED for the baseline
    progress: float   # 0–1 in quarter steps, so the threshold itself is not disclosed


def guide(baseline: PoseSample, sample: PoseSample, step: str,
          policy: ActiveLivenessPolicy | None = None) -> Guidance:
    """Same geometry and threshold as `assess`, so a step reported DONE here completes there."""
    policy = policy or ActiveLivenessPolicy()
    if step == BASELINE:
        offset = max(abs(sample.a - baseline.a), abs(sample.b - baseline.b))
        centered = offset <= policy.movement / 2
        return Guidance("CENTERED" if centered else "NOT_CENTERED", 1.0 if centered else 0.0)
    axis, sign = DIRECTIONS[step]
    move = sign * (getattr(sample, axis) - getattr(baseline, axis))
    if move >= policy.movement:
        return Guidance("DONE", 1.0)
    if move <= -policy.movement / 2:
        return Guidance("WRONG_DIRECTION", 0.0)
    return Guidance("KEEP_GOING", min(0.75, max(0.0, move / policy.movement)) // 0.25 * 0.25)


def _retry(reasons, steps=(), instructions=("FOLLOW_EACH_INSTRUCTION",), metrics=None) -> LivenessOutcome:
    return LivenessOutcome(CheckResult.REVIEW, 0.0, list(reasons), True, steps=list(steps),
                           instructions=list(instructions), metrics=metrics or {})


def assess(frames: list[tuple[int, Image.Image, str]], steps: tuple[str, ...], engine,
           reference: FaceEmbedding | None, match_policy: FaceMatchPolicy,
           policy: ActiveLivenessPolicy | None = None) -> LivenessOutcome:
    """frames: (step index, image, sha256 of the uploaded bytes)."""
    policy = policy or ActiveLivenessPolicy()
    if not policy.min_frames <= len(frames) <= policy.max_frames:
        return _retry(["FRAME_COUNT_INVALID"])
    if any(not 0 <= index < len(steps) for index, _, _ in frames):
        return _retry(["FRAME_STEP_INVALID"])
    hashes = [digest for _, _, digest in frames]
    if len(set(hashes)) == 1:
        # One image submitted for every step: a still replay, not a live capture.
        return LivenessOutcome(CheckResult.FAIL, 0.0, ["STATIC_IMAGE_REPLAY"], False, attack_type="STATIC_REPLAY",
                               metrics={"frames": len(frames), "unique_frames": 1})

    samples: dict[int, list[PoseSample]] = {}
    observed: dict[int, list[PoseSample | None]] = {}
    detections = []
    reasons: list[str] = []
    for index, image, _ in frames:
        faces = engine.detect(image)
        if len(faces) != 1:
            observed.setdefault(index, []).append(None)
            reasons.append("MULTIPLE_FACES" if faces else "NO_FACE_IN_FRAME")
            continue
        try:
            sample = pose(faces[0].landmarks)
        except ValueError:
            observed.setdefault(index, []).append(None)
            reasons.append("LANDMARKS_UNUSABLE")
            continue
        samples.setdefault(index, []).append(sample)
        observed.setdefault(index, []).append(sample)
        detections.append((index, image, faces[0]))
    if "MULTIPLE_FACES" in reasons:
        return _retry(["MULTIPLE_FACES"], instructions=["ONLY_YOU_IN_FRAME"])
    if 0 not in samples:
        return _retry(sorted(set(reasons)) or ["BASELINE_NOT_CAPTURED"], instructions=["LOOK_STRAIGHT", "MORE_LIGHT"])

    if len(samples[0]) < policy.stable_frames_per_step:
        return _retry(["BASELINE_NOT_STABLE"], instructions=["HOLD_STILL", "LOOK_STRAIGHT"])
    for index, image, face in detections:
        if index == 0:
            quality = assess_face_quality(image, [face])
            if not quality.accepted:
                return _retry(["BASELINE_QUALITY_UNUSABLE"], instructions=quality.instructions)

    baseline = PoseSample(a=statistics.median(s.a for s in samples[0]), b=statistics.median(s.b for s in samples[0]),
                          aspect=statistics.median(s.aspect for s in samples[0]),
                          scale=statistics.median(s.scale for s in samples[0]), centre=samples[0][0].centre)
    step_results = [{"step": BASELINE, "completed": True}]
    for index, name in enumerate(steps[1:], start=1):
        axis, sign = DIRECTIONS[name]
        moves = [sign * (getattr(sample, axis) - getattr(baseline, axis)) if sample else float("-inf")
                 for sample in observed.get(index, [])]
        held, longest = 0, 0
        for move in moves:
            held = held + 1 if move >= policy.movement else 0
            longest = max(longest, held)
        step_results.append({"step": name, "completed": longest >= policy.stable_frames_per_step,
                             "frames": len(moves)})

    # Possible flat geometry: expression and landmark error can produce the same measurements.
    flat_frames = 0
    for sample in (s for group in samples.values() for s in group):
        deformation = abs(sample.aspect / baseline.aspect - 1)
        displacement = max(abs(sample.a - baseline.a), abs(sample.b - baseline.b))
        if deformation >= policy.planar_deformation and displacement <= policy.planar_invariance:
            flat_frames += 1
    completed = sum(1 for step in step_results[1:] if step["completed"])
    metrics = {"frames": len(frames), "unique_frames": len(set(hashes)), "frames_with_face": len(detections),
               "steps_completed": completed, "steps_required": len(steps) - 1, "flat_face_frames": flat_frames}
    if flat_frames and completed < len(steps) - 1:
        if not policy.calibrated:
            # An incomplete challenge remains unverified. Do not convert uncertain
            # five-landmark geometry into an automatic rejection of a live person.
            return LivenessOutcome(CheckResult.REVIEW, round(completed / (len(steps) - 1), 3),
                                   ["FLAT_FACE_PRESENTATION", "CHALLENGE_NOT_COMPLETED", "UNCALIBRATED_LIVENESS_POLICY"],
                                   False, attack_type="POSSIBLE_PRINTED_OR_SCREEN_PHOTO",
                                   steps=step_results, metrics=metrics)
        return LivenessOutcome(CheckResult.FAIL, 0.0, ["FLAT_FACE_PRESENTATION"], False,
                               attack_type="PRINTED_OR_SCREEN_PHOTO", steps=step_results, metrics=metrics)
    if completed < len(steps) - 1:
        return _retry(["CHALLENGE_NOT_COMPLETED"], step_results, metrics=metrics)

    # Identity continuity: every frame must still be the person who took the selfie.
    if reference is not None:
        scores = []
        for _, image, detection in detections:
            scores.append(compare_embeddings(reference, engine.embed(image, detection), match_policy).score)
        metrics["identity_frames"] = len(scores)
        if scores and min(scores) < match_policy.fail_threshold:
            reasons_out = ["IDENTITY_CONTINUITY_NOT_ESTABLISHED"]
            return LivenessOutcome(CheckResult.REVIEW, round(completed / (len(steps) - 1) * 0.5, 3), reasons_out, False,
                                   attack_type="POSSIBLE_FACE_SWAP", steps=step_results, metrics=metrics)
    else:
        metrics["identity_frames"] = 0
        return LivenessOutcome(CheckResult.REVIEW, score=1.0,
                               reason_codes=["CHALLENGE_COMPLETED", "IDENTITY_CONTINUITY_NOT_ESTABLISHED"],
                               retryable=False, steps=step_results, metrics=metrics)
    if len(set(hashes)) < len(hashes):
        reasons.append("DUPLICATE_FRAMES")
    score = round(completed / (len(steps) - 1), 3)
    final_reasons = ["CHALLENGE_COMPLETED", *sorted(set(r for r in reasons if r == "DUPLICATE_FRAMES"))]
    if not policy.calibrated:
        return LivenessOutcome(CheckResult.REVIEW, score, final_reasons + ["UNCALIBRATED_LIVENESS_POLICY"], False,
                               steps=step_results, metrics=metrics)
    result = CheckResult.REVIEW if "DUPLICATE_FRAMES" in final_reasons else CheckResult.PASS
    return LivenessOutcome(result, score, final_reasons, False, steps=step_results, metrics=metrics)
