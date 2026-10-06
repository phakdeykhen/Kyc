You are a Senior KYC Platform Architect, Identity Verification Engineer, Biometrics Engineer, Security Engineer, and DevOps Engineer.

I already have an existing KYC platform. Do NOT rebuild it from scratch.

Your job is to inspect my existing project, identify what is incomplete, weak, mocked, uncalibrated, insecure, or not production-ready, and then improve it step by step while preserving the existing architecture and APIs wherever possible.

The current platform already includes:

- FastAPI backend
- PostgreSQL
- Multi-tenant architecture
- Row-level security
- API keys
- Session client tokens
- Consent
- Document capture
- Document quality checks
- Cambodia National ID
- Cambodia NSSF
- Cambodia Passport
- Generic passport
- Generic National ID
- Khmer OCR
- English/Latin OCR
- MRZ reading
- QR/barcode reading
- Document portrait extraction
- Selfie capture
- Face detection
- Face embeddings
- 1:1 face matching
- Active liveness
- NFC/ePassport server verification
- Fraud signals
- Risk engine
- Manual review dashboard
- Signed webhooks
- Data erasure
- Encryption
- Audit logs
- Load testing

Do not assume that "implemented" means "production ready."

The objective is to bring this KYC system from a functional prototype to a production-grade identity verification platform.

# MAIN OBJECTIVE

Review the entire current implementation and prepare it for production approval.

The final production flow should be:

User
↓
Create KYC Session
↓
Consent
↓
Capture Document
↓
Document Quality Gate
↓
Document Classification
↓
Perspective Correction
↓
OCR / MRZ / QR / Barcode
↓
Document Authenticity Analysis
↓
Extract Document Portrait
↓
Capture Live Selfie
↓
Selfie Quality Gate
↓
Liveness / Anti-Spoof
↓
1:1 Face Match
↓
Optional ePassport NFC
↓
Cross-Source Verification
↓
Fraud Detection
↓
Risk Engine
↓
PASS / REVIEW / FAIL
↓
Manual Review when necessary
↓
Signed Webhook / API Result

# IMPORTANT RULE

Never silently guess identity information.

If evidence is missing, uncertain, low confidence, conflicting, unsupported, or uncalibrated:

REVIEW

Do not automatically PASS.

Use:

PASS = sufficient trusted evidence
REVIEW = uncertain or incomplete evidence
FAIL = strong verified failure condition

Unknown must never automatically mean FAIL.

# 1. FIRST AUDIT THE EXISTING PROJECT

Before making large changes, inspect:

- source tree
- models
- migrations
- database schema
- APIs
- services
- document adapters
- OCR implementation
- biometric implementation
- liveness implementation
- NFC implementation
- fraud engine
- risk engine
- review dashboard
- authentication
- encryption
- storage
- worker system
- configuration
- Docker configuration
- tests
- load tests
- SDKs
- webhook system

Produce an internal gap list such as:

COMPLETE
PARTIAL
MOCK
NOT CALIBRATED
NOT TESTED WITH REAL HARDWARE
SECURITY RISK
PERFORMANCE RISK
PRODUCTION BLOCKER

Then fix production blockers first.

Do not replace working components unnecessarily.

# 2. DOCUMENT CAPTURE

Improve document capture for real Cambodian documents.

Support:

KH_NATIONAL_ID
KH_NSSF
KH_PASSPORT
PASSPORT
NATIONAL_ID
RESIDENCE_CARD
DRIVING_LICENSE

Cards require:

FRONT
BACK

Passports require:

DATA_PAGE

Check:

- all document corners visible
- document coverage
- focus
- blur
- glare
- brightness
- shadows
- perspective
- orientation
- resolution
- excessive compression
- screenshot/rephotographed-screen indicators where possible
- more than one document in frame

Return useful instructions:

MOVE_CLOSER
MOVE_BACK
HOLD_STILL
MORE_LIGHT
REDUCE_GLARE
CENTER_DOCUMENT
SHOW_ALL_CORNERS
RECAPTURE

Do not send clearly unusable images to OCR.

# 3. ADD BEST-FRAME CAPTURE

For live camera capture:

Capture a short sequence of frames.

Score every candidate using:

sharpness
glare
brightness
document coverage
perspective
text-region sharpness

Automatically select the best frame.

Do not simply process the first captured frame.

# 4. KHMER OCR IMPROVEMENT

Khmer OCR is a major priority.

Do not OCR the complete document image as one large text block.

Pipeline:

Original image
↓
Detect document
↓
Perspective correction
↓
Rotate correctly
↓
Normalize document dimensions
↓
Detect document layout
↓
Crop individual fields
↓
Create multiple preprocessing versions
↓
OCR each version
↓
Vote/compare readings
↓
Normalize Khmer
↓
Validate field
↓
Assign confidence

Fields can include:

Khmer full name
Latin full name
ID number
date of birth
sex
nationality
birthplace
address
issue date
expiry date

For each crop produce approximately:

original normalized crop
2x or 3x upscale
CLAHE grayscale
security-background-suppressed version where beneficial

Avoid aggressive thresholding that destroys Khmer vowel/subscript marks.

Use Khmer OCR specifically for Khmer fields.

Use Latin OCR for Latin fields.

Use restricted digit recognition for numeric fields.

Where Tesseract is used, evaluate:

tessdata_best Khmer
appropriate PSM such as single-line mode for field crops

Keep:

raw_value
normalized_value
match_key
confidence
bounding_box
OCR engine version
normalization version

Never overwrite the raw OCR value.

# 5. KHMER NORMALIZATION

Implement robust Khmer Unicode normalization.

Handle:

Unicode NFC
zero-width characters
deprecated Khmer characters
split vowels
Khmer character ordering
Khmer numerals ០១២៣៤៥៦៧៨៩ → 0123456789
spacing
field-specific formatting

Do not "correct" a Khmer name merely because a dictionary contains a similar spelling.

Any uncertain transformation must reduce confidence or trigger REVIEW.

# 6. OCR VALIDATION

Validate extracted fields.

Examples:

DOB must be a valid historical date.

Expiry must be a valid date.

Sex must match expected document vocabulary.

Document number must match expected format.

Province/district/commune fields may be checked against authoritative lists.

Validation may increase confidence but must never rewrite identity information without recording the transformation.

# 7. MRZ

Support ICAO MRZ:

TD1
TD2
TD3

Validate:

document number check digit
DOB check digit
expiry check digit
optional data check digit
composite check digit

Compare:

MRZ ↔ OCR visual zone

Check:

name
document number
DOB
sex
expiry
nationality

Do not consider a valid MRZ proof that the document is genuine.

Generate signals such as:

MRZ_DOCUMENT_NUMBER_MISMATCH
MRZ_DOB_MISMATCH
MRZ_EXPIRY_MISMATCH
MRZ_NAME_MISMATCH
MRZ_CHECK_DIGIT_FAILED

# 8. DOCUMENT AUTHENTICITY ENGINE

Build or improve a separate document-authenticity analysis layer.

This should produce evidence, not make the final decision.

Evaluate, where technically feasible:

template alignment
expected document layout
expected field regions
portrait location
font consistency
text spacing
background pattern consistency
edge consistency
image manipulation indicators
copy/paste regions
compression anomalies
metadata/editor indicators
screen reproduction
print reproduction
moiré patterns
portrait replacement indicators
barcode consistency
QR consistency
MRZ consistency
chip consistency

Return structured signals with:

code
severity
confidence
evidence
engine_version

Do NOT claim unsupported forensic capabilities.

Unsupported checks must be explicitly represented as NOT_SUPPORTED rather than PASS.

# 9. DOCUMENT PORTRAIT EXTRACTION

Extract the portrait from:

1. verified ePassport chip DG2 when available
2. passport data page
3. National ID portrait

Run:

face detection
portrait quality
orientation correction
face landmark detection
alignment

Reject weak document portraits instead of performing unreliable face comparison.

# 10. SELFIE QUALITY

Require:

exactly one face
face centered
sufficient face size
eyes visible
reasonable pose
adequate lighting
low blur
no major occlusion

Return user-friendly guidance:

MOVE_CLOSER
MOVE_BACK
CENTER_FACE
MORE_LIGHT
HOLD_STILL
REMOVE_OCCLUSION
LOOK_AT_CAMERA

# 11. LIVENESS

Keep liveness separate from face matching.

Face similarity must never count as liveness.

Use a layered liveness pipeline:

face tracking
randomized challenge
head-pose verification
motion continuity
3D geometry where available
texture anti-spoof
screen replay detection
printed-photo detection
video replay detection
virtual-camera/injection signals
frame consistency
same-person-across-frames checking

Never use only blinking as proof of liveness.

Challenges must be:

random
single use
short-lived
bound to one KYC session

Example challenge:

TURN_LEFT
TURN_RIGHT
LOOK_UP

or another unpredictable sequence.

Raw liveness frames should preferably be processed in memory and discarded unless retention is explicitly required.

# 12. FACE MATCHING

This is 1:1 verification only.

Compare:

document portrait
vs
live selfie/liveness identity

Never perform 1:N face search without a separate architecture, legal basis, and security review.

Use:

Face detector
↓
landmarks
↓
alignment
↓
same embedding model
↓
template A
template B
↓
cosine similarity
↓
calibrated decision bands

Do NOT use arbitrary rules like:

80% = same person

The threshold must be calibrated.

# 13. FACE CALIBRATION

Create a calibration framework.

Dataset should contain genuine pairs:

person A ID ↔ person A selfie

and impostor pairs:

person A ID ↔ person B selfie

Measure:

FAR
False Acceptance Rate

FRR
False Rejection Rate

TAR
True Acceptance Rate

ROC curve

threshold distributions

Create at least three bands:

MATCH
BORDERLINE
NO_MATCH

Example only:

score >= calibrated_high
→ MATCH

calibrated_low <= score < calibrated_high
→ BORDERLINE / REVIEW

score < calibrated_low
→ NO_MATCH

The actual numbers MUST come from measured data.

Until calibration has been completed:

FACE_MATCH_UNCALIBRATED
→ REVIEW

Never PASS using an uncalibrated threshold.

# 14. TEST CAMBODIAN USERS SPECIFICALLY

Calibration/test data should include, where legally and ethically collected:

different ages
different genders
different lighting
glasses
facial hair
old ID photos
current selfies
camera quality differences
different mobile phones
low light
slight pose differences

Measure performance independently for important conditions.

Do not store unnecessary biometric data.

# 15. ePASSPORT NFC MOBILE IMPLEMENTATION

The backend NFC verification already exists or partially exists.

Now verify whether mobile NFC is actually implemented.

If not, implement mobile flows for Android and iPhone.

Flow:

Scan passport MRZ
↓
Request NFC challenge from backend
↓
Open passport chip
↓
PACE if available
otherwise BAC where applicable
↓
Read required LDS data
↓
SOD
DG1
DG2
DG15 where available
↓
perform supported chip-authentication operations
↓
send required evidence to backend
↓
backend independently verifies evidence

Never trust the mobile application's statement:

"NFC verified"

The server must make the verification.

# 16. NFC SERVER VERIFICATION

Validate:

SOD CMS SignedData
document signer certificate
trust chain
CSCA
certificate validity
DG hashes
DG1 consistency
DG2 portrait
DG15 where available
challenge/nonces where applicable

Results:

NFC_VERIFIED
NFC_READ
NFC_FAILED
NFC_NOT_AVAILABLE
NFC_NOT_SUPPORTED

NFC_VERIFIED means:

signature verified
trusted chain
hashes valid

Readable NFC alone is not authenticity.

# 17. CSCA TRUST STORE

Production NFC requires a properly maintained issuer trust store.

Build controlled handling for:

CSCA certificates
certificate versions
certificate validity
trust-store version
updates
revocation information where supported

Never silently trust unknown certificates.

Unknown issuer trust:

REVIEW

not PASS.

# 18. CROSS-CHECK ENGINE

Compare every independent source.

Sources:

OCR
MRZ
QR
barcode
NFC DG1
NFC DG2
document portrait
selfie

Examples:

OCR DOB ↔ MRZ DOB

OCR document number ↔ MRZ document number

OCR identity ↔ NFC DG1

passport page portrait ↔ DG2 portrait

document portrait ↔ selfie

Do not silently choose one source when they disagree.

Create explicit fraud/cross-check signals.

# 19. DUPLICATE AND REPLAY DETECTION

Detect, within the legal and tenant-isolated scope:

same document reused unexpectedly
same file hash
same upload replay
same selfie file
excessive verification velocity
repeated failed attempts
capture reuse

Never perform cross-tenant identity searching unless explicitly designed and legally approved.

# 20. RISK ENGINE

The Risk Engine must remain deterministic.

No LLM may produce the final KYC decision.

Input:

document checks
OCR confidence
MRZ validation
barcode checks
NFC
face match
liveness
fraud signals
required verification level
document expiry
document authenticity

Output:

PASS
REVIEW
FAIL

Priority:

FAIL > REVIEW > PASS

PASS only when all required trusted evidence is present.

Unknown or missing evidence:

REVIEW

Strong proven failure:

FAIL

# 21. EXAMPLE FAIL CONDITIONS

Possible hard failures include:

LIVENESS_FAILED

FACE_MISMATCH

EXPIRED_DOCUMENT

CRYPTOGRAPHIC_TAMPER

CLONED_CHIP_CONFIRMED

SIGNED_DATA_INVALID

POLICY_NOT_MET

Do not hard FAIL simply because OCR confidence is low.

Low OCR:

REVIEW or RECAPTURE

# 22. VERIFICATION LEVELS

Preserve/support:

DOCUMENT_ONLY

DOCUMENT_FACE

DOCUMENT_FACE_LIVENESS

DOCUMENT_FACE_LIVENESS_NFC

Each verification level must clearly declare its required checks.

Never PASS a session when the required evidence for the selected level is incomplete.

# 23. RECAPTURE BEFORE REVIEW

For fixable capture problems:

blur
glare
bad angle
too far
document clipped
selfie too dark
face too small

prefer:

RECAPTURE

before:

MANUAL_REVIEW

Only escalate when reasonable automatic recovery has failed.

# 24. MANUAL REVIEW

Improve the manual review dashboard.

Reviewer should see:

document front/back
document portrait
selfie/liveness representative image
OCR results
raw vs normalized values
MRZ
barcode/QR
NFC status
cross-check results
fraud signals
face decision band
quality indicators
risk-engine reasons
verification timeline

Reviewer actions:

APPROVE
REJECT
REQUEST_RECAPTURE

Every decision requires:

reason_code
reviewer note
reviewer ID
timestamp
case/session version

Use optimistic concurrency.

If case changed:

409 CASE_CHANGED

# 25. REVIEWER SECURITY

Reviewers must have separate authentication from customers.

Roles:

REVIEWER

AUDITOR

AUDITOR:

may inspect permitted non-sensitive case information
cannot approve/reject

REVIEWER:

may review permitted evidence
may decide

Customer API keys must never access reviewer endpoints.

# 26. API SECURITY

Ensure:

API keys are hashed
session tokens are short-lived
tokens belong to exactly one session
API key scopes
organization isolation
RLS
rate limiting
idempotency
network allowlists where configured
audit logging
token rotation
revocation
secret rotation

Prevent:

tenant breakout
IDOR
privilege escalation
token replay
session fixation
mass assignment
SQL injection
SSRF
unsafe file upload
path traversal

# 27. DATA PRIVACY

Sensitive information includes:

ID photos
passport images
names
document numbers
DOB
addresses
selfies
biometric templates
NFC-derived identity information

Protect using:

TLS
encryption at rest
field-level encryption where appropriate
KMS-managed keys
strict access policies
minimal retention
audit logging

EXIF GPS metadata should be removed.

Do not return biometric templates in APIs.

# 28. DATA ERASURE

Maintain:

POST /v1/kyc/{id}/erase

Erase personal information according to policy:

document images
selfies
extracted identity fields
biometric templates
NFC-derived personal information
reviewer notes containing personal information

Keep only what policy/legal obligations permit, such as minimal audit state/reason codes where appropriate.

# 29. WEBHOOK SECURITY

Use:

HTTPS only
HMAC-SHA256 signatures
timestamps
event IDs
deduplication
secret rotation
retry policy
delivery logs

Prevent webhook SSRF.

Do not follow redirects to internal/private networks.

Never put sensitive identity/biometric content into webhook payloads unless explicitly required and securely designed.

# 30. ASYNC ARCHITECTURE

Heavy work must not block the public API.

Separate:

API workers

OCR workers

document processing workers

webhook workers

Potential architecture:

Client
↓
Load Balancer
↓
FastAPI
↓
Queue
├── OCR Worker
├── Document Worker
├── Fraud Worker
└── Webhook Worker
↓
PostgreSQL

Use Redis/Pub/Sub/task infrastructure only where it improves reliability and does not compromise transaction correctness.

Use safe job claiming such as:

SELECT ... FOR UPDATE SKIP LOCKED

or an appropriate durable queue.

# 31. GCP PRODUCTION ARCHITECTURE

Prepare deployment architecture using appropriate managed GCP services.

Target architecture may include:

Internet
↓
Google Cloud Load Balancer
↓
Cloud Armor
↓
KYC API
↓
Cloud Run or GKE depending on workload
↓
worker services

Storage/services:

Cloud SQL PostgreSQL
Memorystore Redis when needed
Cloud Storage
Cloud KMS
Secret Manager
Pub/Sub
Artifact Registry
Cloud Logging
Cloud Monitoring

Use private networking where appropriate.

Do not expose PostgreSQL publicly.

# 32. CLOUD KMS

Move production encryption keys to Cloud KMS.

Separate key purposes.

Examples:

identity field encryption
capture encryption
biometric encryption
webhook-secret encryption

Support key rotation.

No hard-coded production keys.

# 33. OBSERVABILITY

Implement:

structured logging
request ID
session ID
organization ID
worker job ID
latency
error type
decision reason
queue length
OCR latency
face-processing latency
NFC verification latency

Never log:

raw API keys
reviewer tokens
full ID numbers
biometric templates
full document images
secret keys

# 34. METRICS

Add metrics such as:

sessions_created

sessions_verified

sessions_review

sessions_rejected

recapture_rate

OCR_failure_rate

OCR_latency

Khmer_OCR_confidence

selfie_failure_rate

liveness_failure_rate

face_borderline_rate

NFC_success_rate

NFC_failure_rate

manual_review_rate

queue_depth

API_latency

worker_latency

webhook_failure_rate

# 35. PERFORMANCE

Benchmark separately:

session APIs

status polling

document upload

document processing

OCR

face embedding

liveness

NFC verification

risk engine

manual review

webhooks

Test:

1 user

10 users

50 users

100 users

250 users

500 users

and appropriate higher concurrency where infrastructure permits.

Measure:

RPS

p50

p95

p99

CPU

memory

DB connections

queue depth

failure rate

Do not sacrifice verification quality merely for throughput.

# 36. TESTING

Create or improve:

unit tests
integration tests
database tests
RLS tests
API tests
security tests
tenant-isolation tests
OCR tests
MRZ tests
barcode tests
face tests
liveness tests
NFC tests
fraud tests
risk-engine tests
manual-review tests
webhook tests
load tests

# 37. GOLDEN KYC TEST SET

Create structured test fixtures for:

Cambodia National ID

Cambodia NSSF

Cambodia Passport

Test variants:

clean
blurred
glare
dark
far away
perspective distortion
compressed
screenshot
rephotographed
cropped
wrong side
wrong document
expired
OCR disagreement

Expected outputs must be explicit.

# 38. REAL-WORLD VALIDATION

Automated unit tests alone are not enough.

Create a validation checklist requiring real testing with:

real Android devices
real iPhones
real Cambodian ID captures
real NSSF samples where legally available
real ePassports where legally permitted
different camera quality
different lighting
real people for face calibration

Do not declare a capability production-ready if it has only been tested using mocks.

# 39. PRODUCTION READINESS GATES

Before production, verify:

[ ] Khmer OCR benchmark completed

[ ] Khmer OCR acceptable error rate defined

[ ] document capture thresholds calibrated

[ ] face threshold calibrated

[ ] FAR measured

[ ] FRR measured

[ ] liveness tested against basic spoof attacks

[ ] Android NFC physically tested

[ ] iPhone NFC physically tested

[ ] real passport chip tested

[ ] production CSCA trust strategy ready

[ ] API security review completed

[ ] tenant isolation verified

[ ] encryption verified

[ ] KMS configured

[ ] backup tested

[ ] restore tested

[ ] load test completed

[ ] monitoring configured

[ ] alerting configured

[ ] webhook retry tested

[ ] erasure tested

[ ] production secrets separated from development

[ ] Docker/container build verified

[ ] disaster recovery documented

# 40. PRODUCTION APPROVAL RULE

Create a final automated/readable report:

PRODUCTION READY
PRODUCTION READY WITH LIMITATIONS
NOT PRODUCTION READY

Do not mark the project PRODUCTION READY while a critical item remains untested.

Critical blockers include:

uncalibrated face matching

unvalidated liveness

fake PASS caused by missing evidence

broken tenant isolation

unencrypted biometrics

unverified production key management

real NFC flow never tested while NFC is advertised as supported

risk-engine bypass

document authenticity falsely claimed

# 41. DO NOT FAKE CAPABILITIES

If a feature is not implemented, say:

NOT_IMPLEMENTED

If it exists but uses mock data:

MOCK_ONLY

If implemented but not calibrated:

UNCALIBRATED

If implemented but only tested synthetically:

NOT_REAL_WORLD_VALIDATED

If unsupported:

NOT_SUPPORTED

Never label these as production-ready.

# 42. PRESERVE CURRENT ARCHITECTURE

Do not rewrite the whole application.

Prefer incremental improvements.

For every major modification:

1. inspect current implementation
2. identify the gap
3. explain the risk
4. implement the fix
5. add/update migration if required
6. add tests
7. run tests
8. report result

Avoid unnecessary API breaking changes.

# 43. DATABASE CHANGES

Use proper migrations.

Do not manually mutate production schema.

Maintain tenant isolation.

Maintain encryption.

Maintain append-only security/audit tables where required.

# 44. ERROR HANDLING

Use stable reason/error codes.

Examples:

DOCUMENT_BLURRY

DOCUMENT_GLARE

DOCUMENT_TOO_SMALL

WRONG_DOCUMENT

LOW_OCR_CONFIDENCE

MRZ_CHECK_DIGIT_FAILED

FIELD_MISMATCH

SELFIE_LOW_QUALITY

LIVENESS_FAILED

FACE_MATCH_UNCALIBRATED

FACE_SCORE_BORDERLINE

FACE_MISMATCH

NFC_NOT_SUPPORTED

NFC_READ_FAILED

NFC_CERT_UNTRUSTED

NFC_TAMPER_DETECTED

DOCUMENT_AUTHENTICITY_UNVERIFIED

EVIDENCE_MISSING

MANUAL_REVIEW_REQUIRED

Do not expose internal model thresholds to end users.

# 45. FINAL DELIVERABLE

When finished, produce:

KYC PRODUCTION READINESS REPORT

Include:

1. Existing architecture reviewed
2. Components that were already correct
3. Problems discovered
4. Changes implemented
5. Security changes
6. OCR improvements
7. Face calibration status
8. Liveness status
9. NFC status
10. Fraud/authenticity status
11. Infrastructure status
12. Performance results
13. Test results
14. Remaining limitations
15. Production blockers
16. Recommended next actions
17. Final readiness classification

Use this final classification:

READY
READY_WITH_LIMITATIONS
NOT_READY

Also include a table:

Component | Status | Real Tested | Calibrated | Production Ready | Notes

for:

Document Capture
KH National ID
KH NSSF
KH Passport
Khmer OCR
MRZ
QR/Barcode
Document Authenticity
Portrait Extraction
Selfie
Face Match
Liveness
Android NFC
iPhone NFC
Passive Authentication
CSCA Verification
Fraud Detection
Risk Engine
Manual Review
Tenant Isolation
Encryption
Webhooks
GCP
Monitoring
Backup/Restore

# MOST IMPORTANT PRINCIPLE

Accuracy and security are more important than demonstrating that a feature "works."

Never manufacture confidence.

Never invent identity information.

Never automatically approve uncertain evidence.

Never allow an LLM to determine identity verification.

A secure REVIEW is better than a false PASS.

A production KYC system must distinguish clearly between:

IMPLEMENTED

TESTED

CALIBRATED

REAL-WORLD VALIDATED

PRODUCTION READY

Start by auditing the existing repository and produce the current readiness matrix before changing major architecture.