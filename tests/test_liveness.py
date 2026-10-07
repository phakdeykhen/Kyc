"""Phase 10: active liveness. Landmarks come from a projected 3D head model, so geometry is exact."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import unittest
from uuid import UUID, uuid4

import numpy as np
from PIL import Image
import sqlalchemy as sa
from sqlalchemy.orm import Session

from kyc.biometrics.types import FaceDetection, FaceEmbedding, FaceMatchPolicy
from kyc.db.models import KYCSession, LivenessChallenge, LivenessCheck, SelfieCapture
from kyc.domain.enums import CheckResult
from kyc.liveness import challenge as challenges
from kyc.liveness.active import ActiveLivenessPolicy, assess, guide
from kyc.liveness.geometry import pose
from tests.helpers import call, multipart
from tests.test_biometrics_api import BiometricAPICase, InjectedFaceEngine

HEAD = np.array([(-32, 0, 0), (32, 0, 0), (0, 35, 28), (-24, 70, 4), (24, 70, 4)], float)
# Image y points down and +z toward the camera: a negative pitch turns the face up (chin raised).
POSES = {"LOOK_STRAIGHT": (0, 0), "TURN_LEFT": (22, 0), "TURN_RIGHT": (-22, 0), "LOOK_UP": (0, -16), "LOOK_DOWN": (0, 16)}


def rotation(yaw=0.0, pitch=0.0):
    y, p = np.radians(yaw), np.radians(pitch)
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rx = np.array([[1, 0, 0], [0, np.cos(p), np.sin(p)], [0, -np.sin(p), np.cos(p)]])
    return ry.T @ rx.T


def project(points, distance=500.0, shift=(640, 360)):
    return tuple((900 * x / (distance - z) + shift[0], 900 * y / (distance - z) + shift[1]) for x, y, z in points)


def real_head(step, jitter=0.0):
    yaw, pitch = POSES[step]
    return project(HEAD @ rotation(yaw + jitter, pitch))


def flat_photo(tilt_yaw, tilt_pitch):
    """The frontal face printed flat, then the paper tilted in front of the camera."""
    flat = np.array([(x - 640, y - 360, 0.0) for x, y in real_head("LOOK_STRAIGHT")])
    return project(flat @ rotation(tilt_yaw, tilt_pitch), distance=400)


def detection(landmarks):
    xs, ys = [p[0] for p in landmarks], [p[1] for p in landmarks]
    return FaceDetection((min(xs) - 40, min(ys) - 60, max(xs) - min(xs) + 80, max(ys) - min(ys) + 120), landmarks, 0.95)


def unit(index):
    vector = np.zeros(128, dtype=np.float32)
    vector[index] = 1
    return FaceEmbedding(vector)


class FrameEngine(InjectedFaceEngine):
    """Frames are solid-colour images; the red channel selects scripted landmarks and identity."""

    detector_name, detector_version = "TEST-LANDMARK-SCRIPT", "1"

    def __init__(self):
        super().__init__()
        self.frames: dict[int, tuple] = {}

    def frame(self, landmarks, faces=1, identity=0) -> bytes:
        key = len(self.frames) + 1
        self.frames[key] = (landmarks, faces, identity)
        buffer = BytesIO()
        Image.new("RGB", (640, 480), (key * 9 % 256, 120, 90)).save(buffer, "PNG")
        return buffer.getvalue()

    def _lookup(self, image):
        red = image.getpixel((5, 5))[0]
        return next(value for key, value in self.frames.items() if key * 9 % 256 == red)

    def detect(self, image):
        landmarks, faces, _ = self._lookup(image)
        return [detection(landmarks)] * faces

    def embed(self, image, detection_):
        if not hasattr(image, "getpixel") or image.size != (640, 480):
            return super().embed(image, detection_)
        return unit(self._lookup(image)[2])


def image_frames(engine, items):
    """items: (step index, landmarks[, faces, identity]) → assessor frames."""
    out = []
    for item in items:
        index, landmarks, *rest = item
        data = engine.frame(landmarks, *rest)
        image = Image.open(BytesIO(data)).convert("RGB")
        out.append((index, image, str(hash(data))))
    return out


class GeometryTests(unittest.TestCase):
    def test_flat_faces_keep_their_coordinates_under_any_affine_transform(self):
        rng = np.random.default_rng(4)
        base = pose(real_head("LOOK_STRAIGHT"))
        for _ in range(50):
            matrix, shift = rng.normal(0, 0.4, (2, 2)) + np.eye(2), rng.normal(0, 50, 2)
            moved = [tuple(matrix @ np.array(p) + shift) for p in real_head("LOOK_STRAIGHT")]
            sample = pose(moved)
            self.assertAlmostEqual(sample.a, base.a, places=6)
            self.assertAlmostEqual(sample.b, base.b, places=6)

    def test_real_head_movements_move_the_nose_in_the_expected_direction(self):
        base = pose(real_head("LOOK_STRAIGHT"))
        self.assertGreater(pose(real_head("TURN_LEFT")).a - base.a, 0.15)
        self.assertLess(pose(real_head("TURN_RIGHT")).a - base.a, -0.15)
        # Looking up lifts the nose tip toward the eye line; looking down drops it toward the mouth.
        self.assertLess(pose(real_head("LOOK_UP")).b - base.b, -0.1)
        self.assertGreater(pose(real_head("LOOK_DOWN")).b - base.b, 0.1)

    def test_a_tilted_photo_cannot_fake_a_head_turn_even_under_perspective(self):
        base = pose(real_head("LOOK_STRAIGHT"))
        policy = ActiveLivenessPolicy()
        for yaw, pitch in ((35, 0), (-35, 0), (0, 30), (25, 20)):
            sample = pose(flat_photo(yaw, pitch))
            self.assertLess(max(abs(sample.a - base.a), abs(sample.b - base.b)), policy.movement)


class ChallengeTests(unittest.TestCase):
    def test_challenges_are_random_distinct_and_hash_their_nonce(self):
        issued = [challenges.issue() for _ in range(200)]
        self.assertTrue(all(item.steps[0] == "LOOK_STRAIGHT" and len(set(item.steps[1:])) == 3 for item in issued))
        self.assertGreater(len(Counter(item.steps for item in issued)), 15)  # 24 possible orders
        self.assertEqual(len({item.nonce for item in issued}), 200)
        self.assertEqual(issued[0].nonce_hash, challenges.nonce_hash(issued[0].nonce))
        self.assertNotIn("BLINK", {step for item in issued for step in item.steps})


class AssessorTests(unittest.TestCase):
    steps = ("LOOK_STRAIGHT", "TURN_LEFT", "LOOK_UP", "TURN_RIGHT")

    def setUp(self):
        self.engine = FrameEngine()
        self.match = FaceMatchPolicy()

    def run_frames(self, items, policy=None, reference=unit(0)):
        return assess(image_frames(self.engine, items), self.steps, self.engine, reference, self.match, policy)

    def live(self):
        return [(0, real_head("LOOK_STRAIGHT")), (0, real_head("LOOK_STRAIGHT", 1)), (1, real_head("TURN_LEFT")),
                (2, real_head("LOOK_UP")), (3, real_head("TURN_RIGHT"))]

    def test_a_live_person_completes_the_challenge_but_stays_review_until_calibrated(self):
        outcome = self.run_frames(self.live())
        self.assertEqual((outcome.result, outcome.retryable, outcome.score), (CheckResult.REVIEW, False, 1.0))
        self.assertIn("UNCALIBRATED_LIVENESS_POLICY", outcome.reason_codes)
        self.assertTrue(all(step["completed"] for step in outcome.steps))
        calibrated = self.run_frames(self.live(), ActiveLivenessPolicy(calibrated=True))
        self.assertEqual(calibrated.result, CheckResult.PASS)

    def test_uncalibrated_flat_geometry_requires_review_and_never_passes(self):
        frames = [(0, real_head("LOOK_STRAIGHT")), (1, flat_photo(35, 0)), (2, flat_photo(0, 30)), (3, flat_photo(-35, 0))]
        outcome = self.run_frames(frames)
        self.assertEqual((outcome.result, outcome.attack_type, outcome.retryable),
                         (CheckResult.REVIEW, "POSSIBLE_PRINTED_OR_SCREEN_PHOTO", False))
        self.assertIn("FLAT_FACE_PRESENTATION", outcome.reason_codes)
        self.assertIn("UNCALIBRATED_LIVENESS_POLICY", outcome.reason_codes)
        self.assertLess(outcome.metrics["steps_completed"], outcome.metrics["steps_required"])
        self.assertLess(outcome.score, 1.0)

    def test_calibrated_flat_geometry_preserves_failure(self):
        frames = [(0, real_head("LOOK_STRAIGHT")), (1, flat_photo(35, 0)), (2, flat_photo(0, 30)), (3, flat_photo(-35, 0))]
        outcome = self.run_frames(frames, ActiveLivenessPolicy(calibrated=True))
        self.assertEqual((outcome.result, outcome.attack_type), (CheckResult.FAIL, "PRINTED_OR_SCREEN_PHOTO"))
        self.assertEqual(outcome.reason_codes, ["FLAT_FACE_PRESENTATION"])

    def test_live_landmark_displacement_does_not_cause_uncalibrated_rejection(self):
        # A 3D live head with modest mouth/nose landmark displacement can trigger
        # the planar heuristic despite containing no printed or screen photo.
        baseline = real_head("LOOK_STRAIGHT")
        displaced = tuple((x, y + (22 if index in (3, 4) else 11 if index == 2 else 0))
                          for index, (x, y) in enumerate(baseline))
        small_left = project(HEAD @ rotation(5, 0))
        small_right = project(HEAD @ rotation(-5, 0))
        outcome = self.run_frames([(0, baseline), (1, small_left), (2, displaced), (3, small_right)])
        self.assertGreater(outcome.metrics["flat_face_frames"], 0)
        self.assertEqual(outcome.result, CheckResult.REVIEW)
        self.assertIn("UNCALIBRATED_LIVENESS_POLICY", outcome.reason_codes)
        self.assertFalse(all(step["completed"] for step in outcome.steps))

    def test_one_image_replayed_for_every_step_is_failed(self):
        engine, data = self.engine, None
        landmarks = real_head("LOOK_STRAIGHT")
        data = engine.frame(landmarks)
        image = Image.open(BytesIO(data)).convert("RGB")
        frames = [(index, image, "same-digest") for index in (0, 1, 2, 3)]
        outcome = assess(frames, self.steps, engine, unit(0), self.match)
        self.assertEqual((outcome.result, outcome.attack_type), (CheckResult.FAIL, "STATIC_REPLAY"))

    def test_wrong_or_missing_movements_ask_for_a_retry(self):
        mirrored = [(0, real_head("LOOK_STRAIGHT")), (1, real_head("TURN_RIGHT")), (2, real_head("LOOK_UP")),
                    (3, real_head("TURN_LEFT"))]  # left/right swapped (e.g. a mirrored or pre-recorded sequence)
        outcome = self.run_frames(mirrored)
        self.assertTrue(outcome.retryable)
        self.assertEqual(outcome.reason_codes, ["CHALLENGE_NOT_COMPLETED"])
        still = self.run_frames([(index, real_head("LOOK_STRAIGHT", index * 0.3)) for index in (0, 1, 2, 3)])
        self.assertTrue(still.retryable)

    def test_someone_else_appearing_mid_challenge_is_not_accepted(self):
        frames = self.live()
        frames[3] = (2, real_head("LOOK_UP"), 1, 7)  # a different face for one step
        outcome = self.run_frames(frames)
        self.assertEqual((outcome.result, outcome.attack_type), (CheckResult.REVIEW, "POSSIBLE_FACE_SWAP"))
        self.assertFalse(outcome.retryable)

    def test_more_than_one_face_or_no_baseline_retries(self):
        frames = self.live()
        frames[2] = (1, real_head("TURN_LEFT"), 2)
        self.assertEqual(self.run_frames(frames).reason_codes, ["MULTIPLE_FACES"])
        no_base = [(1, real_head("TURN_LEFT")), (2, real_head("LOOK_UP")), (3, real_head("TURN_RIGHT")), (1, real_head("TURN_LEFT", 2))]
        self.assertTrue(self.run_frames(no_base).retryable)


class GuidanceTests(unittest.TestCase):
    steps = ("LOOK_STRAIGHT", "TURN_LEFT", "LOOK_UP", "TURN_RIGHT")

    def setUp(self):
        self.baseline = pose(real_head("LOOK_STRAIGHT"))

    def test_each_movement_is_done_when_made_and_flagged_when_reversed(self):
        opposite = {"TURN_LEFT": "TURN_RIGHT", "TURN_RIGHT": "TURN_LEFT", "LOOK_UP": "LOOK_DOWN", "LOOK_DOWN": "LOOK_UP"}
        for step, other in opposite.items():
            with self.subTest(step=step):
                self.assertEqual(guide(self.baseline, pose(real_head(step)), step).state, "DONE")
                self.assertEqual(guide(self.baseline, pose(real_head(other)), step).state, "WRONG_DIRECTION")
                still = guide(self.baseline, pose(real_head("LOOK_STRAIGHT", 0.5)), step)
                self.assertEqual((still.state, still.progress), ("KEEP_GOING", 0.0))

    def test_progress_is_coarse_and_centering_needs_a_return_to_the_baseline(self):
        partial = guide(self.baseline, pose(project(HEAD @ rotation(9, 0))), "TURN_LEFT")
        self.assertEqual(partial.state, "KEEP_GOING")
        self.assertIn(partial.progress, (0.25, 0.5, 0.75))  # quarter steps only; the threshold is not disclosed
        self.assertEqual(guide(self.baseline, pose(real_head("LOOK_STRAIGHT", 1)), "LOOK_STRAIGHT").state, "CENTERED")
        self.assertEqual(guide(self.baseline, pose(real_head("TURN_LEFT")), "LOOK_STRAIGHT").state, "NOT_CENTERED")

    def test_guidance_and_the_final_assessment_always_agree(self):
        """A step the page was told is DONE must complete in the assessment, and vice versa."""
        match = FaceMatchPolicy()
        for axis, step, index in (("yaw", "TURN_LEFT", 1), ("pitch", "LOOK_UP", 2), ("yaw", "TURN_RIGHT", 3)):
            for degrees in range(0, 31):
                angle = -degrees if step == "TURN_RIGHT" else degrees
                landmarks = project(HEAD @ (rotation(angle, 0) if axis == "yaw" else rotation(0, angle)))
                engine = FrameEngine()  # its colour-keyed frames are unique for 256 frames only
                frames = image_frames(engine, [(0, real_head("LOOK_STRAIGHT")), (0, real_head("LOOK_STRAIGHT")),
                                               (index, landmarks), (index, landmarks)])
                outcome = assess(frames, self.steps, engine, None, match)
                with self.subTest(step=step, degrees=degrees):
                    self.assertEqual(guide(self.baseline, pose(landmarks), step).state == "DONE",
                                     outcome.steps[index]["completed"])


class LivenessAPITests(BiometricAPICase):
    async def at_liveness(self, level="DOCUMENT_FACE_LIVENESS"):
        session_id = self.ready(level=level)
        code, body, _ = await self.upload(session_id)
        self.assertEqual(body["status"], "LIVENESS_REQUIRED" if "LIVENESS" in level else "PROCESSING", body)
        engine = FrameEngine()
        self.app.state.face_engine = engine
        return session_id, engine

    async def challenge(self, session_id, headers=None):
        return await call(self.app, f"/v1/kyc/{session_id}/liveness/challenge", "POST", body={}, headers=headers or self.headers)

    async def submit(self, session_id, issued, frames, nonce=None):
        fields = {"challenge_id": issued["challenge_id"], "nonce": nonce or issued["nonce"],
                  "frame_steps": ",".join(str(index) for index, _ in frames)}
        raw, content_type = multipart(fields, [("frames", f"f{i}.png", "image/png", data) for i, (_, data) in enumerate(frames)])
        return await call(self.app, f"/v1/kyc/{session_id}/liveness", "POST", raw=raw, content_type=content_type,
                          headers=self.headers)

    @staticmethod
    def follow(engine, issued, flat=False):
        frames = [(0, engine.frame(real_head("LOOK_STRAIGHT"))), (0, engine.frame(real_head("LOOK_STRAIGHT", 1)))]
        for item in issued["steps"][1:]:
            landmarks = flat_photo(*{"TURN_LEFT": (35, 0), "TURN_RIGHT": (-35, 0), "LOOK_UP": (0, -30),
                                     "LOOK_DOWN": (0, 30)}[item["step"]]) if flat else real_head(item["step"])
            frames.append((item["index"], engine.frame(landmarks)))
        return frames

    async def test_challenge_then_live_frames_record_evidence_and_advance(self):
        session_id, engine = await self.at_liveness()
        code, issued, _ = await self.challenge(session_id)
        self.assertEqual(code, 200, issued)
        self.assertEqual(len(issued["steps"]), 4)
        self.assertNotIn("movement", json.dumps(issued))  # thresholds never leave the server
        code, body, _ = await self.submit(session_id, issued, self.follow(engine, issued))
        self.assertEqual(code, 200, body)
        self.assertEqual((body["status"], body["result"], body["score"]), ("PROCESSING", "REVIEW", 1.0))
        self.assertEqual(body["coverage"]["MASK_3D"], "NOT_SUPPORTED")
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["checks"]["liveness"], "REVIEW")
        self.assertEqual(result["decision"]["result"], "REVIEW")  # uncalibrated liveness never auto-verifies
        self.assertIn("LIVENESS_INCONCLUSIVE", result["decision"]["reason_codes"])
        with Session(self.engine) as db:
            check = db.scalar(sa.select(LivenessCheck))
            self.assertEqual((check.method, check.challenge_hash), ("ACTIVE_LIVENESS", challenges.nonce_hash(issued["nonce"])))
            self.assertFalse(check.evidence_metadata["frames_retained"])
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(SelfieCapture)), 1)  # frames were not stored

    async def test_uncalibrated_flat_geometry_goes_to_manual_review_without_verifying(self):
        session_id, engine = await self.at_liveness()
        code, issued, _ = await self.challenge(session_id)
        code, body, _ = await self.submit(session_id, issued, self.follow(engine, issued, flat=True))
        self.assertEqual((body["result"], body["attack_type"], body["status"]),
                         ("REVIEW", "POSSIBLE_PRINTED_OR_SCREEN_PHOTO", "PROCESSING"))
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["checks"]["liveness"], "REVIEW")
        self.assertEqual(result["status"], "MANUAL_REVIEW")
        self.assertEqual(result["decision"]["result"], "REVIEW")
        self.assertIn("LIVENESS_INCONCLUSIVE", result["decision"]["reason_codes"])
        self.assertNotIn("LIVENESS_FAILED", result["decision"]["reason_codes"])
        self.assertIn("LIVENESS_FLAT_FACE_PRESENTATION", result["review_flags"])

    async def test_identical_replay_still_rejects_session(self):
        session_id, engine = await self.at_liveness()
        code, issued, _ = await self.challenge(session_id)
        data = engine.frame(real_head("LOOK_STRAIGHT"))
        frames = [(item["index"], data) for item in issued["steps"]]
        code, body, _ = await self.submit(session_id, issued, frames)
        self.assertEqual(body["result"], "FAIL")
        self.assertIn("STATIC_IMAGE_REPLAY", body["reason_codes"])
        code, result, _ = await call(self.app, f"/v1/kyc/{session_id}/result", headers=self.headers)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("LIVENESS_FAILED", result["decision"]["reason_codes"])

    async def guide(self, session_id, issued, step, frame, baseline=None, nonce=None):
        fields = {"challenge_id": issued["challenge_id"], "nonce": nonce or issued["nonce"], "step": str(step)}
        files = [("frame", "frame.png", "image/png", frame)]
        if baseline is not None:
            files.append(("baseline", "baseline.png", "image/png", baseline))
        raw, content_type = multipart(fields, files)
        return await call(self.app, f"/v1/kyc/{session_id}/liveness/guide", "POST", raw=raw, content_type=content_type,
                          headers=self.headers)

    async def test_guidance_confirms_each_step_without_using_the_challenge(self):
        session_id, engine = await self.at_liveness()
        code, issued, _ = await self.challenge(session_id)
        baseline = engine.frame(real_head("LOOK_STRAIGHT"))
        code, body, _ = await self.guide(session_id, issued, 0, baseline)
        self.assertEqual((code, body["face"], body["state"]), (200, "OK", None), body)
        step = issued["steps"][1]
        code, body, _ = await self.guide(session_id, issued, 1, engine.frame(real_head(step["step"])), baseline)
        self.assertEqual((body["step"], body["state"], body["progress"]), (step["step"], "DONE", 1.0))
        code, body, _ = await self.guide(session_id, issued, 1, engine.frame(real_head("LOOK_STRAIGHT", 0.5)), baseline)
        self.assertEqual(body["state"], "KEEP_GOING")
        self.assertNotIn("movement", json.dumps(body))  # thresholds never leave the server
        code, body, _ = await self.guide(session_id, issued, 0, engine.frame(real_head(step["step"])), baseline)
        self.assertEqual(body["state"], "NOT_CENTERED")
        code, body, _ = await self.guide(session_id, issued, 1, engine.frame(real_head("LOOK_STRAIGHT"), 0), baseline)
        self.assertEqual((body["face"], body["state"]), ("NO_FACE", None))
        code, body, _ = await self.guide(session_id, issued, 1, engine.frame(real_head("LOOK_STRAIGHT"), 2), baseline)
        self.assertEqual(body["face"], "MULTIPLE_FACES")
        with Session(self.engine) as db:  # advisory only: nothing recorded, challenge still open
            self.assertEqual(db.scalar(sa.select(sa.func.count()).select_from(LivenessCheck)), 0)
            self.assertIsNone(db.get(LivenessChallenge, UUID(issued["challenge_id"])).used_at)
        code, body, _ = await self.submit(session_id, issued, self.follow(engine, issued))
        self.assertEqual((code, body["status"]), (200, "PROCESSING"), body)

    async def test_guidance_needs_an_open_challenge_and_a_valid_step(self):
        session_id, engine = await self.at_liveness()
        code, issued, _ = await self.challenge(session_id)
        frame = engine.frame(real_head("LOOK_STRAIGHT"))
        code, body, _ = await self.guide(session_id, issued, 0, frame, nonce="wrong-nonce")
        self.assertEqual((code, body["reason_code"]), (409, "CHALLENGE_INVALID"))
        code, body, _ = await self.guide(session_id, issued, 9, frame)
        self.assertEqual((code, body["reason_code"]), (422, "FRAME_STEP_INVALID"))
        code, body, _ = await self.guide(session_id, issued, 1, frame, engine.frame(real_head("LOOK_STRAIGHT"), 0))
        self.assertEqual((code, body["reason_code"]), (422, "BASELINE_UNUSABLE"))
        with Session(self.engine) as db, db.begin():
            db.get(LivenessChallenge, UUID(issued["challenge_id"])).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        code, body, _ = await self.guide(session_id, issued, 0, frame)
        self.assertEqual((code, body["reason_code"]), (409, "CHALLENGE_EXPIRED"))
        code, fresh, _ = await self.challenge(session_id)
        await self.submit(session_id, fresh, self.follow(engine, fresh))
        code, body, _ = await self.guide(session_id, fresh, 0, frame)
        self.assertEqual(code, 409)  # the session has moved on from liveness

    async def test_challenges_are_single_use_bound_to_their_nonce_and_expire(self):
        session_id, engine = await self.at_liveness()
        code, issued, _ = await self.challenge(session_id)
        code, body, _ = await self.submit(session_id, issued, self.follow(engine, issued), nonce="wrong-nonce")
        self.assertEqual((code, body["reason_code"]), (409, "CHALLENGE_INVALID"))
        still = [(index, engine.frame(real_head("LOOK_STRAIGHT", index * 0.2))) for index in (0, 1, 2, 3)]
        code, body, _ = await self.submit(session_id, issued, still)
        self.assertEqual((body["status"], body["retry_allowed"]), ("LIVENESS_REQUIRED", True))
        code, body, _ = await self.submit(session_id, issued, self.follow(engine, issued))
        self.assertEqual((code, body["reason_code"]), (409, "CHALLENGE_ALREADY_USED"))
        code, fresh, _ = await self.challenge(session_id)
        with Session(self.engine) as db, db.begin():
            db.get(LivenessChallenge, UUID(fresh["challenge_id"])).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        code, body, _ = await self.submit(session_id, fresh, self.follow(engine, fresh))
        self.assertEqual((code, body["reason_code"]), (409, "CHALLENGE_EXPIRED"))

    async def test_a_new_challenge_voids_the_previous_one(self):
        session_id, engine = await self.at_liveness()
        code, first, _ = await self.challenge(session_id)
        code, second, _ = await self.challenge(session_id)
        code, body, _ = await self.submit(session_id, first, self.follow(engine, first))
        self.assertEqual(body["reason_code"], "CHALLENGE_ALREADY_USED")
        code, body, _ = await self.submit(session_id, second, self.follow(engine, second))
        self.assertEqual(body["status"], "PROCESSING")

    async def test_attempt_limit_and_state_rules(self):
        self.app.state.settings.max_liveness_attempts = 2
        session_id, engine = await self.at_liveness()
        for _ in range(2):
            code, issued, _ = await self.challenge(session_id)
            self.assertEqual(code, 200)
        code, body, _ = await self.challenge(session_id)
        self.assertEqual((code, body["reason_code"]), (429, "LIVENESS_ATTEMPTS_EXCEEDED"))
        before = self.ready(status="SELFIE_REQUIRED")
        code, body, _ = await self.challenge(before)
        self.assertEqual(body["reason_code"], "LIVENESS_NOT_OPEN")
        no_liveness = self.ready(status="PROCESSING", level="DOCUMENT_FACE")
        code, body, _ = await self.challenge(no_liveness)
        self.assertEqual(body["reason_code"], "LIVENESS_NOT_REQUIRED")

    async def test_other_tenants_cannot_use_a_challenge(self):
        session_id, engine = await self.at_liveness()
        foreign = self.ready(status="LIVENESS_REQUIRED", organization=self.other_org)
        code, body, _ = await self.challenge(foreign)
        self.assertEqual(code, 404)
        code, issued, _ = await self.challenge(session_id)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(sa.select(LivenessChallenge.organization_id)), self.org)


FIXTURE = Path(os.environ.get("KYC_FACE_NATIVE_FIXTURE", "/private/tmp/kyc-opencv-face-smoke.jpg"))
MODELS = Path("var/models")


@unittest.skipUnless(FIXTURE.is_file() and (MODELS / "face_detection_yunet_2023mar.onnx").is_file(),
                     "Native face models and the public OpenCV sample face are not installed.")
class NativePhotoAttackTests(unittest.TestCase):
    """Real YuNet on a real face photo, attacked the way a fraudster would: by moving the photo."""

    def test_moving_and_tilting_a_photo_never_completes_the_challenge(self):
        from kyc.biometrics.opencv import OpenCVFaceEngine
        engine = OpenCVFaceEngine(MODELS / "face_detection_yunet_2023mar.onnx", MODELS / "face_recognition_sface_2021dec.onnx")
        photo = Image.open(FIXTURE).convert("RGB")
        canvas = lambda image: (lambda c: (c.paste(image, ((960 - image.width) // 2, (720 - image.height) // 2)), c)[1])(
            Image.new("RGB", (960, 720), (40, 40, 40)))

        def tilted(horizontal, vertical):
            w, h = photo.size
            dx, dy = w * horizontal, h * vertical
            target = [(dx, dy), (w - dx, -dy), (w + dx, h + dy), (-dx, h - dy)]
            source = [(0, 0), (w, 0), (w, h), (0, h)]
            rows = []
            for (x, y), (u, v) in zip(target, source):
                rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
            coefficients = np.linalg.solve(np.array(rows), np.array(source).reshape(8))
            return canvas(photo.transform(photo.size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC))

        steps = ("LOOK_STRAIGHT", "TURN_LEFT", "LOOK_UP", "TURN_RIGHT")
        variants = [(0, canvas(photo)), (1, tilted(0.12, 0)), (1, canvas(photo.rotate(12))), (2, tilted(0, 0.10)),
                    (3, tilted(-0.12, 0)), (3, canvas(photo.resize((440, 440))))]
        frames = []
        for index, image in variants:
            buffer = BytesIO()
            image.save(buffer, "PNG")
            frames.append((index, image, str(hash(buffer.getvalue()))))
        outcome = assess(frames, steps, engine, None, FaceMatchPolicy())
        self.assertIn(outcome.result, (CheckResult.FAIL, CheckResult.REVIEW), outcome)
        self.assertNotIn(CheckResult.PASS, (outcome.result,))
        self.assertFalse(all(step["completed"] for step in outcome.steps[1:]) if outcome.steps else False)
