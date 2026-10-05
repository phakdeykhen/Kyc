# Build progress — Universal Identity Platform

Updated: 5 October 2026. The full requirements in `Document.md` control the build.
Phases 2–5 were approved and are implemented. Work is paused at the Phase 5
approval gate; Phase 6 has not started.

## Phase 6 implementation (5 October 2026)

Implemented international document adapters. `GenericPassportAdapter` reads the printed
data page of any country's passport (common English/French/Spanish labels, ICAO portrait
area excluded, English OCR model) and cross-checks it with the TD3 MRZ; unfamiliar label
languages fall back to check-digit-valid MRZ fields instead of guessing. `GenericIDAdapter`
and `GenericResidenceCardAdapter` read a TD1/TD2 MRZ (normally on the back) plus labelled
front fields; a front in an unreadable script is carried by a valid back MRZ at REVIEW
classification confidence. A new `ISSUING_COUNTRY` check compares the MRZ issuing state
(ISO 3166 alpha-3 / ICAO codes) with the session country; a check-digit-valid MRZ also
corrects the stored issuing country. Printed nationality is compared only when it is a
code (a demonym such as "UTOPIAN" is reported `NOT_COMPARED`, except on Cambodian
documents, where any non-Cambodian wording remains a disagreement). Design:
[architecture-phase6.md](docs/architecture-phase6.md).

### Phase 6 validation evidence

- Full suite green after the change (see the Phase 7 entry for the final count).
- Real OCR on photographed synthetic SPECIMENs: the foreign passport's printed page and
  MRZ agree on number, birth date, sex, expiry and name; the foreign ID card (front labels
  + back TD1) agrees on all six fields, nationality included. Both reach `SELFIE_REQUIRED`.
- Found and fixed: a shorter label variant ("Given name") could win inside a longer one
  ("Given names"); demonyms were compared with alpha-3 codes and always "mismatched".

### Phase 6 limits

Label vocabularies cover English/French/Spanish only; other languages rely on the MRZ.
Cards without an MRZ and without legible labels are sent back. Driving licences have no
adapter. Accuracy on real foreign documents is unmeasured.

## Phase 5 implementation (5 October 2026)

Implemented the passport and MRZ engine. `kyc/mrz/parser.py` parses ICAO 9303 TD1,
TD2 and TD3 blocks, validates field syntax, feasible dates, document number, birth
date, expiry, optional data and composite check digits, and compares the MRZ field
by field with the visual zone. It supports TD1/TD2 extended numbers and incomplete
birth dates without inventing an exact date.
`kyc/mrz/reader.py` reads the MRZ band four ways (ICAO alphabet, unconstrained,
enlarged and column segmentation). The assembler keeps the candidate block whose
check digits validate, so lines from different OCR passes can be combined.
OCR look-alikes are substituted only in numeric positions, restored filler and
filler noise in names are flagged, and visual values are never overwritten to match
the MRZ. New adapters: `CambodiaPassportAdapter` (bilingual visual zone + TD3; the
visual zone is read without the ICAO portrait area) and `GenericMRZAdapter` for
`PASSPORT` sessions from any country. The national ID's back MRZ (TD1) now uses the
same engine. No migration was needed (`mrz_results` existed since Phase 1); bootstrap
grants were added. The masked result exposes typed MRZ validity, digit results and
field consistency while raw MRZ stays encrypted. Adapters hold no per-session MRZ
state, and fallback fields keep their source boxes and original visual evidence.
Design: [architecture-phase5.md](docs/architecture-phase5.md).

### Phase 5 validation evidence

- **175 tests: 175 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6,
  run with stray environment keys to confirm test isolation
  ([artifacts/phase5-tests.txt](artifacts/phase5-tests.txt)).
- The parser validates the published ICAO 9303 specimens in all three formats and
  catches a single corrupted digit.
- **Real OCR (Tesseract 5.5.3) on photographed synthetic SPECIMEN pages**: the
  Cambodian passport, a foreign (ICAO Utopia) passport and the national ID back all
  produced check-digit-valid MRZs. Synthetic OCR fixtures do not establish real
  passport accuracy.
- **Live PostgreSQL through the API** as restricted `kyc_app` after bootstrap:
  Cambodian and foreign passports reached `SELFIE_REQUIRED` with `mrz: PASS` and
  correct masked numbers. Two TD3 MRZ rows persisted. Cambodia's visual consistency
  passed while low OCR confidence remained flagged. Generic visual consistency was
  `NOT_APPLICABLE`. Identity values were absent from checks/audit, raw MRZ fields
  were encrypted, and a foreign organization was rejected
  ([artifacts/phase5-postgres-e2e.json](artifacts/phase5-postgres-e2e.json)).
- **Live PostgreSQL tenant isolation** includes MRZ visibility, default-deny access
  and cross-tenant insert rejection. The test now isolates its migration version
  table from an already migrated deployment's public schema.
- Problems found and fixed during validation: Khmer-digit re-reading damaged
  passports' Latin digits (now per layout); the portrait was read as text (visual-zone
  region); a fuzzy label beat an exact one; title words were taken as passport numbers;
  TD1 lines could be mis-padded into TD3; MRZ filler misreads polluted names;
  adapter state could mix sessions; unchecked fields could borrow the wrong
  document's MRZ; checksum success could conceal structurally invalid data.

### Phase 5 limits

Passport label layout, the passport-number pattern and the MRZ/visual crop geometry
are assumptions awaiting official specimens and consented samples. MRZ century rules
are heuristics. Check digits prove a consistent reading, not authenticity. Generic
passports have no visual-zone extraction until Phase 6. Docker is still unexecuted here.

## Phase 4 record (5 October 2026)

Implemented the Cambodia NSSF member card adapter. Phase 3's extraction code became a
shared `KhmerLabelAdapter` engine driven by declarative `CardLayout`s; the national ID
is now a layout too. Engine improvements: rival-card detection (wrong Khmer card →
`DOCUMENT_TYPE_MISMATCH`), overlap-safe label matching, fronts require labels or
numbers, adapter-owned capture sides, primary-side classification confidence, and
`NOT_APPLICABLE` expiry/MRZ for cards that print neither. No migration was needed.
Design: [architecture-phase4.md](docs/architecture-phase4.md).

### Phase 4 validation evidence

- **109 tests: 109 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6, run with
  stray keys deliberately set in the environment to prove isolation
  ([artifacts/phase4-tests.txt](artifacts/phase4-tests.txt)).
- **Real OCR on a synthetic NSSF specimen.** Member number, linked ID number, Khmer
  name, Latin name, sex, birth date and employer were read correctly; the misread issue
  date was flagged. The session reached `SELFIE_REQUIRED` through the API.
- **Live PostgreSQL over HTTP** as `kyc_app`: the NSSF flow passed (masked `******5678`,
  `expiry_status: NOT_APPLICABLE`, `decision: null`). A national ID card uploaded to an
  NSSF session went back with `DOCUMENT_TYPE_MISMATCH`.
- The refactor kept every Phase 3 test green. The NSSF service test class also runs all
  inherited national ID tests.
- **Found and fixed.** Test settings could inherit keys from the shell environment. They
  are now hermetic.

### Phase 4 limits

The NSSF label set, member-number format, absence of an expiry and the back content are
assumptions that need official specimens and consented samples. Accuracy has been
measured on one synthetic card. The back QR waits for Phase 7. Cross-checking the linked
national ID number waits for Phase 12. Docker is still unexecuted here.

## Phase 3 record (5 October 2026)

Implemented the Cambodia National ID adapter and the document engine. The pipeline
decrypts captures, corrects perspective from the Phase 2 quadrilateral, normalizes the
image, and runs Tesseract 5 Khmer+Latin OCR with word boxes. A constrained Khmer-digit
re-read follows, then per-side classification (including swapped sides), label-anchored
fuzzy extraction of all card fields with provenance, and validation (required fields,
number format, date consistency, expiry, OCR confidence, script consistency). MRZ,
barcode and portrait report UNAVAILABLE. Fields are AES-GCM encrypted under a separate
PII keyring, with an HMAC document-number lookup. The engine handles recapture with
capture clearing, background processing after commit, a retry worker, and a masked
result with identity and review flags. Also delivered: migration `0003_phase3`, grants,
Docker image with Tesseract Khmer models, env keys, and Postman. Design:
[architecture-phase3.md](docs/architecture-phase3.md).

### Phase 3 validation evidence

- **91 tests: 91 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6
  ([artifacts/phase3-tests.txt](artifacts/phase3-tests.txt)).
- **Real OCR end to end.** A fictional specimen card was rendered with macOS Khmer fonts,
  photographed onto a background, uploaded through the API and read by Tesseract 5.5.3.
  The session reached `SELFIE_REQUIRED`. The Khmer name, Latin name, birth date, sex,
  place of birth and address were correct, and the number was masked `*****3040`.
  The issue date the OCR could not read reliably was flagged (`LOW_OCR_CONFIDENCE`,
  `MIXED_DIGIT_SCRIPTS`), not silently accepted.
- **Live PostgreSQL over HTTP** as the restricted `kyc_app` role, after
  `bootstrap_local.py`: the same flow passed, a plain card without ID text went back to
  `DOCUMENT_REQUIRED` (`DOCUMENT_NOT_RECOGNIZED`), and the database holds no plaintext
  names in `document_fields`.
- Tests also confirm: fields are tenant/field-bound (wrong context fails); audit and
  check records contain no PII; expired cards are accepted as evidence with `expiry: FAIL`
  and `decision: null`; type mismatch, unreadable critical fields, two fronts, and
  swapped sides behave as designed; `PASSPORT` waits honestly with no adapter; OCR
  failure leaves the session for the worker, which then completes it; a foreign tenant's
  session is unreachable.
- The live test exposed a missing database default on a new NOT NULL column, which was
  fixed before sign-off.

### Phase 3 limits

The label set, 9-digit number, Khmer numerals and `IDKHM` back marker are layout
assumptions that need confirmation against official specimens and consented real cards.
Accuracy has been measured only on one synthetic card. Policies are uncalibrated. MRZ
parsing (Phase 5), barcode (Phase 7) and portrait extraction (Phase 8) are not built.
The result endpoint is not yet permission-scoped (Phase 15). Docker is still unexecuted
on this machine.

## Phase 2 record (5 October 2026)

Implemented document capture and the quality pipeline. There are three upload
endpoints (`/documents`, `/documents/front`, `/documents/back`). A safe image
decoder enforces a format allowlist and pixel cap, applies EXIF orientation and
drops metadata. A deterministic heuristic quality engine scores blur, glare,
brightness, shadow, coverage, perspective, resolution and overall quality, with
reason codes and user instructions. AES-256-GCM capture storage binds each object
to its tenant, session and object and supports key rotation. Required sides come
per document type. The session drives `CREATED → DOCUMENT_REQUIRED →
DOCUMENT_PROCESSING`, with attempt limits, a body-size gate, retention purge and
orphan sweep, migration `0002_phase2`, a development camera client at `/capture`,
a Postman collection, and grants. Design: [architecture-phase2.md](docs/architecture-phase2.md).

### Phase 2 validation evidence

- **63 tests: 63 passed, 0 skipped, 0 failures**, on Python 3.13.15 (transcript:
  [artifacts/phase2-tests.txt](artifacts/phase2-tests.txt)).
- The quality gate accepts clean card, passport, rotated, PNG and quality-70 JPEG
  fixtures. It requests recapture with the correct instruction for blur, darkness,
  overexposure, glare, shadow, too far, too close, cropped, two documents,
  perspective, low resolution, wrong shape and blank frames. Fixtures are synthetic
  and non-personal.
- **Live PostgreSQL 18.6** (a temporary local cluster) was used. The RLS test now
  passes, including Phase 2 tables: a restricted role cannot read another tenant's
  `document_images` or write `identity_documents` under another tenant's ID.
- **End-to-end over HTTP.** `bootstrap_local.py` ran against live PostgreSQL, then
  uvicorn served the API as the restricted `kyc_app` role. curl covered: blurry
  front → RECAPTURE (`HOLD_STILL`); good front → ACCEPTED; back via the generic
  endpoint → `DOCUMENT_PROCESSING`; result `document_quality: PASS`, `decision: null`;
  upload after hand-off → 409; side replacement under the DELETE grant; foreign
  organization → 403. The stored files start with the `KYC1` envelope, contain no
  JPEG markers and have mode 0600.
- The capture page was smoke-tested in Chrome: session creation and file upload
  showed the RECAPTURE instruction, with no console errors.
- The migration ran upgrade → metadata comparison → downgrade → upgrade; PostgreSQL
  SQL: [artifacts/phase2-postgresql.sql](artifacts/phase2-postgresql.sql). OpenAPI:
  [artifacts/openapi-phase2.json](artifacts/openapi-phase2.json).

### Phase 2 limits

Docker is still not installed, so the image build and Compose run remain unexecuted.
Quality thresholds (`DOC-CAPTURE-HEURISTIC-2026.10.1`) are uncalibrated and tuned on
synthetic images only. Consent is not yet enforced before capture. GCS storage and a
scheduled purge are deployment work. No document is classified or read; sessions wait
in `DOCUMENT_PROCESSING`.

## Phase 1 record (4 October 2026)

## Phase 1 implementation

Implemented the FastAPI service foundation, canonical identity/OCR contracts,
independent document/biometric/liveness/fraud/risk interfaces, KYC state machine,
and seventeen-table PostgreSQL schema. Created a frozen Alembic migration,
transactional session creation/read/expiry, access and transition auditing,
credential-bound tenant context, composite ownership constraints, and forced
PostgreSQL row policies.

Created a non-root Docker image, local Compose database/API/migration services,
environment template and private credential generator, setup/architecture guides,
OpenAPI artifact, PostgreSQL SQL artifact, curl examples, and a nine-request
Postman collection with assertions.

## Validation evidence

- Python **3.13.15**, satisfying the requested Python 3.12+ baseline.
- **29 tests collected: 28 passed, 1 skipped, 0 failures, 0 errors.**
- Tests exercise the real FastAPI application through in-process ASGI and an
  isolated SQLite test database; no listening socket is required.
- Verified UUID4 sessions, tenant-bound credentials, foreign-session invisibility,
  payload validation, no client status overrides, transactional expiry, access
  auditing, terminal-state immutability, and required-evidence guards.
- Verified database rejection of cross-tenant/cross-session evidence links and
  ciphertext fields without key versions.
- Ran frozen migration upgrade → metadata comparison → downgrade → upgrade.
- Compiled PostgreSQL migration SQL containing UUID, TIMESTAMPTZ, JSONB, and all
  **17 forced tenant policies** with read/write scope expressions.
- Python compilation, shell syntax, and generated JSON parsing passed.

Reports:

- [Test transcript](artifacts/phase1-tests.txt)
- [Validation summary](artifacts/phase1-validation.json)
- [PostgreSQL migration SQL](artifacts/phase1-postgresql.sql)
- [OpenAPI schema](artifacts/openapi-phase1.json)

## Checks that remain pending outside this runner

The live PostgreSQL isolation test was skipped because no dedicated test database
was available. Starting an isolated PostgreSQL cluster failed when the restricted
environment denied shared-memory allocation. RLS was compiled and inspected but
has not been demonstrated on a running PostgreSQL server here.

Docker is not installed in this environment, so container build, Compose startup,
and the provisioning/grant scripts have not been executed. The local runner also
denied binding a web-server port, so no service is currently running.

Package downloads could not resolve PyPI. The core Python 3.13 packages were
loaded from existing local caches; MarkupSafe's pure Python fallback was used for
migration rendering. The PostgreSQL driver remains uninstalled in the local
virtual environment. Use the pinned `requirements.lock` installation from the
README on a normal machine or in Docker before live PostgreSQL validation.

These constraints are reported separately from the passing local tests. No GCP
resources, real customer captures, or biometric records were created. Phase 1
does not claim production readiness or functioning verification engines.

## Phase tracker

| Phase | Deliverable | Status |
| --- | --- | --- |
| 1 | Architecture, database schema, KYC session state machine | Complete; live PostgreSQL RLS verified 5 Oct; Docker pending |
| 2 | Camera/document upload and quality pipeline | Complete; live PostgreSQL E2E passed; Docker pending |
| 3 | Cambodia National ID adapter | Complete; real-OCR and live PostgreSQL E2E passed; Docker pending |
| 4 | Cambodia NSSF adapter | Complete; 109/109 tests; real-OCR and live PostgreSQL E2E passed; Docker pending |
| 5 | Passport and MRZ engine | Complete; 175/175 tests; real-OCR and live PostgreSQL E2E passed; Docker pending |
| 6 | International generic passport adapter | Complete; real-OCR end-to-end passed; Docker pending |
| 7 | QR/barcode engine | In progress |
| 8 | Face detection and quality | Implemented in a Codex checkpoint (`0d509c0`); final validation and docs pending |
| 9 | Face embeddings and 1:1 comparison | Implemented in a Codex checkpoint (`0d509c0`); final validation and docs pending |
| 10 | Liveness/anti-spoof integration | Not started |
| 11 | ePassport NFC mobile architecture | Not started |
| 12 | Cross-checks and fraud signals | Not started |
| 13 | Deterministic risk engine | Not started |
| 14 | Authorized manual review dashboard | Not started |
| 15 | Multi-tenant API and credential provisioning | Not started |
| 16 | Signed webhooks and SDKs | Not started |
| 17 | Security/privacy hardening | Not started |
| 18 | Load/performance testing | Not started |
| 19 | GCP production deployment | Not started |
| 20 | Additional country/document adapters | Not started |

## Approval requirement

`Document.md`, section 31, states: **“Stop and wait for approval before next
phase.”** Phase 6 will begin only after that approval.

## Earlier reference work

The first local review prototype was built while the document was empty. When the
full specification became available, that work was preserved in
`prototypes/local-review/` and the requested FastAPI/PostgreSQL platform was
started at the workspace root. It is not counted as completion of Phase 14.
