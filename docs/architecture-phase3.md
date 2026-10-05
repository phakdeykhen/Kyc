# Phase 3 — Cambodia National ID adapter

Status: implemented on 5 October 2026. Phase 4 (Cambodia NSSF adapter) has not started.

## 1. Design

Phase 3 adds the document engine's first adapter. When every required side has passed
the Phase 2 quality gate, the session enters `DOCUMENT_PROCESSING` and the
`DocumentProcessor` runs the spec §7 pipeline:

```
encrypted captures ─► decrypt (AES-GCM, tenant-bound) ─► decode
    ─► document detection (Phase 2 quadrilateral)
    ─► perspective correction (homography to ID-1 rectangle, 1600 px)
    ─► orientation (portrait quads rotated to landscape)
    ─► normalization (grayscale, autocontrast, unsharp mask; content never edited)
    ─► Khmer + Latin OCR (Tesseract 5, khm+eng, line/word boxes)
    ─► constrained numeric re-read (script/Khmer, Khmer digits only)
    ─► classification (per side) ─► label-anchored field extraction
    ─► normalization ─► validation ─► encrypted persistence
```

| Outcome | When | Effect |
| --- | --- | --- |
| `ACCEPTED` | Recognized KH ID; critical fields read | Fields encrypted, checks stored, `DOCUMENT_ACCEPTED` → `SELFIE_REQUIRED` (or `PROCESSING` for `DOCUMENT_ONLY`) |
| `RECAPTURE` | `DOCUMENT_TYPE_MISMATCH`, `DOCUMENT_NOT_RECOGNIZED`, `BACK_SIDE_EXPECTED`, `CRITICAL_FIELD_UNREADABLE` | Captures deleted (ciphertext swept), `RECAPTURE_REQUIRED` → `DOCUMENT_REQUIRED` |
| `NO_ADAPTER` | Document type has no adapter yet (e.g. `PASSPORT` until Phase 5/6) | Session waits in `DOCUMENT_PROCESSING`; audited once |
| `UNAVAILABLE` / `ERROR` | Keys or OCR missing, OCR timeout | Session waits; `scripts/process_documents.py` retries |

Processing runs in a background task after the upload transaction commits
(`DOCUMENT_PROCESSING_MODE=inline`), or only through the worker script (`deferred`).
The session row is locked `FOR UPDATE` while it is processed, so it is never
processed twice at once.

### The adapter (`src/kyc/documents/adapters/kh_national_id.py`)

* **Label-anchored extraction.** Values are found after the printed Khmer labels
  (គោត្តនាម និងនាម, ថ្ងៃខែឆ្នាំកំណើត, ភេទ, ទីកន្លែងកំណើត, អាសយដ្ឋាន, សុពលភាព), not at
  fixed pixel positions. Labels are fuzzy-matched (similarity ≥ 0.78) to survive OCR
  noise. A fuzzy match is recorded as a `FUZZY_LABEL` flag.
* **Fields.** `document_number`, `full_name_local`, `full_name` (the Latin line below
  the Khmer name), `date_of_birth`, `sex`, `place_of_birth`, `address` (may wrap),
  `issue_date`, `expiry_date`, raw back-side `mrz` (parsed in Phase 5), and
  `nationality` (DERIVED from the document type, marked as such). Fields the card
  does not carry (e.g. `issuing_authority`) stay `null`.
* **Every field keeps** its raw value, normalized value, confidence, bounding box,
  side, source and flags (spec §7).
* **Classification.** Header cues, label hits, a 9-digit number and a Latin name
  line mark the front. An `IDKHM` MRZ line marks the back. Passport cues mean a
  different document. Sides uploaded the wrong way round are detected and swapped
  (`SIDES_SWAPPED` note), so no recapture is needed.
* **Validation checks.** `REQUIRED_FIELDS`, `DOCUMENT_NUMBER_FORMAT`,
  `DATE_CONSISTENCY` (impossible birth date, issued before birth, expiry ≤ issue,
  unusual validity period), `EXPIRY`, `OCR_CONFIDENCE` (< 0.80 → REVIEW) and
  `SCRIPT_CONSISTENCY`. `MRZ`, `BARCODE` and `PORTRAIT` report **UNAVAILABLE**
  until Phases 5, 7 and 8; they never report a fake PASS.
* **Policy** `KH-NID-ADAPTER-2026.10.1`, `calibrated = False`.

### "Never silently replace low-confidence OCR"

* Khmer digits are converted literally. A value that mixes Khmer and Latin digits is
  flagged `MIXED_DIGIT_SCRIPTS` and its confidence is cut. Nothing is guessed.
* Tesseract confuses Khmer digits with Latin look-alikes (១/9, ០/0). The adapter
  declares that the card prints Khmer numerals, so numeric words get a **second
  reading of the same pixels** restricted to that alphabet. The original reading is
  replaced only when the second read keeps the same structure and is usable. Every
  replacement carries `OCR_PASSES_DISAGREED`, which lowers confidence. A
  zero-confidence constrained read must be confirmed by a third read in a different
  segmentation mode (`CONSTRAINED_PASSES_AGREED`). Otherwise the re-read is
  rejected and noted.
* The result reports `review_flags` (e.g. `LOW_OCR_CONFIDENCE`). `decision` stays
  `null`: the risk engine (Phase 13) decides, not the extractor.

### Measured on the synthetic specimen (real Tesseract 5.5.3)

| Field | Read | Notes |
| --- | --- | --- |
| Khmer name, Latin name, sex, place of birth, address | correct | — |
| Document number | correct after constrained re-read | first pass `090203040` |
| Date of birth | correct after constrained re-read | first pass `9990-03-15` |
| Expiry date | correct after constrained re-read | first pass unparseable |
| Issue date | **flagged** (`MIXED_DIGIT_SCRIPTS`, confidence 0.44) | the two constrained reads disagreed |

## 2. Directory tree (Phase 3 additions +, changes ~)

```text
src/kyc/
├── core/crypto.py                    + keyrings, FieldCipher (AES-GCM fields, HMAC lookup)
├── core/config.py                    ~ PII keys, Tesseract settings, processing mode
├── documents/khmer.py                + Khmer/Latin normalization (digits, dates, sex, names)
├── documents/preprocess.py           + perspective / orientation / normalization
├── documents/refine.py               + constrained numeric re-read
├── documents/adapters/__init__.py    + adapter registry
├── documents/adapters/kh_national_id.py + CambodiaNationalIDAdapter
├── ocr/tesseract.py                  + Tesseract engine (stdin/stdout, timeout, TSV → lines/words)
├── services/documents.py             + DocumentProcessor orchestration
├── services/results.py               + masked client result
├── domain/identity.py                ~ OCRLine/OCRWord, field provenance and flags
├── engines/contracts.py              ~ concrete OCREngine / DocumentAdapter contracts
├── api/routes.py, api/schemas.py     ~ background processing, result document/identity
└── main.py                           ~ cipher and processor wiring
migrations/versions/0003_phase3.py     +
scripts/process_documents.py           + worker / retry
tests/test_kh_national_id.py           + normalization, adapter, refinement
tests/test_documents_api.py            + service/API flows; real-OCR end-to-end
tests/images.py                        ~ fictional SPECIMEN card renderer (Khmer fonts via raqm)
requests/phase3.postman.json, artifacts/openapi-phase3.json, phase3-postgresql.sql, phase3-tests.txt  +
```

## 3. Migration `0003_phase3`

* `document_fields`: add `side`, `source` (check: OCR/DERIVED/MRZ/BARCODE/NFC), `flags` (JSON),
  and unique `(organization_id, session_id, document_id, field_name)`.
* `identity_documents`: add `side_classification` (JSON), `extraction_version`, `processed_at`.
* Server defaults stay on the new NOT NULL columns, so direct SQL writers get safe values.
  RLS from 0001 already covers both tables.

## 4. Security concerns

* **PII at rest.** Raw and normalized values are AES-256-GCM sealed with a dedicated
  `PII_ENCRYPTION_KEYS` keyring, kept separate from capture keys (spec §23). The
  associated data binds each value to its organization, session, document, field and
  raw/normalized slot.
* **Duplicate detection without plaintext.** `document_number_hmac` is HMAC-SHA256 with
  `PII_HMAC_KEY` and a document-type namespace, never a bare hash of a 9-digit space.
* **No PII in logs.** Audit entries and check evidence hold reason codes, field names,
  confidences and versions, never OCR text or values. A test asserts this.
* **Stale evidence.** Every recapture deletes the earlier captures. This closes the
  Phase 2 gap where old sides could satisfy a new attempt.
* **OCR subprocess.** No shell, fixed arguments, a timeout, and images piped through
  stdin (no temp files).
* **The result endpoint returns identity fields** to the development credential.
  Permission-scoped masking per client is Phase 15.
* **Layout assumptions.** The label set, the 9-digit number, Khmer numerals and the
  `IDKHM` back marker must be confirmed against official specimens and consented
  samples, and the thresholds calibrated, before production.
* **LLMs** are not used anywhere in this path.
