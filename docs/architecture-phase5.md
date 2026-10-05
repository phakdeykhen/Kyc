# Phase 5 — Passport and MRZ engine

Status: implemented and validated on 5 October 2026. Phase 6
(international passport visual-zone extraction) has not started.

## 1. Design

Phase 5 adds a Cambodia passport data-page adapter and a reusable MRZ engine. It also
connects the existing Cambodia national ID adapter's back-side MRZ to that engine.
Clients continue to use the capture endpoints and session lifecycle from earlier
phases. Extraction moves a readable document to `SELFIE_REQUIRED`; it never issues a
verification decision.

```text
accepted encrypted capture
  → document rectification
  → visual-zone OCR + dedicated MRZ-band OCR
  → TD1 / TD2 / TD3 parser and check digits
  → adapter classification, fields and visual/MRZ consistency
  → encrypted document_fields + non-PII mrz_results/check metadata
  → SELFIE_REQUIRED or DOCUMENT_REQUIRED for recapture
```

The format definitions come from ICAO Doc 9303: TD1 uses three 30-character lines,
TD2 two 36-character lines, and TD3 two 44-character lines. The parser implements
these three layouts; the passport adapters expect TD3. See [ICAO Part 5 (TD1)](https://www.icao.int/sites/default/files/publications/DocSeries/9303_p5_cons_en.pdf),
[Part 6 (TD2)](https://www.icao.int/sites/default/files/publications/DocSeries/9303_p6_cons_en.pdf),
and [Part 4 (TD3/passports)](https://www.icao.int/sites/default/files/publications/DocSeries/9303_p4_cons_en.pdf).

### MRZ reading and validation

The reader crops an adapter-defined MRZ band after perspective correction. It runs
English OCR with an MRZ alphabet, an unconstrained pass, an enlarged pass and a
column-segmentation pass. The parser selects a candidate block by check-digit
validation, with penalties for padding adjustments, filler noise and numeric
substitutions. The national ID reads the lower back; passports read the lower data
page. Region assumptions are versioned adapter settings, not a calibrated detector.

The parser normalizes the permitted alphabet and common filler glyphs, detects the
format, extracts fields, and checks document number, birth date, expiry date and the
composite digit. TD3 additionally checks optional personal data. Check digits use
the repeating 7, 3, 1 weights from [ICAO Part 3, section 4.9](https://www.icao.int/sites/default/files/publications/DocSeries/9303_p3_cons_en.pdf).
Syntax and feasible dates are validated separately from checksum arithmetic. TD1/TD2
overflow document numbers are supported. An incomplete birth date remains flagged
and supplies no invented exact date.

OCR character substitutions are restricted to numeric positions: dates and check
digits. An alphanumeric document number is retained as read. Restoring trailing
filler, removing text after a name's trailing filler run, and numeric substitutions
are flagged. No mismatching visual value is overwritten to make it match the MRZ.

`MRZ` reports internal check-digit consistency and expected adapter format/document
code/issuing state. `MRZ_CONSISTENCY` compares visual number, name, birth date, sex,
expiry and available nationality against the MRZ. Per-field metadata contains
`MATCH`, `MISMATCH`, `VIZ_ONLY` or `MRZ_ONLY`, and disagreements produce review flags.
An MRZ-only generic passport reports `mrz_consistency: NOT_APPLICABLE` because no
independent visual fields were extracted.

### Adapters and current scope

| Document type | Required capture | Implemented behavior |
| --- | --- | --- |
| `KH_PASSPORT` | `DATA_PAGE` | Khmer/English labels, visual fields and TD3 MRZ; expects passport code `P` and issuing state `KHM` |
| `PASSPORT` | `DATA_PAGE` | `GenericMRZAdapter`: reads TD3 passport MRZ only, without a country's visual layout |
| `KH_NATIONAL_ID` | `FRONT`, `BACK` | Existing Khmer fields plus TD1 MRZ reading/validation on the back |
| `KH_NSSF` | `FRONT`, `BACK` | Existing card behavior; MRZ stays `NOT_APPLICABLE` |

The Cambodia passport reads passport number, surname, given names, printed
nationality, birth date, sex, place of birth, issue date and expiry date. Its canonical
Latin full name is given names followed by surname. The visual zone remains the
canonical source when it has a normalized value. MRZ fills missing number/date fields
only when their own check digit validates; names, sex and other unchecked fields
require the whole MRZ to validate. Cambodia's canonical nationality remains the
existing `KH` derivation, with printed nationality retained as an encrypted field
and compared with MRZ nationality through recognized Cambodia aliases. Names,
nationality and sex are marked `NOT_CHECK_DIGIT_PROTECTED`; whole-block validity
does not give those fields an individual checksum.

The generic adapter supplies number, names, birth date, sex, expiry, nationality and
issuing-state fields from the MRZ. It does not extract issue date, place of birth,
address or local-script names. MRZ nationality retains its three-character code;
the public document country remains nullable. The session's `country` is supplied
by the client and is not an issuer authenticity check.
`GET /v1/countries` lists `KH` in `verification_adapters_available` and `PASSPORT`
in `any_country_document_types`. Listing ISO country codes does not claim a visual
adapter for each country.

A missing, unreadable, invalid or non-passport MRZ in a generic passport session
returns to recapture when classification or required fields fail. A Cambodia passport
may remain readable from its visual zone while an absent/invalid MRZ or disagreement
is reported as `REVIEW`. Neither case determines authenticity. Phase 6 will add
international passport visual-zone extraction; the Phase 5 `GenericMRZAdapter` is
the shared MRZ-only fallback required by the architecture.

## 2. Files and directory tree

```text
src/kyc/mrz/
├── __init__.py
├── parser.py                         TD1/TD2/TD3 fields, digits and comparison
└── reader.py                         dedicated MRZ OCR passes
src/kyc/documents/adapters/
├── kh_passport.py                    Cambodia passport layout
├── generic_mrz.py                    MRZ-only passport adapter
├── kh_national_id.py                 TD1 back-band settings
├── khmer_label.py                    MRZ integration and missing-field fill
└── __init__.py                       adapter registry
src/kyc/services/
├── documents.py                     encrypted fields and MRZ metadata persistence
└── results.py                       public MRZ metadata and consistency checks
src/kyc/api/{routes,schemas}.py      capability inventory, typed non-PII MRZ result
scripts/bootstrap_local.py           restricted-role MRZ grants
tests/{mrz_build,test_mrz}.py         synthetic MRZ builders and parser/adapter checks
tests/{images,test_documents_api}.py  synthetic passport pages and API/OCR flows
requests/phase5.postman.json          DATA_PAGE workflows
docs/architecture-phase5.md          this design
artifacts/                           generated Phase 5 API/test evidence
```

`README.md`, `pyproject.toml`, `BUILD_PROGRESS.md`, and the appended progress tracker
in `Document.md` describe the new phase. No runtime dependencies were added.

## 3. Migration, environment and Docker

No migration is needed. Phase 1 already created tenant-scoped `mrz_results`, and
Phase 3 already permits `document_fields.source = 'MRZ'`. Alembic remains at
`0003_phase3`. `mrz_results` stores format, validity, digit outcomes and field
consistency only. Raw/normalized MRZ text and extracted identity values use encrypted
`document_fields`, with AES-256-GCM and context binding to tenant/session/document/field.

Bootstrap now grants `SELECT`, `INSERT` and `DELETE` on `mrz_results` to `kyc_app`.
Existing installations must rerun bootstrap to apply those grants:

```sh
PYTHONPATH=src .venv/bin/python scripts/bootstrap_local.py
# Docker equivalent:
docker compose run --rm migrate
```

Running Alembic alone does not refresh role grants.

The environment variables, dependency lock, Dockerfile and Compose configuration
remain unchanged. Keep both capture and PII keyrings configured. Tesseract's English
model reads MRZ; Khmer/English models support the existing cards and Cambodia visual
zone. The `inline` and `deferred` processing modes continue to work through the same
document service; use `scripts/process_documents.py` for deferred processing.

## 4. API workflow and validation

The [README passport curl workflow](../README.md#passport-data-page-workflow)
and [Phase 5 Postman collection](../requests/phase5.postman.json) create
`KH_PASSPORT` or `PASSPORT` sessions and upload `side=DATA_PAGE`. Set only private
credential variables and select a local data-page image in Postman. The collection
has separate sequential workflows so one session cannot overwrite the other.

The capture response reports the quality gate and handoff to `DOCUMENT_PROCESSING`.
Poll the session/result after processing: readable fields progress to
`SELFIE_REQUIRED`, with a masked alphanumeric document number, `checks.mrz`,
`checks.mrz_consistency`, review flags and `decision: null`. The nullable `mrz`
object contains `format`, `mrz_valid`, per-field check digits (`expected`, `computed`,
`valid`) and field consistency. It contains no raw MRZ lines or parsed identity
values. A `RECAPTURE` capture needs a
new photograph; a document recapture resets the session to `DOCUMENT_REQUIRED`.

The full suite passed: **175 tests, 0 skipped, 0 failures** with live PostgreSQL
([artifacts/phase5-tests.txt](../artifacts/phase5-tests.txt)). Live PostgreSQL tenant RLS validation, including
MRZ results, passed. The [restricted-role PostgreSQL/OCR run](../artifacts/phase5-postgres-e2e.json)
also passed for both passport adapters: each reached `SELFIE_REQUIRED` with valid
MRZ and correct masked numbers, Cambodia consistency was `PASS`, and generic
consistency was `NOT_APPLICABLE`. Cambodia's low OCR confidence remained a review
flag. The run checked encrypted MRZ fields, absence of PII values in audit/check
metadata and rejection of a foreign-organization request. Automated coverage
includes ICAO example blocks, synthetic TD1/TD2/TD3 blocks, corrupted digits, numeric
OCR confusions, visual/MRZ disagreements, passport adapter selection, encrypted MRZ
persistence and API recapture. Real OCR uses fictional `SPECIMEN` data pages only.
Its fixture renderer depends on Tesseract `khm`/`eng`, Pillow RAQM and macOS Khmer
Sangam MN, Arial and Courier New Bold fonts. The real-OCR suite may skip when its
declared prerequisites are absent; fixture fonts are not installed by Docker.

## 5. Security concerns and limits

- A valid MRZ can be copied or fabricated. Check digits and visual agreement prove
  consistency of the reading, not government issuance, chip authenticity or identity.
  No risk decision, face verification, NFC or barcode engine is added here.
- Captures and identity/MRZ values are encrypted; checks and audit events exclude raw
  text, names, numbers and dates. Metadata is still tenant-scoped sensitive operational
  data. The authenticated result intentionally returns permitted identity fields.
- Production remains gated. Development credentials and `.env` keys need the
  production authentication, consent, retention and key-management controls from
  later phases. Capture retention currently deletes images; it does not establish a
  lifecycle for extracted fields or database backups.
- Cambodian labels, the 7–9-character alphanumeric passport-number rule and crop
  geometry are assumptions awaiting official specimens and consented samples. OCR
  thresholds and assigned confidence scores are uncalibrated. Synthetic OCR success
  is not measured field performance on real passports.
- MRZ date centuries use heuristics: birth chooses the most recent matching date
  no later than today; expiry uses a moving 100-year window. People over 100 years
  old and unusually distant expiry dates need visual evidence. Incomplete birth
  dates do not supply exact canonical dates. National deviations and every
  transliteration edge case are not covered; unchecked names and nationality still
  need corroborating evidence.
- Multiple OCR passes increase processing work. OCR subprocess timeouts bound each
  invocation; production throughput and hostile-image testing remain later work.

Work stops after Phase 5. Phase 6 requires separate approval under `Document.md`
section 31.
