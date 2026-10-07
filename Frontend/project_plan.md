# Verix — KYC / Identity Verification Platform

## 1. Project Description
Verix is a multi-country KYC / identity verification product. It provides a
verification console where organizations start verification sessions, applicants
capture their identity documents and a live selfie, and reviewers inspect flagged
sessions and make decisions.

- **Product positioning:** A KYC console + applicant verification flow that will
  eventually power document verification, biometrics, liveness, risk scoring and
  manual review.
- **Target users:** Banks, fintechs, insurance, HR/ERP, telecom, e-commerce, schools
  and developers integrating identity verification via API.
- **Core value:** One consistent verification experience across Cambodia and
  international documents, with a pluggable document-adapter model for new countries.

> **Scope note (important):** This project is being built as a **front-end product
> prototype with realistic mock data**. The heavy verification engines described in
> the original spec (Khmer OCR, MRZ decoding, face embedding/1:1 match, liveness /
> anti-spoof, ePassport NFC, GCP infrastructure) cannot run inside this platform and
> are represented as UI flow + simulated results. They are designed to be swapped for
> real external services later.

## 2. Page Structure
- `/` — Console dashboard (overview stats + recent verification sessions)
- `/verify/new` — Start a new verification (country, document type, level, consent)
- `/verify/session/:sessionId` — Applicant capture flow (document → selfie → liveness → processing)
- `/verify/result` — Verification result (document, identity, checks, decision)
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
- [x] "Simulate poor capture" demo toggle in the capture flow to showcase the recapture / low-quality rejection path

## 4. Data Model Design
No database is connected yet — this phase runs on mock data. When a backend
(Readdy Backend or SaaS Supabase) is connected, the planned tables are:

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
- **Database:** Not connected now — temporary demo data (mock). Readdy Backend or SaaS Supabase can be connected later for persistence, auth, storage and edge functions.
- **Shopify:** Not needed.
- **Stripe / payments:** Not needed.
- **Email (Resend):** Not needed yet.
- **OCR / Biometrics / NFC:** External integration points (not runnable here); simulated in UI.

## 6. Development Phase Plan

### Phase 1: Console + Applicant Verification Flow (UI)
- Goal: Show the core verification experience end-to-end.
- Deliverable: Console dashboard, New verification setup, document capture, selfie
  capture, liveness challenge, processing pipeline, and result view — all on mock data.

### Phase 2: Reviewer / Manual Review Dashboard
- Goal: Let reviewers inspect flagged sessions and decide APPROVE / REJECT / REQUEST_RECAPTURE.
- Deliverable: Review queue with filters + session detail with fields, confidence,
  checks, fraud signals and decision actions (audited).

### Phase 3: Multi-tenant Developer Console
- Goal: Manage API keys, webhook endpoints and document-type / country registry.
- Deliverable: Developer console pages with API key management and webhook settings.

### Phase 4: Persistence & Real Integrations
- Goal: Persist sessions/documents and wire real services.
- Deliverable: Backend connection, KYC tables, storage for captures, and adapter
  interface for external OCR / biometrics / NFC providers.