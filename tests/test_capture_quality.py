from io import BytesIO
import unittest

from PIL import Image

from kyc.documents.requirements import ID1_ASPECT, TD3_ASPECT
from kyc.engines.capture_quality import SCORE_NAMES, CaptureRejected, DocumentQualityPolicy, HeuristicDocumentQualityEngine, decode_capture
from tests import images

LIMITS = {"max_bytes": 20 * 1024 * 1024, "max_pixels": 40_000_000}


def assess(image, aspect=ID1_ASPECT, fmt="JPEG", **options):
    return HeuristicDocumentQualityEngine().assess(decode_capture(images.encode(image, fmt, **options), **LIMITS), aspect)


class QualityGateTests(unittest.TestCase):
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
