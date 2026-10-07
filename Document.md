MASTER PROMPT — BUILD ADVANCED MULTI-COUNTRY KYC / IDENTITY VERIFICATION PLATFORM

You are a Principal Identity Verification Architect, Python/FastAPI Engineer,
Computer Vision Engineer, Biometrics Engineer, Security Engineer, and GCP Architect.

Build a production-grade KYC / Identity Verification platform.

The platform must support initially:

1. Cambodia National ID
2. Cambodia Passport
3. Cambodia NSSF / social-security-related identity cards
4. Other Cambodian identity documents through pluggable adapters
5. International passports
6. Foreign national IDs
7. Residence/identity cards
8. Documents containing MRZ
9. Documents containing QR/barcodes

Later it must support additional countries without rewriting the KYC core.

==================================================
1. TECHNOLOGY
==================================================

Backend:
Python 3.12+
FastAPI
Pydantic
SQLAlchemy
Alembic

Database:
PostgreSQL

Cache:
Redis

Storage:
Google Cloud Storage

Infrastructure:
Google Cloud Platform
Cloud Run
Cloud SQL
Secret Manager
Pub/Sub
Cloud Logging
Cloud Monitoring

AI / CV:
OCR engine abstraction
Document classification model
Face detection
Face quality assessment
Face embedding model
1:1 face verification
Liveness / presentation-attack detection
Document fraud/tampering detection

Optional:
Google Document AI
Vertex AI

IMPORTANT:
General LLMs must NOT be the final authority for identity verification.

==================================================
2. SYSTEM ARCHITECTURE
==================================================

Build:

Mobile / Web / Partner API
          |
          v
      KYC Gateway
          |
          v
     FastAPI Backend
          |
          v
    KYC Orchestrator
          |
   +------+------+----------------+
   |             |                |
Document      Biometric         Fraud
Engine         Engine           Engine
   |             |                |
OCR           Face detect      Tamper
MRZ           Quality          Replay
QR            Embedding        Injection
Barcode       Face Match       Screenshot
NFC           Liveness         Duplicate
   |             |                |
   +-------------+----------------+
                 |
                 v
             Risk Engine
                 |
        +--------+--------+
        |        |        |
       PASS    REVIEW    FAIL


Keep services logically independent so they can later be deployed
as separate microservices.

==================================================
3. KYC SESSION
==================================================

Every verification starts with:

POST /v1/kyc/sessions

Create:

session_id
organization_id
user_id
country
expected_document_type
verification_level
status
expires_at

Example status:

CREATED
DOCUMENT_REQUIRED
DOCUMENT_PROCESSING
SELFIE_REQUIRED
LIVENESS_REQUIRED
NFC_REQUIRED
PROCESSING
MANUAL_REVIEW
VERIFIED
REJECTED
EXPIRED

Never use predictable public IDs.

==================================================
4. DOCUMENT CAPTURE
==================================================

Mobile/web camera must provide a document capture overlay.

Detect:

document boundaries
document orientation
blur
glare
brightness
shadow
cropping
perspective distortion
resolution
document too far away
document too close
multiple documents

Do not accept immediately.

Create quality scores:

blur_score
glare_score
brightness_score
document_coverage
perspective_score
overall_quality

If quality is insufficient:

RECAPTURE

Do not waste OCR/model calls on unusable images.

==================================================
5. DOCUMENT CLASSIFICATION
==================================================

Before extraction determine:

country
document_family
document_type
document_side
document_version

Example:

{
  "country": "KH",
  "document_family": "NATIONAL_ID",
  "document_type": "KH_NATIONAL_ID",
  "side": "FRONT",
  "version": "..."
}

Possible types:

KH_NATIONAL_ID
KH_PASSPORT
KH_NSSF
PASSPORT
NATIONAL_ID
RESIDENCE_CARD
DRIVING_LICENSE
UNKNOWN

Return confidence.

Low confidence -> manual review / recapture.

==================================================
6. DOCUMENT ADAPTER ARCHITECTURE
==================================================

DO NOT hardcode Cambodia logic inside the core OCR service.

Create:

DocumentAdapter

methods:

supports()
classify()
required_sides()
extract_fields()
validate_fields()
extract_portrait()
parse_mrz()
parse_barcode()
parse_qr()
get_security_checks()

Implement:

CambodiaNationalIDAdapter
CambodiaPassportAdapter
CambodiaNSSFAdapter
GenericPassportAdapter
GenericMRZAdapter
GenericIDAdapter

Later:

ThailandNationalIDAdapter
VietnamNationalIDAdapter
USPassportAdapter
etc.

==================================================
7. CAMBODIAN OCR
==================================================

Support:

Khmer Unicode
Latin text
mixed Khmer + English
numbers
dates
document numbers

Do NOT depend solely on generic OCR.

Pipeline:

Original image
   |
Document detection
   |
Perspective correction
   |
Orientation correction
   |
Image normalization
   |
Text-region detection
   |
Khmer/Latin OCR
   |
Field extraction
   |
Normalization
   |
Validation

Preserve:

raw OCR
normalized value
confidence
bounding box

Example:

{
  "field": "full_name_km",
  "raw_value": "...",
  "normalized_value": "...",
  "confidence": 0.96,
  "bbox": [...]
}

Never silently replace low-confidence OCR.

==================================================
8. FIELD EXTRACTION
==================================================

Create canonical identity schema.

IdentityDocument:

document_type
issuing_country
document_number

full_name
given_names
surname

full_name_local

date_of_birth
sex
nationality

place_of_birth
address

issue_date
expiry_date

issuing_authority

mrz

portrait

barcode_data
qr_data

Do not force unsupported documents to contain every field.

Use null when a field legitimately does not exist.

==================================================
9. MRZ ENGINE
==================================================

Implement standards-aware MRZ parsing.

Support relevant ICAO MRZ formats.

Pipeline:

detect MRZ
   |
OCR MRZ
   |
normalize allowed characters
   |
parse
   |
validate check digits
   |
compare against visual-zone fields

Extract where available:

document number
nationality
date of birth
sex
expiry
name
optional data

Return:

mrz_valid
check_digit_results
field_consistency

Never consider OCR success equivalent to document authenticity.

==================================================
10. QR / BARCODE ENGINE
==================================================

If a document contains QR/barcode:

detect code
decode
validate format
validate checksum/signature where defined
compare decoded information against OCR

IMPORTANT:

A QR code merely decoding successfully does NOT prove authenticity.

If a QR code contains a digital signature and official public verification
mechanism exists, implement cryptographic verification separately.

Return:

decoded
format_valid
signature_present
signature_valid
data_consistency

==================================================
11. PASSPORT NFC
==================================================

For NFC-enabled ePassports, support mobile NFC architecture.

Mobile application performs chip communication.

Flow:

Passport camera
   |
MRZ extraction
   |
NFC access setup
   |
Read chip
   |
Validate data
   |
Verify document security where supported
   |
Extract chip portrait
   |
Send verification result to backend

Design for ICAO 9303 concepts.

Keep separate statuses for:

NFC_NOT_SUPPORTED
NFC_NOT_AVAILABLE
NFC_FAILED
NFC_READ
NFC_VERIFIED

Do not claim a passport is authentic merely because NFC data was readable.

Store verification evidence/results instead of unnecessarily retaining
complete raw chip contents.

==================================================
12. DOCUMENT PORTRAIT EXTRACTION
==================================================

Locate portrait region.

Perform:

portrait detection
crop
orientation
quality assessment

Never modify identity features.

Generate a reference biometric template using the approved face model.

Reference:

ID portrait
OR
passport portrait
OR preferably verified ePassport chip portrait when available.

==================================================
13. LIVE CAMERA FACE CAPTURE
==================================================

Mobile/web face capture:

camera
   |
face detection
   |
quality assessment
   |
liveness
   |
face embedding
   |
1:1 comparison

Quality checks:

exactly one face
face centered
minimum face size
eyes visible
sufficient illumination
acceptable blur
acceptable pose
no severe occlusion

Return actionable instructions:

MOVE_CLOSER
MOVE_BACK
CENTER_FACE
MORE_LIGHT
REMOVE_OCCLUSION
HOLD_STILL

==================================================
14. LIVENESS / ANTI-SPOOF
==================================================

Face similarity is NOT liveness.

Implement liveness separately.

Protect against:

printed photograph
photo displayed on another device
video replay
screen replay
mask attacks where supported
virtual-camera/injection attacks where possible
AI-generated/manipulated media where supported

Support:

PASSIVE_LIVENESS
ACTIVE_LIVENESS

Active challenges must be unpredictable.

Example:

turn head
look toward target
controlled motion challenge

Do not use "blink once" as the only security mechanism.

Output:

liveness_result
liveness_score
attack_type if detected
model_version

Never expose internal security thresholds to end users.

==================================================
15. FACE EMBEDDING
==================================================

Reference portrait:

face
 |
alignment
 |
embedding model
 |
encrypted template A


Live capture:

face
 |
alignment
 |
same embedding model
 |
template B


Compare:

A <-> B

Use calibrated thresholds.

DO NOT invent:

"80% = same person"

Thresholds must be versioned and calibrated against validation data.

Store:

model_name
model_version
threshold_policy_version
comparison_score
decision

==================================================
16. 1:1 FACE VERIFICATION
==================================================

This KYC system performs:

CLAIMED IDENTITY
        |
Document portrait
        |
        +---- compare ---- Live face
                           |
                        Liveness

NOT unrestricted 1:N surveillance identification.

Create separate architecture/security review if 1:N identification
is ever requested.

==================================================
17. DOCUMENT FRAUD ENGINE
==================================================

Build a pluggable fraud-analysis pipeline.

Possible signals:

document layout mismatch
font inconsistency
portrait replacement indication
text-region inconsistency
unexpected document dimensions
MRZ mismatch
barcode/OCR mismatch
expiry
impossible dates
duplicate document usage
metadata anomaly
image manipulation indicators
screenshot/reproduction indicators
NFC mismatch

Return signals, not unsupported certainty.

Example:

{
  "signal": "MRZ_VISUAL_DOB_MISMATCH",
  "severity": "HIGH"
}

==================================================
18. CROSS-CHECK ENGINE
==================================================

Compare independent sources.

Example passport:

Visual OCR
   |
   +-- DOB -------+
   +-- Name ------+
   +-- Passport --+
                  |
MRZ --------------+--> Consistency Engine
                  |
NFC ---------------+
                  |
QR/barcode --------+

Detect mismatches.

Example:

OCR DOB: 1999-01-01
MRZ DOB: 1999-01-07

=> REVIEW

Never automatically "fix" disagreement by choosing one value silently.

==================================================
19. RISK ENGINE
==================================================

Create deterministic, configurable policies.

Inputs:

document quality
document validity
MRZ validity
NFC verification
QR verification
fraud signals
face match
liveness
duplicate checks
country/document policy

Output:

PASS
REVIEW
FAIL

Also return reason codes.

Example:

PASS:
DOCUMENT_VALID
FACE_MATCH
LIVENESS_PASS

REVIEW:
LOW_OCR_CONFIDENCE
FIELD_MISMATCH
FACE_SCORE_BORDERLINE

FAIL:
LIVENESS_FAILED
EXPIRED_DOCUMENT
FACE_MISMATCH
HIGH_RISK_TAMPER_SIGNAL

LLMs must NOT override the Risk Engine.

==================================================
20. MANUAL REVIEW
==================================================

Create reviewer dashboard.

Reviewer can see only authorized information.

Display:

document images when retention/policy allows
extracted fields
confidence
MRZ checks
QR checks
NFC result
face comparison
liveness result
fraud signals
risk reason codes

Reviewer actions:

APPROVE
REJECT
REQUEST_RECAPTURE

Require reason.

Audit every reviewer action.

==================================================
21. DATABASE
==================================================

Create tables:

kyc_sessions
identity_documents
document_images
document_fields
document_checks
mrz_results
barcode_results
nfc_results

biometric_templates
face_comparisons
liveness_checks

fraud_signals
risk_assessments
manual_reviews
consents
audit_logs

Important:

Do not casually store raw biometric information.

Use encryption and retention policies.

==================================================
22. BIOMETRIC SECURITY
==================================================

Treat:

selfies
face templates
passport portraits
national-ID portraits
NFC portraits

as highly sensitive data.

Implement:

encryption at rest
encryption in transit
strict IAM
tenant isolation
short-lived signed URLs
retention periods
automatic deletion
access logging
key rotation
consent records

Never expose biometric templates through normal API responses.

==================================================
23. PRIVACY-FIRST STORAGE
==================================================

Separate:

PII database
biometric storage
audit data
application data

Prefer storing:

verification result
necessary identity fields
verification evidence metadata

rather than retaining every raw capture indefinitely.

Retention must be configurable per organization/jurisdiction.

==================================================
24. API DESIGN
==================================================

Create:

POST /v1/kyc/sessions

POST /v1/kyc/{session}/documents
POST /v1/kyc/{session}/documents/front
POST /v1/kyc/{session}/documents/back

POST /v1/kyc/{session}/selfie
POST /v1/kyc/{session}/liveness

POST /v1/kyc/{session}/nfc

POST /v1/kyc/{session}/verify

GET /v1/kyc/{session}

GET /v1/kyc/{session}/result

Reviewer:

GET  /v1/review/queue
GET  /v1/review/{session}
POST /v1/review/{session}/decision

Administrative:

GET /v1/document-types
GET /v1/countries

==================================================
25. RESULT FORMAT
==================================================

Example:

{
  "session_id": "...",
  "status": "VERIFIED",

  "document": {
    "country": "KH",
    "type": "NATIONAL_ID",
    "document_number_masked": "******1234",
    "expiry_status": "VALID"
  },

  "identity": {
    "full_name": "...",
    "full_name_local": "...",
    "date_of_birth": "...",
    "nationality": "KH"
  },

  "checks": {
    "document_quality": "PASS",
    "mrz": "NOT_APPLICABLE",
    "barcode": "PASS",
    "nfc": "NOT_APPLICABLE",
    "face_match": "PASS",
    "liveness": "PASS",
    "fraud": "PASS"
  },

  "decision": {
    "result": "PASS",
    "reason_codes": [...]
  }
}

Mask sensitive information according to permissions.

==================================================
26. MULTI-TENANT KYC API
==================================================

This will become a service used by:

banks
fintech
insurance
HR
ERP
POS
schools
telecom
e-commerce
government integrations where authorized
mobile applications
external developers

Architecture:

Customer Application
       |
       v
YOUR KYC API
       |
       +-- Document Verification
       +-- Biometrics
       +-- Liveness
       +-- Risk
       +-- Review

Every request includes organization context.

No organization may access another organization's KYC records.

==================================================
27. WEBHOOKS
==================================================

Implement signed webhooks:

kyc.processing
kyc.document.accepted
kyc.selfie.required
kyc.review.required
kyc.verified
kyc.rejected

Example:

Customer App
     |
Create KYC
     |
Your KYC platform
     |
processing...
     |
signed webhook
     |
Customer backend

Implement:

signature verification
timestamp
replay protection
retry
idempotency

==================================================
28. SDK STRATEGY
==================================================

Prepare SDKs:

JavaScript/TypeScript
Python
Android/Kotlin
iOS/Swift
Flutter

Mobile SDK must eventually support:

camera capture
document framing
quality feedback
selfie capture
liveness
NFC passport reading

Do not put sensitive verification logic entirely in the client.

==================================================
29. GCP DEPLOYMENT
==================================================

Use:

Cloud Load Balancing/API Gateway
             |
          Cloud Run
             |
       FastAPI KYC API
             |
   +---------+-----------+
   |                     |
Cloud SQL            Cloud Storage
PostgreSQL           encrypted captures
   |
Redis/Memorystore
   |
Pub/Sub
   |
KYC Workers

Secrets:
Secret Manager

Logging:
Cloud Logging

Monitoring:
Cloud Monitoring

Use service accounts and least privilege.

==================================================
30. DO NOT USE LLM AS OCR SECURITY AUTHORITY
==================================================

Gemini/Claude/GPT may assist with:

document-type classification
difficult field extraction
normalization
reviewer assistance

They must NOT independently decide:

"This passport is authentic."

They must NOT independently decide:

"This is definitely the same person."

The authoritative path must use specialized verification components
and deterministic policy.

==================================================
31. DEVELOPMENT PHASES
==================================================

Do not build everything at once.

PHASE 1
Architecture + DB schema + KYC session state machine

PHASE 2
Camera/document upload + quality pipeline

PHASE 3
Cambodia National ID adapter

PHASE 4
Cambodia NSSF adapter

PHASE 5
Passport + MRZ engine

PHASE 6
International generic passport adapter

PHASE 7
QR/barcode engine

PHASE 8
Face detection + quality

PHASE 9
Face embeddings + 1:1 comparison

PHASE 10
Liveness / anti-spoof integration

PHASE 11
ePassport NFC mobile architecture

PHASE 12
Cross-check + fraud signals

PHASE 13
Risk engine

PHASE 14
Manual review dashboard

PHASE 15
Multi-tenant API + API keys

PHASE 16
Webhooks + SDK

PHASE 17
Security/privacy hardening

PHASE 18
Load/performance testing

PHASE 19
GCP production deployment

PHASE 20
Additional countries/document adapters

For EACH phase:

1. Explain design.
2. Show directory tree.
3. List files being created.
4. Write complete code.
5. Create migrations.
6. Create tests.
7. Create Docker configuration.
8. Provide environment variables.
9. Provide curl/Postman tests.
10. Explain security concerns.
11. Stop and wait for approval before next phase.

==================================================
32. FINAL ARCHITECTURAL RULE
==================================================

Design this as:

                UNIVERSAL IDENTITY PLATFORM
                           |
                    Identity Gateway
                           |
             +-------------+-------------+
             |             |             |
          Document      Biometrics      Risk
             |             |             |
       +-----+-----+   +---+---+     +---+---+
       |     |     |   |       |     |       |
      OCR   MRZ   NFC Face   Liveness Fraud  Policy
       |     |     |   |       |     |       |
       +-----+-----+---+-------+-----+-------+
                           |
                     KYC Decision
                           |
                +----------+----------+
                |          |          |
              PASS       REVIEW      FAIL
                           |
                    Integration API
                           |
     +---------+--------+--------+--------+---------+
     |         |        |        |        |         |
   Fintech    ERP      HRM      POS     Mobile    Custom

---

## Implementation progress — 6 October 2026

The original specification above is preserved. Phases 1–18 have been implemented
at this workspace root. The user authorized Phases 8 and 9 together, then Phases 10
and 11, then Phases 12, 13 and 14 in turn, then Phases 15 and 16 together, then Phases 17 and 18;
their validation passed.

| Phase | Deliverable | Status |
| --- | --- | --- |
| 1 | Architecture + DB schema + KYC session state machine | Complete; live PostgreSQL RLS verified; Docker pending |
| 2 | Camera/document upload + quality pipeline | Complete; live PostgreSQL end-to-end passed; Docker pending |
| 3 | Cambodia National ID adapter | Complete; real-OCR end-to-end passed; Docker pending |
| 4 | Cambodia NSSF adapter | Complete; 109/109 tests; real-OCR end-to-end passed; Docker pending |
| 5 | Passport + MRZ engine | Complete; 175/175 tests; real-OCR and live PostgreSQL E2E passed; Docker pending |
| 6 | International generic passport adapter | Complete; real-OCR end-to-end passed; Docker pending |
| 7 | QR/barcode engine | Complete; 264/264 tests; live PostgreSQL passed; Docker pending |
| 8 | Face detection + quality | Complete; native inference and tenant isolation verified; quality limits documented |
| 9 | Face embeddings + 1:1 comparison | Complete; 264/264 tests; full document→selfie pipeline verified; default REVIEW |
| 10 | Liveness / anti-spoof integration | Complete; re-tested 5 Oct (300/300); photo attacks blocked; uncalibrated REVIEW |
| 11 | ePassport NFC mobile architecture | Complete; 300/300 tests; server-side PA + AA; clone/tamper detected; no physical chip yet |
| 12 | Cross-check + fraud signals | Complete; 318/318 tests; signals not verdicts; forensics models not included |
| 13 | Risk engine | Complete; 333/333 tests; deterministic, tighten-only policy; uncalibrated biometrics → manual review |
| 14 | Manual review dashboard | Complete; 344/344 tests; role-based, audited, guarded approvals |
| 15 | Multi-tenant API + API keys | Complete; scoped hashed keys, RLS lookup, client tokens, idempotency; live E2E 25/25 |
| 16 | Webhooks + SDK | Complete; signed outbox webhooks, retries, SSRF-safe delivery; Python + TypeScript SDKs; mobile SDKs specified, not built |
| 17 | Security/privacy hardening | Complete; 440/440 tests, 24/24 DB security checks, no known runtime dependency vulnerabilities |
| 18 | Load/performance testing | Complete; 445/445 tests; admission control, lock-free status reads, worker scale-out and OCR-tier findings |
| 19 | GCP production deployment | Not started |
| 20 | Additional countries/document adapters | Not started |

### Phase 1 deliverables

- FastAPI session creation, retrieval, pending results, and health endpoints.
- UUID4 public IDs, credential-bound organization context, and session expiry.
- PostgreSQL SQLAlchemy schema with all 16 requested tables plus organizations.
- Frozen Alembic migration and 17 forced tenant row-security policies.
- Event-driven state machine with terminal-state and evidence guards.
- Separate document, OCR, biometric, liveness, fraud, and risk contracts.
- Canonical identity fields with nullable unsupported fields and Khmer OCR support
  in the data contract; no working OCR engine is claimed.
- Docker/Compose configuration, environment generator, bootstrap script,
  architecture explanation, directory tree, curl examples, and Postman checks.

### Validation and limits

**29 tests: 28 passed, 1 skipped, 0 failures, 0 errors**, on Python 3.13.15.
The API and migration lifecycle were tested in process using isolated SQLite.
PostgreSQL migration SQL compiled with all 17 tenant policies. Live PostgreSQL
RLS execution and Docker validation remain pending because this runner blocked
PostgreSQL startup and has no Docker installation. The local PostgreSQL driver
also needs installation from the pinned requirements on a normal machine.
No service is running, and no GCP resources have been deployed.

See [README.md](README.md) for design, directory tree, environment variables,
Docker/Python launch commands, and curl examples. Detailed status and evidence
are in [BUILD_PROGRESS.md](BUILD_PROGRESS.md); the design is in
[docs/architecture-phase1.md](docs/architecture-phase1.md).

### Phase 2 deliverables

- Document upload endpoints (`/documents`, `/documents/front`, `/documents/back`) and
  a development camera client with an overlay at `/capture`.
- Deterministic quality gate with blur, glare, brightness, shadow, coverage,
  perspective and resolution scores, plus RECAPTURE instructions. Versioned and
  explicitly uncalibrated.
- AES-256-GCM encrypted capture storage with key rotation, retention purge and
  orphan sweep. Rejected images are never stored.
- Migration `0002_phase2`, design in [docs/architecture-phase2.md](docs/architecture-phase2.md).

### Phase 3 deliverables

- Cambodia National ID adapter: label-anchored Khmer/Latin extraction of all card
  fields with raw value, normalized value, confidence and bounding box.
- Document engine: perspective correction, Tesseract 5 OCR, constrained Khmer-digit
  re-read, side classification, validation, encrypted field storage, recapture flow.
- Masked result with identity fields and review flags; decision left to Phase 13.
- Migration `0003_phase3`, design in [docs/architecture-phase3.md](docs/architecture-phase3.md).

### Phase 4 deliverables

- Cambodia NSSF member card adapter: member number, linked national ID number,
  Khmer/Latin names, sex, birth date, employer, issue date; expiry and MRZ reported as
  NOT_APPLICABLE when not printed.
- Shared Khmer label engine with declarative card layouts; rival-card detection between
  the national ID and NSSF cards.
- No migration needed; design in [docs/architecture-phase4.md](docs/architecture-phase4.md).

### Phase 5 deliverables

- Cambodia passport adapter: Khmer/English visual labels, Latin names, passport
  number and dates, with TD3 MRZ extraction and visual/MRZ disagreement review flags.
- Reusable TD1/TD2/TD3 MRZ parser and dedicated OCR passes; the national ID back MRZ
  now uses the same engine. Check digits establish reading consistency only.
- `GenericMRZAdapter` supplies MRZ-only passport fields; Phase 6 international
  visual-zone extraction remains unstarted.
- Encrypted MRZ text and field values; non-PII validity/check/consistency metadata in
  existing tenant-scoped `mrz_results`. No migration or new dependencies; existing
  installations rerun bootstrap for restricted-role MRZ grants.
- Typed client MRZ metadata: format, validity, check-digit outcomes and field
  consistency; raw MRZ stays private. Generic MRZ-only consistency is NOT_APPLICABLE.
- DATA_PAGE curl/Postman workflows and synthetic parser/API/real-OCR coverage.
  175/175 tests passed with live PostgreSQL; MRZ tenant RLS and restricted-role
  real-OCR passport workflows passed. Docker remains unexecuted. Evidence:
  [artifacts/phase5-postgres-e2e.json](artifacts/phase5-postgres-e2e.json).
- Design and security limits in [docs/architecture-phase5.md](docs/architecture-phase5.md).

### Phases 8–9 deliverables

- YuNet CPU detection and actionable selfie quality checks; SFace aligned normalized
  embeddings and versioned, same-model 1:1 cosine comparison.
- Explicit biometric consent, encrypted selfie capture and separately keyed templates,
  scoped provenance, retention purge and migration `0004_phase8_9`.
- Selfie API, resumable front-camera capture client, masked comparison results, pinned
  model/license provisioning and Postman workflows.
- 264/264 tests passed with live PostgreSQL; native detector/recognizer and encrypted
  restricted-role selfie flow verified. Default matching returns REVIEW. Eye visibility,
  severe occlusion and production calibration remain unverified; no liveness or final
  decision is asserted. Docker remains unexecuted.
- [Design](docs/architecture-phase8-9.md), [test evidence](artifacts/stage8-9-tests.txt),
  [native PostgreSQL evidence](artifacts/stage8-9-postgres-native-e2e.json).

**Approval gate:** Section 31 requires stopping after each phase. The user approved
Phases 8 and 9 together. Work stops after Phase 9 until separate approval for Phase 10.

**Approval gate (Phases 15–16):** the user approved Phases 15 and 16 together. Work stops
after Phase 16 until separate approval for Phase 17 (security/privacy hardening). That approval
was given on 6 October 2026, together with Phase 18; both are complete. Work stops after
Phase 18 until separate approval for Phase 19 (GCP production deployment).

### Phase 18 deliverables

- Load generator and per-stage/pipeline benchmarks with recorded baseline and post-fix results.
- Admission control fixing a connection-pool/thread-pool stall above 40 in-flight requests.
- Lock-free status reads; worker-process scale-out guidance (`WEB_CONCURRENCY`).
- Deferred OCR tier with a continuously claiming, parallel document worker; capacity guidance
  for Phase 19 in [docs/architecture-phase18.md](docs/architecture-phase18.md).

### Phase 17 deliverables

- Production configuration gates, secure HTTP defaults, sanitized security logs,
  CIDR-restricted API keys and expiring reviewer tokens.
- Current-policy document consent, separately authorized subject erasure, independent
  webhook encryption and authenticated key inventory/resealing.
- Restricted database grants and forced tenant RLS, append-only coded decisions and
  audits, and tenant-scoped erasure of reviewer notes. Migrations `0011_phase17` and
  `0012_phase17_finalize` are applied locally.
- HTTPS/redirect protection in both SDKs, patched runtime dependencies, production
  configuration examples, OpenAPI and Postman workflows.
- 440/440 Python tests passed with no skips, 24/24 live database security checks and
  6/6 TypeScript SDK tests passed; 31 runtime packages audited with no known vulnerabilities.
  Docker execution and GCP deployment remain unverified here.
- [Design and limits](docs/architecture-phase17.md),
  [validation summary](artifacts/phase17-validation.json),
  [full test transcript](artifacts/phase17-tests.txt).
