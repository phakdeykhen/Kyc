import unittest

from kyc.domain.enums import SessionStatus as S, VerificationLevel as L
from kyc.domain.state_machine import Event as E, InvalidTransition, VerificationEvidence, transition
from kyc.domain.identity import IdentityDocument, OCRField


class StateMachineTests(unittest.TestCase):
    def test_all_supported_verification_paths(self):
        for level in L:
            with self.subTest(level=level):
                status = transition(S.CREATED, E.START, level)
                self.assertEqual(status, S.DOCUMENT_REQUIRED)
                status = transition(status, E.DOCUMENT_SUBMITTED, level)
                status = transition(status, E.DOCUMENT_ACCEPTED, level)
                if level != L.DOCUMENT_ONLY:
                    self.assertEqual(status, S.SELFIE_REQUIRED)
                    status = transition(status, E.SELFIE_ACCEPTED, level)
                if level in {L.DOCUMENT_FACE_LIVENESS, L.DOCUMENT_FACE_LIVENESS_NFC}:
                    self.assertEqual(status, S.LIVENESS_REQUIRED)
                    status = transition(status, E.LIVENESS_ACCEPTED, level)
                if level == L.DOCUMENT_FACE_LIVENESS_NFC:
                    self.assertEqual(status, S.NFC_REQUIRED)
                    status = transition(status, E.NFC_ACCEPTED, level)
                self.assertEqual(status, S.PROCESSING)
                evidence = VerificationEvidence(True, True, True, True, True)
                self.assertEqual(transition(status, E.ASSESSMENT_PASS, level, evidence), S.VERIFIED)

    def test_incomplete_evidence_cannot_verify(self):
        for level in L:
            with self.subTest(level=level), self.assertRaises(InvalidTransition):
                transition(S.PROCESSING, E.ASSESSMENT_PASS, level)
        for field in ["document_valid", "face_match", "liveness_pass", "nfc_verified", "policy_pass"]:
            evidence = {name: True for name in ["document_valid", "face_match", "liveness_pass", "nfc_verified", "policy_pass"]}
            evidence[field] = False
            with self.subTest(field=field), self.assertRaises(InvalidTransition):
                transition(S.PROCESSING, E.ASSESSMENT_PASS, L.DOCUMENT_FACE_LIVENESS_NFC, VerificationEvidence(**evidence))

    def test_document_only_requires_document_and_policy(self):
        self.assertEqual(transition(S.PROCESSING, E.ASSESSMENT_PASS, L.DOCUMENT_ONLY,
                        VerificationEvidence(document_valid=True, policy_pass=True)), S.VERIFIED)

    def test_terminal_states_are_immutable(self):
        for status in [S.VERIFIED, S.REJECTED, S.EXPIRED]:
            for event in E:
                with self.subTest(status=status, event=event), self.assertRaises(InvalidTransition):
                    transition(status, event, L.DOCUMENT_FACE_LIVENESS)

    def test_skip_and_out_of_order_events_are_rejected(self):
        for status, event in [(S.CREATED, E.ASSESSMENT_PASS), (S.DOCUMENT_REQUIRED, E.REVIEW_APPROVED),
                              (S.SELFIE_REQUIRED, E.NFC_ACCEPTED), (S.LIVENESS_REQUIRED, E.DOCUMENT_ACCEPTED)]:
            with self.subTest(status=status, event=event), self.assertRaises(InvalidTransition):
                transition(status, event, L.DOCUMENT_FACE_LIVENESS)

    def test_review_recapture_and_failure_paths(self):
        self.assertEqual(transition(S.DOCUMENT_PROCESSING, E.RECAPTURE_REQUIRED, L.DOCUMENT_ONLY), S.DOCUMENT_REQUIRED)
        self.assertEqual(transition(S.PROCESSING, E.ASSESSMENT_REVIEW, L.DOCUMENT_ONLY), S.MANUAL_REVIEW)
        self.assertEqual(transition(S.MANUAL_REVIEW, E.RECAPTURE_REQUIRED, L.DOCUMENT_ONLY), S.DOCUMENT_REQUIRED)
        self.assertEqual(transition(S.MANUAL_REVIEW, E.REVIEW_REJECTED, L.DOCUMENT_ONLY), S.REJECTED)
        self.assertEqual(transition(S.PROCESSING, E.ASSESSMENT_FAIL, L.DOCUMENT_ONLY), S.REJECTED)
        with self.assertRaises(InvalidTransition):
            transition(S.MANUAL_REVIEW, E.REVIEW_APPROVED, L.DOCUMENT_ONLY)

    def test_all_active_states_can_expire(self):
        for status in set(S) - {S.VERIFIED, S.REJECTED, S.EXPIRED}:
            self.assertEqual(transition(status, E.EXPIRE, L.DOCUMENT_FACE_LIVENESS), S.EXPIRED)

    def test_levels_cannot_accept_unrequested_capture_stages(self):
        for status, event, level in [(S.SELFIE_REQUIRED, E.SELFIE_ACCEPTED, L.DOCUMENT_ONLY),
                                      (S.LIVENESS_REQUIRED, E.LIVENESS_ACCEPTED, L.DOCUMENT_FACE),
                                      (S.NFC_REQUIRED, E.NFC_ACCEPTED, L.DOCUMENT_FACE_LIVENESS)]:
            with self.subTest(status=status, level=level), self.assertRaises(InvalidTransition):
                transition(status, event, level)

    def test_khmer_and_low_confidence_are_preserved(self):
        field = OCRField(field="full_name_local", raw_value="សុខ ដារ៉ា", normalized_value="សុខ ដារ៉ា", confidence=0.2)
        document = IdentityDocument(document_type="KH_NATIONAL_ID", full_name_local=field.raw_value, fields=[field])
        self.assertEqual(document.fields[0].confidence, 0.2)
        self.assertEqual(document.fields[0].raw_value, "សុខ ដារ៉ា")
        self.assertIsNone(document.expiry_date)
