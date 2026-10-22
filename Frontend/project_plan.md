# Verix — KYC / Identity Verification Platform

## 1. Project Description
Verix is a multi-country KYC / identity verification product. It provides a
verification console where organizations start verification sessions, applicants
capture their identity documents and a live selfie, and reviewers inspect flagged
sessions and make decisions.

- **Product positioning:** A KYC console and applicant verification flow connected
  to the project's FastAPI verification, risk and manual-review services.
- **Target users:** Banks, fintechs, insurance, HR/ERP, telecom, e-commerce, schools
  and developers integrating identity verification via API.
- **Core value:** One consistent verification experience across Cambodia and
  international documents, with a pluggable document-adapter model for new countries.

> **Current scope:** The application calls the real KYC API for sessions, captures,
> OCR, face comparison, guided liveness, results and reviewer decisions. Test doubles
> belong to automated tests. Face matching and liveness remain uncalibrated, so their
> results require review. The browser reports NFC as unsupported; native passport
> readers and production cloud infrastructure remain unfinished. See the
> [readiness report](../docs/production-readiness-2026-10-07.md).

## 2. Page Structure
- `/` — Redirect to `/console` (authenticated overview and recent sessions)
- `/signin` — Staff reviewer authentication
- `/verify/new` — Start a new verification (country, document type, level, consent)
- `/verify/:sessionId` — Applicant capture flow (document → selfie → liveness → processing)
- `/verify/:sessionId/done` — Applicant completion status
- `/sessions/:sessionId` — Authorized staff result (document, identity, checks, decision)
- `/review` — Reviewer queue
- `/review/:sessionId` — Reviewer session detail + decision
- `/developers` — API keys, webhooks, document types & countries

## 3. Core Features
- [x] Console dashboard with session stats and recent sessions
- [x] New verification setup with document type selector (KH National ID, KH Passport, KH NSSF, International Passport/MRZ, Foreign National ID / Residence Card)
- [x] Document capture stage with quality feedback (blur, glare, brightness, coverage, perspective)
- [x] Selfie capture stage with face-quality feedback
- [x] Liveness challenge stage (unpredictable active challenge)
- [x] Verification processing pipeline visualization (status state machine)
- [x] Result view with masked document number, checks and reason codes
- [x] Reviewer queue with filters (status / document type / priority / search) + session detail with fields, confidence, checks, fraud signals and APPROVE / REJECT / REQUEST_RECAPTURE actions (audited)
- [x] Multi-tenant API keys, webhook endpoints + deliveries, and document-type/country registry
- [x] Server-authoritative quality feedback and recapture handling
- [x] Camera cancellation, request timeouts and polling recovery after temporary connection errors

## 4. Data Model Design
The FastAPI backend persists sessions and verification evidence in PostgreSQL with
tenant policies and Alembic migrations. The table summaries below describe the core
model; the complete current schema is in `src/kyc/db/models.py` and `migrations/`.

### Table: kyc_sessions
| Field | Type | Description |
|-------|------|-------------|
| id | uuid | Primary key (non-predictable) |
| organization_id | uuid | Owning organization (tenant) |
| user_id | text | Applicant reference |
| country | text | ISO country code |
| expected_document_type | text | Expected document adapter |
| verification_level | text | BASIC / STANDARD / ENHANCED |
| status | text | Session state machine status |
| created_at | timestamptz | Creation time |
| expires_at | timestamptz | Expiry |

### Table: identity_documents
| Field | Type | Description |
|-------|------|-------------|
| id | uuid | Primary key |
| session_id | uuid | FK to kyc_sessions |
| country | text | Issuing country |
| document_family | text | NATIONAL_ID / PASSPORT / NSSF / RESIDENCE_CARD |
| document_type | text | Resolved document type |
| number_masked | text | Masked document number |

### Table: document_checks
| Field | Type | Description |
|-------|------|-------------|
| id | uuid | Primary key |
| session_id | uuid | FK to kyc_sessions |
| check_type | text | QUALITY / MRZ / BARCODE / NFC / FRAUD |
| result | text | PASS / REVIEW / FAIL / NOT_APPLICABLE |
| details | jsonb | Evidence metadata |

### Additional planned tables
`document_images`, `document_fields`, `mrz_results`, `barcode_results`,
`nfc_results`, `biometric_templates`, `face_comparisons`, `liveness_checks`,
`fraud_signals`, `risk_assessments`, `manual_reviews`, `consents`, `audit_logs`.

> Biometric templates and raw captures are treated as highly sensitive: encrypted
> at rest, tenant-isolated, short-lived signed URLs, retention + automatic deletion,
> and never exposed in normal API responses.

## 5. Backend / Third-party Integration Plan
- **Database:** PostgreSQL through FastAPI/SQLAlchemy; authentication, scoped credentials, encrypted storage and tenant isolation are implemented in the backend.
- **Shopify:** Not needed.
- **Stripe / payments:** Not needed.
- **Email (Resend):** Not needed yet.
- **OCR / Biometrics:** CPU Tesseract/OpenCV engines run in the backend. Accuracy calibration and physical-device validation are outstanding.
- **NFC:** Server-side passive/active authentication is implemented; native chip-reading clients and governed issuer trust inputs are outstanding.

## 6. Development Phase Plan

### Phase 1: Console + Applicant Verification Flow (UI)
- Goal: Show the core verification experience end-to-end.
- Deliverable: Console dashboard, New verification setup, document capture, selfie
  capture, liveness challenge, processing status and result views connected to the KYC API.

### Phase 2: Reviewer / Manual Review Dashboard
- Goal: Let reviewers inspect flagged sessions and decide APPROVE / REJECT / REQUEST_RECAPTURE.
- Deliverable: Review queue with filters + session detail with fields, confidence,
  checks, fraud signals and decision actions (audited).

### Phase 3: Multi-tenant Developer Console
- Goal: Manage API keys, webhook endpoints and document-type / country registry.
- Deliverable: Developer console pages with API key management and webhook settings.

### Phase 4: Production Validation and Remaining Integrations
- Persistence, encrypted capture storage and document-adapter interfaces are implemented.
- Remaining work: single-use mobile handoff/device binding, server-driven challenge-step
  disclosure, native NFC readers, signed result envelopes, cloud KMS/operations, labelled
  OCR and biometric calibration, and physical iPhone/Android validation.
- Release evidence and limitations are tracked in the
  [correctness review](../docs/kyc-review-2026-10-07.md).
