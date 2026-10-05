# Phase 4 — Cambodia NSSF adapter

Status: implemented on 5 October 2026. Phase 5 (Passport + MRZ engine) has not started.

## 1. Design

The NSSF member card (National Social Security Fund, បេឡាជាតិរបបសន្តិសុខសង្គម) uses
the same Khmer label-and-value style as the national ID. Phase 4 therefore made the
Phase 3 extraction engine **reusable** instead of copying it:

```
documents/adapters/khmer_label.py   KhmerLabelAdapter  ← shared engine
        │  fuzzy labels · overlap-safe label marks · value/next-line/wrapped extraction
        │  number rules · rival-card detection · validation · provenance and flags
        ├── kh_national_id.py   CardLayout(KH_NATIONAL_ID)   (Phase 3, now a layout)
        └── kh_nssf.py          CardLayout(KH_NSSF)          (Phase 4)
```

A `CardLayout` declares the labels, the text fields and their normalizers, number
rules (labelled or unlabelled, pattern and format), date handling (one combined
validity label, or separate issue/expiry labels), the back-side marker, critical and
important fields, and whether an expiry is printed. Further Khmer card types are new
layouts, not new code.

### NSSF layout

| Field | Source | Notes |
| --- | --- | --- |
| `document_number` | after `លេខសមាជិក` / `លេខ ប.ស.ស` | member number, 6–12 digits (format unconfirmed) |
| `national_id_number` | after `លេខអត្តសញ្ញាណប័ណ្ណ` | linked national ID, 9 digits; stored for Phase 12 cross-checks |
| `full_name_local`, `full_name` | `គោត្តនាម និងនាម` / Latin line below | |
| `sex`, `date_of_birth` | `ភេទ`, `ថ្ងៃខែឆ្នាំកំណើត` | |
| `employer` | `ឈ្មោះសហគ្រាស` / `សហគ្រាស` | may wrap one line |
| `issue_date` | `ថ្ងៃចេញប័ណ្ណ` / `ថ្ងៃចេញ` | |
| `expiry_date` | `ផុតកំណត់` if printed | none printed → `EXPIRY: NOT_APPLICABLE` |

* Critical fields: member number and Khmer name. Missing either means recapture.
* No MRZ, so `MRZ: NOT_APPLICABLE`. A back QR, if present, is Phase 7 (`BARCODE: UNAVAILABLE`).
* Policy `KH-NSSF-ADAPTER-2026.10.1`, `calibrated = False`.
* The NSSF card is evidence of social-security registration. How much weight it carries
  as identity is a risk-policy decision (Phase 13).

### Engine improvements made for Phase 4 (they apply to both cards)

* **Rival-card detection.** Each adapter checks the distinctive titles of the other card
  types. An NSSF card in an ID session, or an ID card in an NSSF session, is
  `DOCUMENT_TYPE_MISMATCH` and goes back to recapture. Shared words are handled: the
  NSSF label `លេខអត្តសញ្ញាណប័ណ្ណ` contains the ID title, so the card type with more
  title hits wins.
* **Overlap-safe labels.** A short label inside a longer one (`ឈ្មោះ` "name" inside
  `ឈ្មោះសហគ្រាស` "enterprise name") is assigned to the longer label.
* **Fronts need evidence.** Headings alone (often printed on backs too) no longer
  classify a side as the front. A front needs field labels or a number.
* **Adapter-owned capture sides.** `requirement_for()` now uses the adapter's
  `required_sides()` when an adapter exists, as Phase 2 planned.
* **Secondary sides.** Classification confidence comes from the primary side. A back
  without cues is noted (`BACK_SIDE_UNVERIFIED`), not penalized, and still recaptures
  if it is really a second front.
* The result reports `expiry_status: NOT_APPLICABLE` for cards without an expiry.

### Measured on the synthetic NSSF specimen (real Tesseract 5.5.3)

| Field | Read |
| --- | --- |
| member number, linked ID number, Khmer name, Latin name, sex, birth date, employer | correct |
| issue date | **flagged** (`MIXED_DIGIT_SCRIPTS`); not guessed |

## 2. Files

```text
src/kyc/documents/adapters/khmer_label.py    + shared Khmer label engine (from Phase 3 code)
src/kyc/documents/adapters/kh_national_id.py ~ now a CardLayout on the shared engine
src/kyc/documents/adapters/kh_nssf.py        + NSSF layout
src/kyc/documents/adapters/__init__.py       ~ registry adds KH_NSSF
src/kyc/documents/requirements.py            ~ adapter-owned required sides
src/kyc/services/documents.py                ~ primary-side classification confidence
src/kyc/services/results.py, api/schemas.py  ~ NOT_APPLICABLE expiry status
tests/images.py                              ~ fictional NSSF SPECIMEN front/back renderer
tests/test_kh_national_id.py                 ~ NSSF adapter tests
tests/test_documents_api.py                  ~ NSSF flows, cross-card mismatch, real-OCR NSSF E2E
tests/test_api.py                            ~ hermetic test settings (ignore ambient env keys)
requests/phase4.postman.json, artifacts/openapi-phase4.json, phase4-tests.txt  +
```

## 3. Migration

None. Phase 4 needs no schema change. Extra NSSF fields (`national_id_number`,
`employer`) use the existing encrypted `document_fields` rows. The schema revision stays
`0003_phase3`.

## 4. Security concerns

* Same protections as Phase 3: AES-GCM fields under the PII keyring, HMAC number lookup
  namespaced per document type (`KH_NSSF`), no PII in audit or check records.
* `national_id_number` links two identities. It is encrypted like every field, and the
  cross-document comparison is deliberately left to Phase 12 under its own controls.
* **Layout assumptions** (labels, member-number format, absence of expiry, the back
  content) are unconfirmed and must be checked against official NSSF specimens and
  consented samples before production.
* A test-isolation gap was found and fixed: test settings no longer inherit keys from the
  developer's environment.
