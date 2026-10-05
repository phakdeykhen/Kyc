# Build progress — Universal Identity Platform

Updated: 5 October 2026. The full requirements in `Document.md` control the build.
Phases 1–10 are implemented. Work is paused at the Phase 10 approval gate; Phase 11
(ePassport NFC) has not started.

## Phase 10 implementation (5 October 2026)

Implemented active liveness. The server issues a single-use, random head-movement
challenge (frontal baseline + three distinct moves, 24 sequences, 256-bit nonce stored
only as a hash, 120 s TTL). The client returns 4–12 raw frames tagged with step indices.
Each move is verified with an affine-invariant 3D test on YuNet landmarks: flat faces
keep the nose's eye/mouth-frame coordinates; real heads move them. Flat-face presentations
and single-image replays FAIL. Every frame must still be the selfie's person (SFace
continuity). Incomplete challenges are retryable within an attempt limit. Frames are
never stored; `liveness_checks` keeps outcomes, metrics, coverage and the nonce hash.
The policy is uncalibrated, so the best result is REVIEW. The capture page gained the
movement step. Migration `0005_phase10` adds `liveness_challenges` (forced RLS).
Design: [architecture-phase10.md](docs/architecture-phase10.md).

### Phase 10 validation evidence

- **281 tests: 281 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6
  ([artifacts/phase10-tests.txt](artifacts/phase10-tests.txt)); the client simulation
  also covers challenge, retry and finish.
- Geometry checked against a projected 3D head: real ±20° turns move the nose coordinate
  ±0.16, while a flat photo tilted up to 45° at close range moves it at most 0.053.
  Invariance holds exactly under 50 random affine transforms.
- Real YuNet on a real face photo, moved, rotated, scaled and tilted like a handheld
  printout: 0 of 3 moves completed, never accepted.
- Live PostgreSQL over HTTP as `kyc_app`: photographed ID → OCR → selfie → liveness. Two
  photo-attack attempts were recorded and not accepted, the session stayed in
  `LIVENESS_REQUIRED`, a reused challenge got 409, the third challenge got 429, and no
  frames were retained.
- The live RLS test now covers `liveness_challenges` and `liveness_checks`.

### Phase 10 limits

Uncalibrated thresholds; no device attestation (real-time deep-fake injection is not
defeated); 3D masks and AI-generated media are not detected; no passive model. A genuine
moving head was simulated with 3D landmarks, not filmed.


## Phases 8–9 implementation (5 October 2026)

Implemented local CPU YuNet face detection, measured capture quality and actionable
selfie recapture, SFace aligned 128-dimensional embeddings, and same-model 1:1 cosine
comparison against the session's accepted document portrait. The selfie endpoint is
`POST /v1/kyc/{session_id}/selfie`; `/capture/` continues document processing into a
front-camera selfie flow, requires explicit biometric consent, and can resume sessions.
Document portraits use original color pixels, geometry correction and layout-specific
regions, with a whole-page fallback. Physical/logical side mapping survives swapped
document captures. Unusable reference portraits reopen document capture and invalidate
stale document evidence while retaining attempt history.

Templates use a third independent AES-256-GCM keyring with tenant/session/template/
source/model-bound associated data. Photos remain encrypted capture objects. Selfie
captures and face-quality evidence have tenant-scoped tables; migration `0004_phase8_9`
adds provenance and retention metadata. Bootstrap grants, purge integration, pinned
model/license provisioning, OpenAPI and Postman workflows are included. Models are
installed locally and never fetched by a request. Design:
[architecture-phase8-9.md](docs/architecture-phase8-9.md).

### Phases 8–9 validation evidence

- **264 tests: 264 passed, 0 skipped, 0 failures** against the combined workspace,
  including the international document and barcode work that landed during this build
  ([transcript](artifacts/stage8-9-tests.txt)).
- Real CPU YuNet/SFace inference detected one face, rejected blank/multiple-face
  fixtures, aligned the face and produced normalized 128-dimensional embeddings.
  Self-comparison scored 1.0 and correctly remained REVIEW under the uncalibrated
  policy ([native evidence](artifacts/stage8-9-native-face-smoke.json)).
- Real PostgreSQL through ASGI as restricted `kyc_app`, starting from a seeded accepted
  reference capture: consented native selfie submission reached PROCESSING, stored two
  encrypted templates and an encrypted selfie, and exposed only safe REVIEW evidence.
  The encrypted round trip, model-bound payloads, null decision, and late-upload 409
  passed. This boundary test excludes document classification/OCR
  ([evidence](artifacts/stage8-9-postgres-native-e2e.json)).
- Live PostgreSQL verifies tenant visibility/default-deny access, rejected foreign
  writes, and cross-session document/template links for all biometric tables. SQLite
  migration upgrade → metadata comparison → downgrade → upgrade passed.
- API tests cover quality and reference recapture, consent, expiry, attempt limits,
  unavailable models/storage, stale-evidence hiding and tenant-specific retention.
  Uploads now enforce actual streamed bytes and retain multipart captures in bounded
  memory until encryption; a valid upload over 1 MiB does not spool plaintext to disk.
- Client syntax and simulated consent, resume, camera, recapture, processing handoff
  and unavailable-engine recovery passed. The simulation does not validate a physical
  camera or rendered layout. Model/license integrity verification and Python compilation
  passed. Artifacts: [OpenAPI](artifacts/openapi-stage8-9.json),
  [migration SQL](artifacts/stage8-9-postgresql.sql).

### Independent full-pipeline validation (Claude, 5 October 2026)

Codex's live PostgreSQL test seeded the document reference and excluded OCR. The complete
chain was then run in one session over HTTP as restricted `kyc_app` with real Tesseract and
real YuNet/SFace: a photographed SPECIMEN card carrying a public OpenCV sample face went
through capture → OCR/MRZ → portrait template → consented selfie → 1:1 comparison
([evidence](artifacts/stage8-9-full-pipeline-e2e.json)).

- Same person: cosine 0.691 (KH ID), 0.795 (foreign passport), 0.844 (foreign ID card);
  different person: 0.224. Every result stays `REVIEW` under the uncalibrated policy, the
  session moves to `LIVENESS_REQUIRED` and `decision` stays null.
- A small face is recaptured with `MOVE_CLOSER`; a selfie without consent is refused (422).
- Gap fixed: the Phase 6 generic passport/ID adapters had no portrait regions and relied
  on the whole-page fallback; they now declare the ICAO TD3 portrait zone and the ID-1
  front-left area.
- Suite after these changes: **264/264 passed, 0 skipped**, live PostgreSQL included
  ([transcript](artifacts/stage8-9-tests.txt)).
- Important for Phase 13: with the uncalibrated policy, a *different* person also proceeds
  with `face_match: REVIEW`. The risk engine must never pass a session on that basis.

### Phases 8–9 limits

The default uncalibrated comparison always returns REVIEW. Cosine similarity is not
an identity probability. Pose/quality are heuristics; eye visibility and severe
occlusion remain explicitly unverified and prevent a full quality PASS. Upstream sample
success does not establish production accuracy, fairness or threshold calibration.
Liveness, NFC and final risk decisions remain later work. Docker remains unexecuted.

## Phase 7 implementation (5 October 2026)

Implemented the QR/barcode engine (`kyc/barcode/`). zxing-cpp decodes QR, PDF417, Data
Matrix, Aztec and linear codes from every captured side. Payloads are parsed as signed
JWS, JSON, key=value, AAMVA PDF417 (North American driving licences) or ICAO Visible
Digital Seals (detected; verification needs a CSCA trust list), otherwise as unstructured
text. JWS signatures (ES256/ES384/RS256/PS256/EdDSA) are verified only against keys in
`BARCODE_TRUST_STORE`; `alg: none` always fails. Decoded fields are compared with the
printed document. The `BARCODE` check is PASS for a valid signature or consistent data,
REVIEW for any disagreement, FAIL for a signature that does not verify, and
NOT_APPLICABLE when no code exists. `barcode_results` stores symbology, format,
signature state and per-field consistency, with the payload AES-GCM encrypted under the
PII keyring. Design: [architecture-phase7.md](docs/architecture-phase7.md).

### Phase 7 validation evidence

- **264 tests: 264 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6
  ([artifacts/phase7-tests.txt](artifacts/phase7-tests.txt)).
- Real decode: the photographed NSSF SPECIMEN back carries a real QR. The session
  reaches `SELFIE_REQUIRED` with `barcode: PASS`, all three QR fields MATCH the
  printed front, and the stored payload contains no plaintext.
- Live PostgreSQL over HTTP as `kyc_app` after bootstrap: the NSSF QR flow, the foreign
  passport and the foreign ID card all passed; one `barcode_results` row was persisted.
- Found and fixed: the Phase 2 glare measure counted printed white beside dark ink (QR
  modules, quiet zones) as glare, so a card with a QR was rejected; it now ignores
  saturated pixels near ink, and the glare/overexposure fixtures are still rejected.
  The AAMVA parser missed the number following the subfile marker. Generic cards
  are now classified by their strongest correctly-placed side.

### Phase 7 limits

No official verification key or format is known for Cambodian document barcodes, so
their codes can only be checked for consistency, not authenticity. VDS verification
needs ICAO PKD/CSCA material. The NSSF QR payload is a fictional fixture format.

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

- Full suite green after the change; final count in the Phase 7 entry.
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
| 7 | QR/barcode engine | Complete; 264/264 tests; real decode end-to-end and live PostgreSQL passed; Docker pending |
| 8 | Face detection and quality | Complete; native inference, live PostgreSQL and full document→selfie pipeline verified |
| 9 | Face embeddings and 1:1 comparison | Complete; 264/264 tests; full pipeline E2E (same 0.69–0.84 vs different 0.22); uncalibrated REVIEW |
| 10 | Liveness/anti-spoof integration | Complete; 281/281 tests; real photo attack blocked; live PostgreSQL passed; uncalibrated REVIEW |
| 11 | ePassport NFC mobile architecture | Waiting for approval |
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
phase.”** The user's request authorized Phases 8 and 9 together. Phase 10 requires
separate approval.

## Earlier reference work

The first local review prototype was built while the document was empty. When the
full specification became available, that work was preserved in
`prototypes/local-review/` and the requested FastAPI/PostgreSQL platform was
started at the workspace root. It is not counted as completion of Phase 14.
