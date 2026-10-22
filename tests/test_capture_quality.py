from io import BytesIO
import unittest

import numpy as np
from PIL import Image, ImageFilter
from unittest.mock import patch

from kyc.documents.requirements import ID1_ASPECT, TD3_ASPECT
from kyc.engines.capture_quality import SCORE_NAMES, CaptureRejected, DocumentQualityPolicy, HeuristicDocumentQualityEngine, decode_capture
from tests import images

LIMITS = {"max_bytes": 20 * 1024 * 1024, "max_pixels": 40_000_000}


def assess(image, aspect=ID1_ASPECT, fmt="JPEG", **options):
    return HeuristicDocumentQualityEngine().assess(decode_capture(images.encode(image, fmt, **options), **LIMITS), aspect)


class QualityGateTests(unittest.TestCase):
    def test_printed_region_with_wrong_aspect_is_not_used_as_a_card_quadrilateral(self):
        engine = HeuristicDocumentQualityEngine()
        ink = (np.array([[0, 0], [340, 0], [385, 60], [30, 140]], dtype=np.float32), .35, 2, False)
        crop = np.asarray(images.card(1280))
        with patch.object(engine, "_locate", return_value=ink):
            self.assertEqual(engine.locate_corners(crop, ID1_ASPECT).tolist(),
                             [[0, 0], [1279, 0], [1279, crop.shape[0] - 1], [0, crop.shape[0] - 1]])

    def test_whole_portrait_camera_frame_cannot_rotate_the_landscape_card(self):
        engine = HeuristicDocumentQualityEngine()
        photo = np.asarray(images.scene(images.card(760), size=(960, 1280)))
        whole_frame = (np.array([[0, 0], [299, 0], [299, 399], [0, 399]], dtype=np.float32), 1., 4, False)
        with patch.object(engine, "_locate", return_value=whole_frame):
            corners = engine.locate_corners(photo, ID1_ASPECT)
        self.assertIsNotNone(corners)
        self.assertGreater(np.linalg.norm(corners[1] - corners[0]), np.linalg.norm(corners[2] - corners[1]))
        self.assertGreater(corners[:, 1].min(), 350)
        self.assertLess(corners[:, 1].max(), 930)

    def test_usable_captures_are_accepted(self):
        for name, image, aspect in [("card", images.good(), ID1_ASPECT),
                                    ("passport", images.passport_page(), TD3_ASPECT),
                                    ("rotated", images.good().rotate(4, fillcolor=(58, 48, 44)), ID1_ASPECT)]:
            with self.subTest(name):
                result = assess(image, aspect)
                self.assertTrue(result.accepted, result.reason_codes)
                self.assertEqual(result.reason_codes, ())
                self.assertEqual(set(result.scores), set(SCORE_NAMES))
                self.assertTrue(all(0 <= value <= 1 for value in result.scores.values()))
                self.assertEqual(result.geometry["orientation"], "LANDSCAPE")

    def test_png_and_compressed_jpeg_are_accepted(self):
        self.assertTrue(assess(images.good(), fmt="PNG").accepted)
        self.assertTrue(assess(images.good(), quality=70).accepted)

    def test_unusable_captures_request_recapture_with_instructions(self):
        cases = {
            "blurred": ("IMAGE_BLURRY", "HOLD_STILL"),
            "dark": ("TOO_DARK", "MORE_LIGHT"),
            "overexposed": ("GLARE_DETECTED", "REDUCE_GLARE"),
            "glare": ("GLARE_DETECTED", "REDUCE_GLARE"),
            "shadow": ("SHADOW_DETECTED", "AVOID_SHADOW"),
            "too_far": ("DOCUMENT_TOO_FAR", "MOVE_CLOSER"),
            "too_close": ("DOCUMENT_CROPPED", "MOVE_BACK"),
            "cropped": ("DOCUMENT_CROPPED", "CENTER_DOCUMENT"),
            "two_documents": ("MULTIPLE_DOCUMENTS", "ONE_DOCUMENT_ONLY"),
            "skewed": ("PERSPECTIVE_DISTORTED", "ALIGN_DOCUMENT"),
            "low_resolution": ("RESOLUTION_TOO_LOW", "USE_HIGHER_RESOLUTION"),
            "blank": ("DOCUMENT_NOT_DETECTED", "USE_CONTRASTING_BACKGROUND"),
        }
        for name, (reason, instruction) in cases.items():
            with self.subTest(name):
                result = assess(getattr(images, name)())
                self.assertFalse(result.accepted)
                self.assertIn(reason, result.reason_codes)
                self.assertIn(instruction, result.instructions)

    def test_dark_frames_are_not_misreported_as_motion_blur(self):
        result = assess(images.dark())
        self.assertNotIn("IMAGE_BLURRY", result.reason_codes)

    def test_wrong_document_shape_is_flagged(self):
        result = assess(images.scene(images.card(700, ratio=1.0)), ID1_ASPECT)
        self.assertIn("DOCUMENT_SHAPE_UNEXPECTED", result.reason_codes)

    def test_portrait_orientation_is_reported_not_rejected(self):
        result = assess(images.scene(images.card().rotate(90, expand=True), size=(1200, 1600)))
        self.assertTrue(result.accepted, result.reason_codes)
        self.assertEqual(result.geometry["orientation"], "PORTRAIT")
        self.assertEqual(result.geometry["rotation_hint_degrees"], 90)

    def test_an_image_already_cropped_to_the_card_is_accepted(self):
        # Uploads and scanner apps send the card alone, with no background around it.
        result = assess(images.card(1400))
        self.assertTrue(result.accepted, result.reason_codes)
        self.assertTrue(result.geometry["precropped"])
        self.assertEqual(result.scores["document_coverage"], 1.0)
        corners = HeuristicDocumentQualityEngine().locate_corners(np.asarray(images.card(1400)), ID1_ASPECT)
        self.assertEqual(corners.tolist(), [[0, 0], [1399, 0], [1399, 882], [0, 882]])
        # Image quality still applies to a cropped card.
        self.assertIn("RESOLUTION_TOO_LOW", assess(images.card(500)).reason_codes)
        self.assertIn("IMAGE_BLURRY", assess(images.card(1400).filter(ImageFilter.GaussianBlur(6))).reason_codes)

    def test_a_camera_frame_cut_through_the_card_is_not_mistaken_for_a_crop(self):
        for frame in ((1600, 1200), (1920, 1080)):   # 4:3 and 16:9 phone frames are not card-shaped
            with self.subTest(frame=frame):
                close = images.scene(images.card(1900), size=frame)
                result = assess(close)
                self.assertFalse(result.accepted)
                self.assertNotIn("precropped", result.geometry)
        self.assertNotIn("precropped", assess(images.card(1200, ratio=1.0)).geometry, "A square crop is not an ID card")

    def test_policy_is_versioned_and_marked_uncalibrated(self):
        policy = DocumentQualityPolicy()
        self.assertFalse(policy.calibrated)
        self.assertEqual(assess(images.good()).policy_version, policy.version)


class DecodeTests(unittest.TestCase):
    def test_rejects_unusable_inputs(self):
        jpeg = images.encode(images.good())
        gif = BytesIO()
        Image.new("RGB", (64, 64)).save(gif, "GIF")
        frames = [Image.new("RGB", (64, 64), color) for color in ("red", "blue")]
        animated = BytesIO()
        frames[0].save(animated, "PNG", save_all=True, append_images=frames[1:])
        cases = [
            (b"", LIMITS, "EMPTY_FILE"),
            (jpeg, {**LIMITS, "max_bytes": 1000}, "FILE_TOO_LARGE"),
            (b"%PDF-1.7 not an image", LIMITS, "UNREADABLE_IMAGE"),
            (gif.getvalue(), LIMITS, "UNSUPPORTED_FORMAT"),
            (animated.getvalue(), LIMITS, "ANIMATED_IMAGE"),
            (jpeg[: len(jpeg) // 3], LIMITS, "UNREADABLE_IMAGE"),
            (jpeg, {**LIMITS, "max_pixels": 1_000_000}, "IMAGE_DIMENSIONS_TOO_LARGE"),
        ]
        for data, limits, reason in cases:
            with self.subTest(reason), self.assertRaises(CaptureRejected) as raised:
                decode_capture(data, **limits)
            self.assertEqual(raised.exception.reason_code, reason)

    def test_exif_orientation_is_applied_and_metadata_dropped(self):
        image = images.good()
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 degrees clockwise on display
        exif[0x010F] = "Example camera maker"
        decoded = decode_capture(images.encode(image, exif=exif.tobytes()), **LIMITS)
        self.assertEqual(decoded.pixels.shape[:2], (image.width, image.height))
        self.assertEqual(decoded.media_type, "image/jpeg")
        self.assertFalse(hasattr(decoded, "exif"))
