# Phase 12 — Cross-check and fraud signals

Status: implemented on 5 October 2026.

## 1. Design (spec §17, §18)

When a session reaches `PROCESSING`, a post-commit background task compares every
independent source with every other and runs a pipeline of fraud detectors. The result
is **signals with a severity**, never a verdict. The session status does not change,
and `decision` stays null until the Phase 13 risk engine.

```
reaches PROCESSING (document-only: after extraction; face levels: selfie, liveness or NFC)
  → FraudAnalyzer.analyze()            own transaction, session row locked, tenant set
      gather facts from stored evidence (values decrypted in memory only)
      → cross-check matrix             VISUAL · MRZ · BARCODE · NFC, field by field
      → detectors                      consistency · validity · tamper · duplicates · metadata · portrait · context
  → fraud_signals (replaced on every run)
  → document_checks CROSS_CHECK + FRAUD_ANALYSIS (summary, coverage)
  → audit FRAUD_ANALYZED (signal codes only)
```

The deferred worker (`scripts/process_documents.py`) also analyzes `PROCESSING`
sessions that have no analysis yet.

### Cross-check engine (`kyc/fraud/crosscheck.py`)

Pairwise comparison **states** (MATCH / MISMATCH) are collected per field:

| Pair | Comes from |
| --- | --- |
| MRZ ~ VISUAL | `mrz_results.field_consistency` (Phase 5) |
| BARCODE ~ VISUAL | `barcode_results.data_consistency` (Phase 7) |
| MRZ ~ BARCODE | **new:** MRZ and barcode payload decrypted in memory and compared |
| NFC ~ VISUAL, NFC ~ MRZ | `nfc_results.document_consistency` (Phase 11) |

Each field becomes `CONSISTENT` or `CONFLICT`. The engine never chooses a value. If one
source disagrees with two or more sources that agree with each other, it is named as
the **outlier** (`VISUAL_OUTLIER_DOB`, `NFC_OUTLIER_DOCUMENT_NUMBER`). That is evidence
for a reviewer about which source looks wrong, not a correction.

The spec's example `OCR DOB 1999-01-01` vs `MRZ DOB 1999-01-07` produces
`MRZ_VISUAL_DOB_MISMATCH` with severity HIGH, and `cross_check = REVIEW`.

**Severity:** a mismatch is HIGH when one side is protected (verified chip, valid
barcode signature, or a check-digit-valid MRZ field) and the field is identifying
(number, DOB, expiry, MRZ data). Names, sex and nationality, and comparisons between
two unprotected sources, are MEDIUM.

### Detectors (`kyc/fraud/engine.py`)

Detectors are pure functions of a `FraudContext`, so a new one is a function added to
`DETECTORS`. None of them reads the database or sees identity values.

| Detector | Signals |
| --- | --- |
| consistency | `{A}_{B}_{FIELD}_MISMATCH`, `{SOURCE}_OUTLIER_{FIELD}` |
| validity | `EXPIRED_DOCUMENT`, `IMPOSSIBLE_DATES_*`, `MRZ_CHECK_DIGIT_FAILED`, `MRZ_IMPOSSIBLE_DATE`, `UNUSUAL_VALIDITY_PERIOD` |
| tamper | `BARCODE_SIGNATURE_INVALID`, `NFC_DATA_GROUP_ALTERED`, `NFC_SECURITY_OBJECT_INVALID`, `NFC_CHIP_CLONE_SUSPECTED` (all HIGH/TAMPER); `NFC_ISSUER_NOT_TRUSTED`, `NFC_NOT_COMPLETED` (LOW) |
| duplicates | `DOCUMENT_USED_BY_ANOTHER_USER` (HIGH), `DOCUMENT_CAPTURE_REUSED` / `SELFIE_CAPTURE_REUSED` (identical file bytes, HIGH), `DOCUMENT_VELOCITY` (MEDIUM), `DOCUMENT_PREVIOUSLY_USED` (LOW) |
| metadata | `EDITING_SOFTWARE_IN_METADATA`, `SCREENSHOT_METADATA` (MEDIUM), `OLD_CAPTURE_TIMESTAMP` (LOW) |
| portrait | `PORTRAIT_DIFFERS_FROM_CHIP`: printed portrait vs verified chip portrait; MEDIUM while uncalibrated, HIGH when calibrated |
| context | `ISSUING_STATE_DIFFERS_FROM_SESSION_COUNTRY` (LOW) |

Duplicates use the keyed `document_number_hmac` (never a plain number) and file SHA-256
values. They are scoped to the organization by RLS and limited to data still inside
retention. The metadata detector reads the encrypted original upload and extracts only
an editor name, a screenshot marker and the capture date. GPS and device tags are never
read. It works in one direction only: the web capture client strips metadata, so a
file without metadata proves nothing.

### Summary values

- `checks.cross_check`: `PASS` (all consistent), `REVIEW` (any conflict), or `NOT_APPLICABLE`.
- `checks.fraud`: `FAIL` if any HIGH TAMPER signal (cryptographic proof); `REVIEW` if any
  MEDIUM or HIGH signal; otherwise `PASS`.
- `fraud_signals` in the result carries `signal`, `severity` and `category` only. Scores,
  thresholds and other details stay server-side.

A severity is not a decision: a HIGH mismatch means REVIEW (spec §18). Turning signals
into PASS/REVIEW/FAIL for the session is Phase 13.

### Coverage is reported, not implied

`FRAUD_ANALYSIS.coverage` lists every spec §17 signal as SUPPORTED, PARTIAL or
NOT_SUPPORTED. Font, layout, text-region and pixel-manipulation forensics are
NOT_SUPPORTED. Their absence from the signals must not be read as a pass, and the
Phase 13 policy has to take coverage into account.

### Privacy

Signals, cross-check evidence and audit entries hold codes, field **names**, source
names and counts. They never hold identity values. Decrypted MRZ and barcode values
live only in memory during the comparison.

## 2. Files

```
src/kyc/fraud/signals.py          severities, categories, coverage map, engine version
src/kyc/fraud/crosscheck.py       pairwise matrix, outliers, consistency signals
src/kyc/fraud/engine.py           FraudContext, detectors, summary rules
src/kyc/fraud/metadata.py         editor / screenshot / capture-date markers from uploads
src/kyc/services/fraud.py         fact gathering, persistence, worker query
src/kyc/api/routes.py             ~ post-commit analysis after document, selfie, liveness, NFC
src/kyc/services/results.py       ~ cross_check, fraud and fraud_signals in the result
src/kyc/db/models.py              ~ fraud_signals.category; sha256 lookup indexes
migrations/versions/0007_phase12.py   + category column/constraint, two indexes
scripts/process_documents.py      ~ also analyzes unanalyzed PROCESSING sessions
tests/test_fraud.py               + 18 tests: matrix, detectors, metadata, API, duplicates, trigger
```

Configuration: `FRAUD_DUPLICATE_WINDOW_HOURS` (24) and `FRAUD_VELOCITY_LIMIT` (3).
Bootstrap grants `kyc_app` SELECT/INSERT/DELETE on `fraud_signals`.

## 3. Validation (5 October 2026)

- **318 tests: 318 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6 (including
  RLS on `fraud_signals`) and native face models
  ([artifacts/phase12-tests.txt](../artifacts/phase12-tests.txt)).
- Live HTTP as `kyc_app`, with real OCR and models
  ([artifacts/phase12-live-e2e.json](../artifacts/phase12-live-e2e.json)):

| Case | cross_check | fraud | Signals |
| --- | --- | --- | --- |
| Genuine passport and chip, first use | PASS | PASS | none |
| Same passport, another user | PASS | REVIEW | `DOCUMENT_USED_BY_ANOTHER_USER` |
| Byte-identical file replayed, 3rd session in 24 h | PASS | REVIEW | `DOCUMENT_CAPTURE_REUSED`, `DOCUMENT_VELOCITY`, … |
| Photoshop-tagged upload | PASS | REVIEW | `EDITING_SOFTWARE_IN_METADATA`, `DOCUMENT_PREVIOUSLY_USED`, … |
| Genuine chip of another passport | REVIEW | REVIEW | `NFC_VISUAL_*_MISMATCH`, `NFC_MRZ_DATA_MISMATCH`, `NFC_OUTLIER_*`, `PORTRAIT_DIFFERS_FROM_CHIP` |
| Cloned chip | PASS | FAIL | `NFC_CHIP_CLONE_SUSPECTED` |
| Document-only session (analysis from the OCR path) | PASS | REVIEW | duplicate signals from earlier cases |

7 analyses, 0 failures. No identity values were stored, and `kyc_app` saw 0 rows
without tenant context. `decision` stayed null throughout.

## 4. Limits

- No forensic models for fonts, layout, text regions, pixel manipulation or print/screen
  (moiré) reproduction. They are listed as NOT_SUPPORTED in coverage.
- Duplicate checks cover one organization and its retention window only. Cross-tenant
  matching would break tenant isolation and needs a separate design and review.
- 1:N face search is not used (spec §16). Duplicate faces across sessions are not detected.
- The portrait comparison uses the uncalibrated face policy, so it is at most MEDIUM.
- Metadata is attacker-controlled. Its presence is a signal; its absence is not evidence.
- The signal set and severities are policy choices that need review against real fraud
  cases before production.
