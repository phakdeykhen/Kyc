"""Phase 7: barcode decoding, payload formats, signatures and consistency (synthetic payloads only)."""

import base64
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
import numpy as np
from PIL import Image

from kyc.barcode import engine
from kyc.barcode.evaluate import assess
from kyc.barcode.payload import parse
from kyc.barcode.signatures import TrustStore, verify
from kyc.domain.enums import CheckResult, DocumentType
from kyc.domain.identity import IdentityDocument, OCRField
from tests import images


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jws(claims: dict, key, kid="issuer-test-1", alg="ES256") -> str:
    header = b64url(json.dumps({"alg": alg, "kid": kid}).encode())
    body = b64url(json.dumps(claims).encode())
    if alg == "none":
        return f"{header}.{body}."
    r, s = decode_dss_signature(key.sign(f"{header}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{header}.{body}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def document(**values):
    base = {"document_number": "0012345678", "full_name": "CHAN DARA", "date_of_birth": "1988-07-02"} | values
    fields = [OCRField(field=name, raw_value=value, normalized_value=value, confidence=0.9) for name, value in base.items()]
    return IdentityDocument(document_type=DocumentType.KH_NSSF, fields=fields, **base)


def read(text: str, symbology="QR_CODE") -> engine.BarcodeRead:
    return engine.BarcodeRead(symbology, text.encode(), text, (0.6, 0.5, 0.9, 0.95))


class DecodeTests(unittest.TestCase):
    def test_qr_and_pdf417_are_decoded_from_a_photographed_card(self):
        card = images.card()
        card.paste(images.qr_image('{"member":"0012345678"}', 260), (700, 330))
        found = engine.decode(np.asarray(images.photographed(card)))
        self.assertEqual([(item.symbology, item.text) for item in found], [("QR_CODE", '{"member":"0012345678"}')])
        pdf = images.qr_image("@\n\x1e\rANSI 636000090002DL00410278ZV03190008DLDAQT64235789\nDCSSAMPLE\n", 600, "PDF417")
        canvas = Image.new("RGB", (900, 500), "white")
        canvas.paste(pdf, (150, 100))
        self.assertEqual(engine.decode(np.asarray(canvas))[0].symbology, "PDF417")

    def test_nothing_to_decode(self):
        self.assertEqual(engine.decode(np.asarray(images.good())), [])


class PayloadTests(unittest.TestCase):
    def test_structured_formats(self):
        json_payload = parse(b"", '{"member":"0012345678","name":"chan dara","dob":"1988-07-02"}')
        self.assertEqual((json_payload.format, json_payload.fields),
                         ("JSON", {"document_number": "0012345678", "full_name": "CHAN DARA", "date_of_birth": "1988-07-02"}))
        pairs = parse(b"", "ID=090807060; DOB=02.07.1988; SEX=M")
        self.assertEqual((pairs.format, pairs.fields["date_of_birth"], pairs.fields["sex"]), ("KEY_VALUE", "1988-07-02", "M"))
        khmer_digits = parse(b"", '{"member":"០០១២៣៤៥៦៧៨"}')
        self.assertEqual(khmer_digits.fields["document_number"], "0012345678")

    def test_aamva_driving_licence_pdf417(self):
        text = "@\n\x1e\rANSI 636014080102DL00410288ZC03290034DLDAQD1234562\nDCSSAMPLE\nDACJANE\nDBB04191988\nDBA04192030\nDBC2\n"
        parsed = parse(text.encode(), text)
        self.assertEqual(parsed.format, "AAMVA")
        self.assertEqual(parsed.fields, {"document_number": "D1234562", "surname": "SAMPLE", "given_names": "JANE",
                                         "date_of_birth": "1988-04-19", "expiry_date": "2030-04-19", "sex": "F"})

    def test_visible_digital_seal_and_plain_text(self):
        seal = parse(b"\xdc\x03ABC", "")
        self.assertEqual((seal.format, seal.signature_present), ("ICAO_VDS", True))
        self.assertEqual(verify(seal, TrustStore())[1], "VDS_TRUST_LIST_UNAVAILABLE")
        text = parse(b"", "https://example.org/verify")
        self.assertEqual((text.format, text.format_valid, text.fields), ("TEXT", None, {}))
        self.assertEqual(parse(b"", "{not json").format_valid, False)


class SignatureTests(unittest.TestCase):
    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.trust = TrustStore({"issuer-test-1": self.key.public_key()})
        self.claims = {"member": "0012345678", "name": "CHAN DARA", "dob": "1988-07-02"}

    def test_valid_signature_with_a_trusted_key(self):
        parsed = parse(b"", jws(self.claims, self.key))
        self.assertEqual(verify(parsed, self.trust), (True, "SIGNATURE_VALID"))

    def test_tampered_unknown_key_and_alg_none(self):
        token = jws(self.claims, self.key)
        header, body, signature = token.split(".")
        forged_body = b64url(json.dumps(self.claims | {"member": "9999999999"}).encode())
        self.assertEqual(verify(parse(b"", f"{header}.{forged_body}.{signature}"), self.trust), (False, "SIGNATURE_INVALID"))
        self.assertEqual(verify(parse(b"", jws(self.claims, self.key, kid="someone-else")), self.trust), (None, "NO_TRUSTED_KEY"))
        self.assertEqual(verify(parse(b"", jws(self.claims, None, alg="none")), self.trust), (False, "UNSIGNED_ALG_NONE"))

    def test_trust_store_loads_pem_keys_from_file(self):
        pem = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trust.json"
            path.write_text(json.dumps({"issuer-test-1": pem}))
            loaded = TrustStore.load(path)
        self.assertEqual(verify(parse(b"", jws(self.claims, self.key)), loaded)[0], True)


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.trust = TrustStore({"issuer-test-1": self.key.public_key()})

    def test_consistent_unsigned_code_passes_as_consistent_only(self):
        check, evidence = assess({"BACK": [read(images.NSSF_QR_PAYLOAD)]}, document(), TrustStore())
        self.assertEqual((check.result, check.reason_codes), (CheckResult.PASS, ("BARCODE_DATA_CONSISTENT",)))
        self.assertEqual(evidence[0].consistency, {"document_number": "MATCH", "date_of_birth": "MATCH", "full_name": "MATCH"})
        self.assertIsNone(check.details["codes"][0]["signature_valid"])

    def test_disagreement_with_the_printed_card_is_flagged(self):
        check, _ = assess({"BACK": [read(images.NSSF_QR_PAYLOAD)]}, document(date_of_birth="1988-07-03"), TrustStore())
        self.assertEqual((check.result, check.reason_codes), (CheckResult.REVIEW, ("BARCODE_VISUAL_DATE_OF_BIRTH_MISMATCH",)))

    def test_signed_codes(self):
        claims = {"member": "0012345678", "name": "CHAN DARA", "dob": "1988-07-02"}
        good, _ = assess({"BACK": [read(jws(claims, self.key))]}, document(), self.trust)
        self.assertEqual((good.result, good.reason_codes), (CheckResult.PASS, ("BARCODE_SIGNATURE_VALID",)))
        token = jws(claims, self.key)
        header, _, signature = token.split(".")
        forged = f"{header}.{b64url(json.dumps(claims | {'dob': '1990-01-01'}).encode())}.{signature}"
        bad, _ = assess({"BACK": [read(forged)]}, document(), self.trust)
        self.assertEqual(bad.result, CheckResult.FAIL)
        self.assertIn("BARCODE_SIGNATURE_INVALID", bad.reason_codes)

    def test_absent_or_uncomparable_codes(self):
        self.assertEqual(assess({}, document(), TrustStore())[0].result, CheckResult.NOT_APPLICABLE)
        self.assertEqual(assess({}, document(), TrustStore(), expected=True)[0].reason_codes, ("BARCODE_NOT_FOUND",))
        url, _ = assess({"BACK": [read("https://example.org/v")]}, document(), TrustStore())
        self.assertEqual((url.result, url.reason_codes), (CheckResult.NOT_APPLICABLE, ("BARCODE_NOT_COMPARABLE",)))
