"""Face unit tests use explicit injected geometry/features, never model fixtures."""

from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageFilter

from kyc.biometrics import (
    FaceDetection, FaceEmbedding, FaceEngineUnavailable, FaceMatchPolicy,
    FaceQualityPolicy, InvalidFaceEmbedding, OpenCVFaceEngine,
    assess_face_quality, compare_embeddings, deserialize_embedding, serialize_embedding,
)
from kyc.biometrics.opencv import verify_model
from scripts.download_face_models import fetch_verified, verified_file


def textured_image(size=(600, 800)):
    pixels = np.random.default_rng(1234).integers(70, 186, (size[1], size[0], 3), dtype=np.uint8)
    return Image.fromarray(pixels)


def face(bbox=(150, 200, 300, 400), confidence=0.97):
    x, y, width, height = bbox
    return FaceDetection(bbox, tuple((x + width * px, y + height * py)
                                    for px, py in ((.3, .32), (.7, .32), (.5, .55), (.35, .75), (.65, .75))), confidence)


def vector(cosine=1.0):
    result = np.zeros(128, dtype=np.float32)
    result[0], result[1] = cosine, np.sqrt(1 - cosine * cosine)
    return result


class FaceQualityTests(unittest.TestCase):
    def test_usable_selfie_keeps_unverified_eyes_and_occlusion(self):
        result = assess_face_quality(textured_image(), [face()])
        self.assertTrue(result.accepted, result.reason_codes)
        self.assertEqual(result.instructions, ())
        self.assertEqual(result.face_count, 1)
        self.assertEqual(result.detection, face())
        self.assertEqual(result.unverified_checks, ("EYES_VISIBLE", "SEVERE_OCCLUSION"))
        self.assertEqual(result.reason_codes, ("FACE_EYE_VISIBILITY_UNVERIFIED", "FACE_OCCLUSION_UNVERIFIED"))
        self.assertTrue(all(0 <= score <= 1 for score in result.scores.values()))

    def test_zero_and_multiple_faces_cannot_select_reference_or_live(self):
        for detections, reason, instruction in [([], "FACE_NOT_DETECTED", "CENTER_FACE"),
                                                  ([face(), face((10, 10, 130, 180))], "MULTIPLE_FACES", "ONE_FACE_ONLY")]:
            with self.subTest(reason):
                result = assess_face_quality(textured_image(), detections)
                self.assertFalse(result.accepted)
                self.assertIsNone(result.detection)
                self.assertIn(reason, result.reason_codes)
                self.assertIn(instruction, result.instructions)

    def test_framing_and_size_feedback(self):
        for bbox, reason, instruction in [((280, 350, 40, 50), "FACE_TOO_SMALL", "MOVE_CLOSER"),
                                         ((0, 0, 200, 240), "FACE_OFF_CENTER", "CENTER_FACE"),
                                         ((20, 20, 560, 760), "FACE_TOO_CLOSE", "MOVE_BACK"),
                                         ((-20, 200, 300, 400), "FACE_CROPPED", "MOVE_BACK")]:
            with self.subTest(reason):
                result = assess_face_quality(textured_image(), [face(bbox)])
                self.assertFalse(result.accepted)
                self.assertIn(reason, result.reason_codes)
                self.assertIn(instruction, result.instructions)

    def test_document_portrait_does_not_apply_selfie_framing(self):
        detected = face((0, 0, 100, 120))
        portrait = assess_face_quality(textured_image(), [detected], "DOCUMENT_PORTRAIT")
        self.assertTrue(portrait.accepted, portrait.reason_codes)
        selfie = assess_face_quality(textured_image(), [detected])
        self.assertFalse(selfie.accepted)
        self.assertIn("FACE_TOO_SMALL", selfie.reason_codes)
        self.assertIn("FACE_OFF_CENTER", selfie.reason_codes)

    def test_dark_frame_requires_light_without_claiming_motion_blur(self):
        result = assess_face_quality(Image.new("RGB", (600, 800), (5, 5, 5)), [face()])
        self.assertFalse(result.accepted)
        self.assertIn("FACE_TOO_DARK", result.reason_codes)
        self.assertNotIn("FACE_BLURRY", result.reason_codes)
        self.assertIn("MORE_LIGHT", result.instructions)

    def test_overexposed_frame_requires_less_light(self):
        result = assess_face_quality(Image.new("RGB", (600, 800), (250, 250, 250)), [face()])
        self.assertIn("FACE_OVEREXPOSED", result.reason_codes)
        self.assertIn("REDUCE_LIGHT", result.instructions)

    def test_blur_is_measured_inside_face_crop(self):
        result = assess_face_quality(textured_image().filter(ImageFilter.GaussianBlur(12)), [face()])
        self.assertFalse(result.accepted)
        self.assertIn("FACE_BLURRY", result.reason_codes)
        self.assertIn("HOLD_STILL", result.instructions)

    def test_rotated_and_asymmetric_landmarks_request_frontal_pose(self):
        normal = face()
        for points in [((240, 280), (360, 350), *normal.landmarks[2:]),
                       (*normal.landmarks[:2], (425, 420), *normal.landmarks[3:])]:
            with self.subTest(points):
                result = assess_face_quality(textured_image(), [FaceDetection(normal.bbox, points, .95)])
                self.assertFalse(result.accepted)
                self.assertIn("FACE_POSE_UNACCEPTABLE", result.reason_codes)
                self.assertIn("FACE_CAMERA", result.instructions)

    def test_invalid_landmark_geometry_does_not_assert_occlusion(self):
        result = assess_face_quality(textured_image(), [FaceDetection(face().bbox, ((300, 400),) * 5, .95)])
        self.assertFalse(result.accepted)
        self.assertIn("FACE_LANDMARKS_UNRELIABLE", result.reason_codes)
        self.assertIn("FACE_OCCLUSION_UNVERIFIED", result.reason_codes)

    def test_quality_policy_is_versioned_and_uncalibrated(self):
        self.assertFalse(FaceQualityPolicy().calibrated)
        result = assess_face_quality(textured_image(), [face()], policy=FaceQualityPolicy(version="test-v2"))
        self.assertEqual(result.policy_version, "test-v2")

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            assess_face_quality(textured_image(), [face()], "GALLERY_SELFIE")


class EmbeddingTests(unittest.TestCase):
    def test_serialization_retains_internal_provenance_and_exact_values(self):
        embedding = FaceEmbedding(vector(.5))
        payload = serialize_embedding(embedding)
        restored = deserialize_embedding(payload)
        np.testing.assert_array_equal(restored.vector, embedding.vector)
        self.assertEqual(restored.model_sha256, embedding.model_sha256)
        self.assertEqual(restored.dimension, 128)
        self.assertNotIn("vector=", repr(restored))
        self.assertFalse(restored.vector.flags.writeable)

    def test_invalid_dimension_nonfinite_zero_and_unnormalized_embeddings(self):
        for values in [np.zeros(128), np.ones(128), np.ones(127), np.full(128, np.nan), np.full(128, np.inf)]:
            with self.subTest(shape=values.shape), self.assertRaises(InvalidFaceEmbedding):
                FaceEmbedding(values)

    def test_invalid_provenance_is_rejected(self):
        for metadata in [{"model_name": ""}, {"model_version": ""}, {"model_sha256": "z" * 64}]:
            with self.subTest(metadata), self.assertRaises(InvalidFaceEmbedding):
                FaceEmbedding(vector(), **metadata)

    def test_malformed_template_payloads_fail_closed(self):
        for payload in [b"not json", b"[]", b"{}", b"\xff", b"x" * 32769]:
            with self.subTest(payload=payload[:20]), self.assertRaises(InvalidFaceEmbedding):
                deserialize_embedding(payload)
        value = json.loads(serialize_embedding(FaceEmbedding(vector())))
        value["dimension"] = 127
        with self.assertRaises(InvalidFaceEmbedding):
            deserialize_embedding(json.dumps(value).encode())

    def test_unvalidated_thresholds_always_require_review(self):
        reference = FaceEmbedding(vector())
        for similarity in [1, .5, .3, 0, -1]:
            with self.subTest(similarity):
                comparison = compare_embeddings(reference, FaceEmbedding(vector(similarity)))
                self.assertEqual(comparison.decision, "REVIEW")
                self.assertEqual(comparison.reason_codes, ("UNCALIBRATED_FACE_POLICY",))
                self.assertAlmostEqual(comparison.score, similarity, places=6)
                self.assertEqual(comparison.metric, "COSINE_SIMILARITY")

    def test_calibrated_policy_pass_review_and_fail(self):
        policy = FaceMatchPolicy(version="validation-dataset-v2", calibrated=True,
                                 pass_threshold=.5, fail_threshold=.2)
        for similarity, decision in [(.7, "PASS"), (.5, "PASS"), (.3, "REVIEW"), (.2, "REVIEW"), (.1, "FAIL")]:
            with self.subTest(similarity):
                comparison = compare_embeddings(FaceEmbedding(vector()), FaceEmbedding(vector(similarity)), policy)
                self.assertEqual(comparison.decision, decision)
                self.assertEqual(comparison.threshold_policy_version, policy.version)
                self.assertTrue(comparison.calibrated)

    def test_model_version_and_digest_must_match_both_templates_and_policy(self):
        reference = FaceEmbedding(vector())
        for metadata in [{"model_name": "Other model"}, {"model_version": "2026"}, {"model_sha256": "0" * 64}]:
            with self.subTest(metadata), self.assertRaises(InvalidFaceEmbedding):
                compare_embeddings(reference, FaceEmbedding(vector(), **metadata))
        other_model = FaceEmbedding(vector(), model_sha256="0" * 64)
        with self.assertRaises(InvalidFaceEmbedding):
            compare_embeddings(other_model, other_model)

    def test_template_copy_does_not_alias_mutable_input(self):
        original = vector()
        embedding = FaceEmbedding(original)
        original[:] = 0
        self.assertAlmostEqual(compare_embeddings(embedding, embedding).score, 1)

    def test_threshold_validation(self):
        for values in [{"pass_threshold": float("nan")}, {"fail_threshold": 2},
                       {"pass_threshold": .1, "fail_threshold": .2}, {"version": ""}]:
            with self.subTest(values), self.assertRaises(ValueError):
                FaceMatchPolicy(**values)


class FakeDetector:
    def __init__(self, rows):
        self.rows = rows
        self.input_size = None
        self.input_bgr = None

    def setInputSize(self, size):
        self.input_size = size

    def detect(self, pixels):
        self.input_bgr = pixels.copy()
        return 1, self.rows


class FakeRecognizer:
    def __init__(self):
        self.input_bgr = self.input_row = None
        self.features = np.arange(1, 129, dtype=np.float32).reshape(1, 128)

    def alignCrop(self, pixels, row):
        self.input_bgr, self.input_row = pixels.copy(), row.copy()
        return pixels[:112, :112]

    def feature(self, aligned):
        return self.features


def injected_native_engine(rows):
    engine = OpenCVFaceEngine("unused-yunet", "unused-sface")
    engine._detector = FakeDetector(rows)
    engine._recognizer = FakeRecognizer()
    engine._cv = types.SimpleNamespace(INTER_AREA=3, resize=lambda pixels, size, interpolation:
                                      np.asarray(Image.fromarray(pixels).resize(size)))
    return engine


class NativeAdapterTests(unittest.TestCase):
    def test_missing_or_corrupt_models_report_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.onnx"
            engine = OpenCVFaceEngine(path, path)
            self.assertEqual(engine.unavailable_reason(), "FACE_MODELS_UNAVAILABLE")
            path.write_bytes(b"version https://git-lfs.github.com/spec/v1")
            self.assertEqual(engine.unavailable_reason(), "FACE_MODELS_UNAVAILABLE")
            with self.assertRaises(FaceEngineUnavailable):
                engine.detect(textured_image())

    def test_runtime_does_not_attempt_network_or_fetch(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Runtime fetch forbidden")):
            self.assertEqual(OpenCVFaceEngine("missing-a", "missing-b").unavailable_reason(), "FACE_MODELS_UNAVAILABLE")

    def test_sha256_verifies_every_model_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.onnx"
            path.write_bytes(b"exact-model-bytes")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            verify_model(path, digest)
            path.write_bytes(b"exact-model-byte!")
            with self.assertRaises(FaceEngineUnavailable):
                verify_model(path, digest)

    def test_resolution_cap_scales_boxes_and_landmarks_back_to_original(self):
        row = np.array([[128, 64, 256, 320, 192, 160, 320, 160, 256, 224, 208, 288, 304, 288, .95]], dtype=np.float32)
        engine = injected_native_engine(row)
        detections = engine.detect(textured_image((2000, 1000)))
        self.assertEqual(engine._detector.input_size, (1280, 640))
        np.testing.assert_allclose(detections[0].bbox, (200, 100, 400, 500))
        np.testing.assert_allclose(detections[0].landmarks[0], (300, 250))

    def test_empty_detector_output_is_no_face_and_malformed_output_fails(self):
        self.assertEqual(injected_native_engine(None).detect(textured_image()), [])
        for rows in [np.zeros((1, 14)), np.full((1, 15), np.nan), np.array([[0] * 15])]:
            with self.subTest(rows=rows.shape), self.assertRaises(FaceEngineUnavailable):
                injected_native_engine(rows).detect(textured_image())

    def test_rgb_is_converted_to_bgr_before_alignment_and_features_are_normalized(self):
        engine = injected_native_engine(None)
        result = engine.embed(Image.new("RGB", (600, 800), (10, 20, 30)), face())
        np.testing.assert_array_equal(engine._recognizer.input_bgr[0, 0], (30, 20, 10))
        np.testing.assert_allclose(engine._recognizer.input_row[:4], face().bbox)
        self.assertEqual(result.dimension, 128)
        self.assertAlmostEqual(float(np.linalg.norm(result.vector)), 1, places=6)
        engine._recognizer.features[:] = 0
        self.assertGreater(float(result.vector[0]), 0)

    def test_native_invalid_embedding_does_not_create_template(self):
        for feature in [np.zeros((1, 128)), np.full((1, 128), np.nan), np.ones((1, 64))]:
            engine = injected_native_engine(None)
            engine._recognizer.features = feature
            with self.subTest(feature=feature.shape), self.assertRaises(InvalidFaceEmbedding):
                engine.embed(textured_image(), face())

    def test_invalid_cpu_resource_policy_is_rejected(self):
        for options in [{"detection_max_dimension": 10000}, {"detection_threshold": 1.0}]:
            with self.subTest(options), self.assertRaises(ValueError):
                OpenCVFaceEngine("a", "b", **options)

    def test_native_detection_operations_are_serialized_across_requests(self):
        engine = injected_native_engine(np.array([(*face().bbox,
                                                   *(value for point in face().landmarks for value in point), .95)], dtype=np.float32))
        active, peak = 0, 0
        counter_lock = threading.Lock()
        original_detect = engine._detector.detect

        def tracked_detect(pixels):
            nonlocal active, peak
            with counter_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.01)
            result = original_detect(pixels)
            with counter_lock:
                active -= 1
            return result

        engine._detector.detect = tracked_detect
        with ThreadPoolExecutor(max_workers=4) as executor:
            detections = list(executor.map(engine.detect, [textured_image()] * 8))
        self.assertEqual(peak, 1)
        self.assertEqual(len(detections), 8)
        self.assertTrue(all(result[0].bbox == face().bbox for result in detections))


class ModelProvisioningTests(unittest.TestCase):
    def test_verified_download_is_atomic_and_existing_correct_file_is_reused(self):
        content = b"test model bytes"
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "model.onnx"
            with patch("scripts.download_face_models.urlopen", return_value=BytesIO(content)) as opener:
                fetch_verified("https://example.invalid/model", destination, digest, len(content))
                self.assertEqual(opener.call_count, 1)
                fetch_verified("https://example.invalid/model", destination, digest, len(content))
                self.assertEqual(opener.call_count, 1)
            self.assertTrue(verified_file(destination, digest, len(content)))
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_download_integrity_failure_preserves_existing_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "model.onnx"
            destination.write_bytes(b"previous model")
            for content, size in [(b"broken bytes", 12), (b"oversized contents", 4)]:
                with self.subTest(content), patch("scripts.download_face_models.urlopen", return_value=BytesIO(content)):
                    with self.assertRaises(ValueError):
                        fetch_verified("https://example.invalid/model", destination, "0" * 64, size)
                    self.assertEqual(destination.read_bytes(), b"previous model")
                    self.assertEqual(list(Path(directory).iterdir()), [destination])


class NativeModelSmokeTests(unittest.TestCase):
    """Optional real models and public OpenCV sample; no identity accuracy claim.

    Install the pinned bundle, and set KYC_FACE_NATIVE_FIXTURE to OpenCV's
    samples/data/lena.jpg (SHA-256 below), to run these tests in CI. The local
    provisioning smoke also places that fixture in /private/tmp. No image or
    biometric template is committed, and unavailable fixtures produce a skip.
    """

    @classmethod
    def setUpClass(cls):
        model_directory = Path(os.environ.get("KYC_FACE_NATIVE_MODELS_DIR", "var/models"))
        fixture = Path(os.environ.get("KYC_FACE_NATIVE_FIXTURE", "/private/tmp/kyc-opencv-face-smoke.jpg"))
        yunet = model_directory / "face_detection_yunet_2023mar.onnx"
        sface = model_directory / "face_recognition_sface_2021dec.onnx"
        if not all(path.is_file() for path in (yunet, sface, fixture)):
            raise unittest.SkipTest("Pinned native face models and public fixture are not installed")
        if hashlib.sha256(fixture.read_bytes()).hexdigest() != "7de7ed51a1594fff247f4cae2301eceacf5313d6011e37b4a4c8733f7bb72c07":
            raise ValueError("The optional native face fixture must match OpenCV's pinned public sample")
        cls.engine = OpenCVFaceEngine(yunet, sface)
        cls.image = Image.open(fixture).convert("RGB")

    def test_real_cpu_detection_alignment_embedding_and_default_review(self):
        self.assertIsNone(self.engine.unavailable_reason())
        assessment = self.engine.assess(self.image)
        self.assertEqual(assessment.face_count, 1)
        self.assertTrue(assessment.accepted, assessment.reason_codes)
        embedding = self.engine.embed(self.image, assessment.detection)
        self.assertEqual(embedding.dimension, 128)
        self.assertAlmostEqual(float(np.linalg.norm(embedding.vector)), 1, places=5)
        comparison = compare_embeddings(embedding, embedding)
        self.assertAlmostEqual(comparison.score, 1, places=6)
        self.assertEqual(comparison.decision, "REVIEW")
        self.assertEqual(comparison.reason_codes, ("UNCALIBRATED_FACE_POLICY",))

    def test_real_detector_rejects_blank_and_multiple_face_capture(self):
        blank = self.engine.assess(Image.new("RGB", self.image.size, "gray"))
        self.assertFalse(blank.accepted)
        self.assertIn("FACE_NOT_DETECTED", blank.reason_codes)
        multiple = Image.new("RGB", (self.image.width * 2, self.image.height))
        multiple.paste(self.image, (0, 0))
        multiple.paste(self.image, (self.image.width, 0))
        assessment = self.engine.assess(multiple)
        self.assertFalse(assessment.accepted)
        self.assertEqual(assessment.face_count, 2)
        self.assertIn("MULTIPLE_FACES", assessment.reason_codes)
        self.assertIsNone(assessment.detection)
