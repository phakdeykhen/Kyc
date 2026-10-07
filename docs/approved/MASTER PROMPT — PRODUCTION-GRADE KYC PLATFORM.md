MASTER PROMPT — PRODUCTION-GRADE KYC PLATFORM

You are a Senior KYC Architect, Identity Verification Engineer, Biometrics Engineer, OCR Engineer, Security Engineer, Fraud Engineer, Mobile Engineer, Backend Engineer, and DevOps Engineer.

I already have an existing KYC platform.

DO NOT rebuild the project from scratch.

Your job is to audit the existing implementation, preserve what already works, fix weak/incomplete components, add missing production capabilities, calibrate the system using real-world testing, and produce a complete production-readiness report.

The system must support strong identity verification for:

Cambodia National ID
Cambodia NSSF
Cambodia Passport
Generic Passport
Generic National ID
Residence Card
Driving License
Future country adapters

The system must perform:

Document capture
Document quality
Document classification
Khmer OCR
Latin OCR
MRZ
QR / barcode
Document authenticity analysis
Document portrait extraction
Mobile face verification
Guided liveness
1:1 face matching
ePassport NFC
Cross-checking
Fraud detection
Risk decision
Manual review
Signed result API
Signed webhooks
Audit logs
Security
Encryption
Production monitoring

--------------------------------------------------
CORE SECURITY PRINCIPLE
--------------------------------------------------

Never fake confidence.

Never silently guess identity data.

Never convert missing evidence into PASS.

Never allow an LLM to make identity decisions.

Never let face similarity substitute for liveness.

Never let liveness substitute for identity.

Never let readable OCR prove document authenticity.

Never let readable NFC alone prove authenticity.

Unknown / uncertain evidence:
→ REVIEW

Recoverable capture problem:
→ RECAPTURE

Strong verified failure:
→ FAIL

Technical system error:
→ TECHNICAL_ERROR

Do not reject a real user merely because an internal service crashed.

--------------------------------------------------
FINAL KYC FLOW
--------------------------------------------------

User
↓
Create Session
↓
Consent
↓
Capture ID
↓
Document Quality Gate
↓
Document Classification
↓
Perspective Correction
↓
Field Detection
↓
Khmer / Latin OCR
↓
MRZ / QR / Barcode
↓
Document Authenticity
↓
Extract Document Portrait
↓
Mobile Phone Handoff
↓
QR Code
↓
Phone Camera
↓
Face Positioning
↓
Guided Liveness
↓
Best Live Face Frame
↓
Face Embedding
↓
ID Portrait vs Live Face
↓
Optional Passport NFC
↓
Cross-Source Verification
↓
Fraud Analysis
↓
Risk Engine
↓
PASS / REVIEW / FAIL
↓
Manual Review if required
↓
Safe API Result
↓
Signed Webhook

--------------------------------------------------
1. AUDIT EXISTING PROJECT FIRST
--------------------------------------------------

Before making major changes inspect:

project structure
database
migrations
models
API routes
authentication
session state machine
document adapters
OCR
MRZ
barcode
QR
portrait extraction
face detection
face embeddings
liveness
NFC
fraud engine
risk engine
review dashboard
webhooks
workers
storage
encryption
Docker
cloud config
tests
load tests
SDKs
frontend
mobile flow

Classify every component:

COMPLETE
PARTIAL
MOCK_ONLY
UNCALIBRATED
NOT_REAL_WORLD_TESTED
NOT_SUPPORTED
SECURITY_RISK
PERFORMANCE_RISK
PRODUCTION_BLOCKER

Do not modify large parts of the architecture before this audit.

--------------------------------------------------
2. DOCUMENT CAPTURE
--------------------------------------------------

For every captured document verify:

all four corners visible
document sufficiently large
correct focus
low blur
acceptable glare
acceptable brightness
no major shadows
acceptable perspective
correct orientation
good resolution
not heavily compressed
not multiple documents
correct front/back/data page

Return instructions:

MOVE_CLOSER
MOVE_BACK
CENTER_DOCUMENT
SHOW_ALL_CORNERS
HOLD_STILL
REDUCE_GLARE
MORE_LIGHT
ROTATE_DOCUMENT
RECAPTURE

Do not send unusable images to OCR.

--------------------------------------------------
3. BEST FRAME DOCUMENT CAPTURE
--------------------------------------------------

When camera mode is available:

capture multiple frames

Score:

sharpness
glare
brightness
coverage
perspective
text sharpness

Select the strongest frame automatically.

Do not process only the first frame.

--------------------------------------------------
4. PERSPECTIVE NORMALIZATION
--------------------------------------------------

Detect document edges.

Correct perspective.

Normalize the card to fixed reference dimensions.

Keep:

original image
normalized image

Do not destroy original evidence.

--------------------------------------------------
5. DOCUMENT CLASSIFICATION
--------------------------------------------------

Detect:

country
document_type
document_side
document_version
confidence

Support:

KH_NATIONAL_ID
KH_NSSF
KH_PASSPORT
PASSPORT
NATIONAL_ID
RESIDENCE_CARD
DRIVING_LICENSE
UNKNOWN

If expected document differs from detected document:

WRONG_DOCUMENT
→ RECAPTURE

--------------------------------------------------
6. CAMBODIA KHMER OCR — HIGH PRIORITY
--------------------------------------------------

Do NOT OCR the entire Cambodian ID as one text block.

Use document-specific layout detection.

After perspective correction:

Normalized KH National ID
↓
Detect field ROIs
↓
Crop each field independently

Important fields:

Khmer name
Latin name
DOB
Sex
Document number
Address
Place of birth
Issue date
Expiry
Other supported fields

For every Khmer field produce several preprocessing variants:

original crop
2x upscale
3x upscale
CLAHE grayscale
mild sharpening
security-pattern suppression if useful
mild denoise

Avoid aggressive binary thresholding because it destroys Khmer:

subscripts
vowels
small marks
stacked characters

Use Khmer-specific OCR for Khmer fields.

Evaluate:

Tesseract 5
tessdata_best Khmer

For single-line fields evaluate:

--psm 7

Do not blindly use khm+eng on the entire card.

--------------------------------------------------
7. KHMER NAME — CRITICAL FIX
--------------------------------------------------

For Cambodia National ID:

The Khmer name field must have its own dedicated ROI.

The Khmer name is independent from the Latin name.

Do not use MRZ Latin name as a replacement for Khmer name.

Pipeline:

normalized card
↓
Khmer name ROI
↓
preprocessing variants
↓
Khmer OCR multiple passes
↓
candidate voting
↓
Khmer Unicode normalization
↓
confidence
↓
PASS / RECAPTURE / REVIEW

If empty:

KHMER_NAME_NOT_DETECTED

If corrupted:

KHMER_NAME_LOW_CONFIDENCE

If confidence remains below calibrated threshold:

RECAPTURE

After reasonable recapture attempts:

MANUAL_REVIEW

Never:

empty Khmer field → PASS

--------------------------------------------------
8. KHMER NORMALIZATION
--------------------------------------------------

Implement:

Unicode NFC
remove invalid zero-width characters
normalize whitespace
deprecated Khmer character normalization
split vowel handling
Khmer ordering rules
Khmer numerals ០១២៣៤៥៦៧៨៩ → 0123456789

Keep:

raw_value
normalized_value
match_key
normalization_steps
confidence
normalizer_version

Never overwrite raw OCR.

--------------------------------------------------
9. OCR MULTI-PASS VOTING
--------------------------------------------------

For each field:

variant A
variant B
variant C
variant D

Run OCR independently.

Compare results.

If several independent reads agree strongly:

increase trust.

If outputs disagree:

FIELD_UNCERTAIN

Do not simply choose the highest confidence OCR result blindly.

--------------------------------------------------
10. PLACE OF BIRTH / ADDRESS
--------------------------------------------------

Do not treat long Khmer address strings as one unvalidated field.

Parse into:

province
district
commune
village

Validate against authoritative Cambodian geography datasets.

Example:

Province: កំពង់ឆ្នាំង
District: ទឹកផុស
Commune: ...
Village: ...

If OCR says garbage or mixed random Latin characters:

OCR_NOISE_DETECTED
LOW_OCR_CONFIDENCE

→ RECAPTURE or REVIEW

Never guess the correct location based only on fuzzy similarity.

--------------------------------------------------
11. FIELD VALIDATION
--------------------------------------------------

Validate:

date formats
impossible dates
expiry
document number format
sex vocabulary
nationality
province
district
commune

Validation may increase confidence.

Validation must not silently rewrite identity data.

--------------------------------------------------
12. MRZ
--------------------------------------------------

Support ICAO:

TD1
TD2
TD3

Verify:

document number check digit
DOB check digit
expiry check digit
optional data
composite check digit

Extract:

document number
name
DOB
sex
expiry
nationality

Compare:

MRZ ↔ visual OCR

Possible signals:

MRZ_NAME_MISMATCH
MRZ_DOB_MISMATCH
MRZ_DOCUMENT_NUMBER_MISMATCH
MRZ_EXPIRY_MISMATCH
MRZ_CHECK_DIGIT_FAILED

Valid MRZ is evidence.

It is NOT proof of authentic document.

--------------------------------------------------
13. QR / BARCODE
--------------------------------------------------

Decode where present.

Support where appropriate:

QR
PDF417
Data Matrix
Aztec

Validate:

format
checksum
payload
signature if official mechanism exists

Compare payload with OCR.

Readable QR is not automatically authentic.

--------------------------------------------------
14. DOCUMENT AUTHENTICITY ENGINE
--------------------------------------------------

Create an independent authenticity layer.

Potential checks where technically supported:

expected template
document dimensions
layout consistency
field positions
portrait location
font/style anomalies
spacing
security pattern
edge consistency
metadata
editing software indicators
copy/paste regions
compression anomalies
portrait replacement
screen reproduction
print reproduction
moiré
barcode consistency
MRZ consistency
chip consistency

Every capability must have:

PASS
FAIL
REVIEW
NOT_SUPPORTED
NOT_APPLICABLE
NOT_RUN
ERROR

Never mark unsupported checks as PASS.

--------------------------------------------------
15. DOCUMENT PORTRAIT EXTRACTION
--------------------------------------------------

Reference priority:

verified passport DG2 portrait
passport portrait
national ID portrait

Pipeline:

portrait crop
↓
face detection
↓
quality
↓
landmarks
↓
alignment
↓
Template A

If portrait unusable:

DOCUMENT_PORTRAIT_UNUSABLE
→ RECAPTURE

Do not perform weak face matching.

--------------------------------------------------
16. FACE VERIFICATION MUST USE PHONE
--------------------------------------------------

Production face verification must always use the user's phone.

Desktop camera must not be the default production biometric capture.

Desktop flow:

ID scanned
↓
Face verification required
↓
Generate QR
↓
"Continue on your phone"
↓
Phone claims session
↓
Phone camera opens
↓
Guided liveness
↓
Phone completes
↓
Desktop continues automatically

--------------------------------------------------
17. QR HANDOFF
--------------------------------------------------

QR contains only a short-lived URL/token.

Never include:

API key
password
PII
organization secret
permanent token

Example concept:

https://verify.example.com/mobile?t=<SHORT_LIVED_TOKEN>

Token must be:

random
single-use
short-lived
session-bound
organization-bound
step-bound

Expire approximately:

2–5 minutes

Invalidate after:

claim
expiration
cancel
new QR generation

--------------------------------------------------
18. MOBILE CLAIM
--------------------------------------------------

Phone opens QR.

Backend:

validate QR token
↓
claim session
↓
invalidate QR token
↓
issue short-lived mobile capture token

If token reused:

QR_ALREADY_USED

If another device already owns capture:

MOBILE_SESSION_ALREADY_CLAIMED

--------------------------------------------------
19. DESKTOP LIVE STATUS
--------------------------------------------------

Desktop must update automatically.

Use:

WebSocket
or SSE

Fallback:

polling

Possible states:

WAITING_FOR_PHONE
PHONE_CONNECTED
CAMERA_PERMISSION_REQUIRED
FACE_POSITIONING
LIVENESS_IN_PROGRESS
FACE_PROCESSING
COMPLETED
FAILED
EXPIRED

No manual refresh.

--------------------------------------------------
20. MOBILE CAMERA UI
--------------------------------------------------

Create clean, modern, original UI.

Inspired by modern verification flows but do NOT copy Google's branding or exact UI.

Requirements:

full-screen camera
large face guide
one instruction at a time
large readable text
progress indicator
clear success state
dark or clean neutral design
responsive mobile layout

Support:

iPhone Safari
Android Chrome
Samsung Internet

--------------------------------------------------
21. FACE POSITIONING
--------------------------------------------------

Before liveness:

exactly one face
centered
adequate face size
eyes visible
acceptable pose
good lighting
low blur
no major occlusion

Instructions:

CENTER_YOUR_FACE
MOVE_CLOSER
MOVE_BACK
MORE_LIGHT
LOOK_STRAIGHT
HOLD_STILL
REMOVE_OCCLUSION

Do not begin liveness until quality conditions are satisfied.

--------------------------------------------------
22. GUIDED LIVENESS
--------------------------------------------------

Use server-generated random challenges.

Example actions:

CENTER
TURN_LEFT
TURN_RIGHT
LOOK_UP
RETURN_CENTER

Each session should receive an unpredictable sequence.

Example:

CENTER
RIGHT
CENTER
LEFT

or:

CENTER
LEFT
CENTER
UP

Never expose all future steps in advance.

--------------------------------------------------
23. LIVENESS MUST BE ACTION-BASED, NOT TIMER-BASED
--------------------------------------------------

If instruction says:

TURN_LEFT

the system must remain on that instruction until the camera actually detects the correct motion.

Wrong:

show TURN_LEFT
wait 2 seconds
continue

Correct:

show TURN_LEFT
↓
measure head pose
↓
detect correct yaw
↓
verify same person
↓
require stable pose
↓
step complete
↓
next instruction

--------------------------------------------------
24. HEAD POSE
--------------------------------------------------

Estimate:

yaw
pitch
roll

Use calibrated pose thresholds.

Do not invent arbitrary final production thresholds.

Require pose stability across multiple frames or a short calibrated duration.

Do not accept one noisy frame.

--------------------------------------------------
25. LIVENESS SECURITY
--------------------------------------------------

Check:

face tracking
motion continuity
challenge correctness
same person across frames
flat photo indicators
printed photo
screen replay
video replay
virtual camera/injection signals where supported
AI-generated media where supported
3D geometry where supported

Do not use blink-only liveness.

--------------------------------------------------
26. SAME PERSON THROUGH ENTIRE LIVENESS
--------------------------------------------------

The face must remain the same identity through the entire challenge.

If person changes:

LIVENESS_IDENTITY_CHANGED

→ FAIL or controlled restart according to policy

If multiple faces:

MULTIPLE_FACES
→ pause/retry

--------------------------------------------------
27. BEST LIVE FACE FRAME
--------------------------------------------------

During successful liveness collect valid frames.

Rank using:

sharpness
lighting
frontal pose
face size
eyes visible
occlusion
landmark confidence

Choose the best valid frame.

This becomes:

Template B

Do not use the first frame automatically.

--------------------------------------------------
28. FACE ALIGNMENT
--------------------------------------------------

Both faces must use same pipeline.

ID:

detect
↓
landmarks
↓
align
↓
same input size
↓
embedding model
↓
Template A

Live:

detect
↓
same landmarks
↓
same alignment
↓
same model
↓
Template B

Different incompatible model versions:

refuse comparison

--------------------------------------------------
29. 1:1 FACE MATCH
--------------------------------------------------

Only compare:

ID portrait
vs
live phone liveness face

This is 1:1 verification.

Do not implement 1:N face search inside normal KYC.

Calculate appropriate similarity metric.

Possible result:

MATCH
BORDERLINE
NO_MATCH
UNCALIBRATED
ERROR

Never display raw thresholds to end users.

--------------------------------------------------
30. FACE CALIBRATION
--------------------------------------------------

Do not use arbitrary:

80% = same person

Create calibration dataset.

Collect legally/ethically:

genuine pairs:
person A ID ↔ person A selfie

impostor pairs:
person A ID ↔ person B selfie

Test:

different age
glasses
facial hair
old ID photo
new selfie
different phones
different lighting
low light
pose
camera quality

Measure:

FAR
FRR
TAR
ROC
genuine distribution
impostor distribution

Use calibrated bands:

MATCH
BORDERLINE
NO_MATCH

Until calibration complete:

FACE_MATCH_UNCALIBRATED
→ REVIEW

--------------------------------------------------
31. PASSPORT NFC
--------------------------------------------------

Mobile flow:

scan MRZ
↓
request server challenge
↓
phone NFC
↓
PACE or BAC
↓
read:
SOD
DG1
DG2
DG15 if available
↓
send evidence to backend
↓
backend verifies independently

Do not trust client-side statement:

"NFC verified"

--------------------------------------------------
32. NFC SERVER VERIFICATION
--------------------------------------------------

Verify:

SOD CMS SignedData
messageDigest
Document Signer
CSCA trust
certificate validity
data group hashes
DG1 consistency
DG2 portrait
DG15 / Active Authentication where supported
Chip Authentication where supported

Statuses:

NFC_VERIFIED
NFC_READ
NFC_FAILED
NFC_NOT_AVAILABLE
NFC_NOT_SUPPORTED

--------------------------------------------------
33. PASSPORT FACE COMPARISON
--------------------------------------------------

If NFC DG2 is cryptographically verified:

prefer DG2 portrait as Template A.

Then:

DG2 verified portrait
vs
live liveness face

This is stronger than printed passport portrait alone.

--------------------------------------------------
34. CSCA TRUST STORE
--------------------------------------------------

Maintain:

trusted CSCA certificates
versioning
validity
updates
revocation where supported
trust store version

Unknown certificate:

REVIEW

Never silently trust unknown issuer.

--------------------------------------------------
35. CROSS-CHECK ENGINE
--------------------------------------------------

Compare every independent source.

OCR
↔
MRZ
↔
QR
↔
Barcode
↔
NFC DG1
↔
NFC DG2
↔
Document portrait
↔
Live face

Compare:

name
DOB
document number
sex
expiry
nationality

Never silently choose one source if they disagree.

Create explicit signals.

--------------------------------------------------
36. DUPLICATE / REPLAY DETECTION
--------------------------------------------------

Detect within allowed tenant scope:

same document reused
same file hash
same upload replay
same selfie replay
same document image
velocity anomaly
repeated failures
capture reuse

Do not perform cross-tenant identity searching unless explicitly designed and legally approved.

--------------------------------------------------
37. RISK ENGINE
--------------------------------------------------

The Risk Engine must be deterministic.

Inputs:

document quality
classification
OCR
MRZ
barcode
NFC
document authenticity
portrait quality
face match
liveness
fraud signals
cross-checks
expiry
verification level

Final:

PASS
REVIEW
FAIL

Priority:

FAIL > REVIEW > PASS

--------------------------------------------------
38. PASS CONDITIONS
--------------------------------------------------

PASS only when all evidence required by verification level is present and acceptable.

Example:

Document authentic enough
+
Face MATCH
+
Liveness PASS
+
Required NFC PASS if policy requires it
+
No high-risk fraud
+
No unresolved critical mismatch

→ PASS

--------------------------------------------------
39. REVIEW CONDITIONS
--------------------------------------------------

Examples:

LOW_OCR_CONFIDENCE
FIELD_MISMATCH
FACE_SCORE_BORDERLINE
FACE_MATCH_UNCALIBRATED
DOCUMENT_AUTHENTICITY_UNVERIFIED
UNKNOWN_CERTIFICATE
EVIDENCE_MISSING
UNSUPPORTED_CRITICAL_CHECK

→ REVIEW

--------------------------------------------------
40. FAIL CONDITIONS
--------------------------------------------------

Examples:

LIVENESS_FAILED
FACE_MISMATCH
EXPIRED_DOCUMENT
CRYPTOGRAPHIC_TAMPER
SIGNED_DATA_INVALID
CLONED_CHIP_CONFIRMED
HIGH_RISK_TAMPER_SIGNAL
PRESENTATION_ATTACK_CONFIRMED

--------------------------------------------------
41. TECHNICAL ERROR
--------------------------------------------------

Examples:

OCR_TIMEOUT
DATABASE_FAILURE
MODEL_UNAVAILABLE
QUEUE_FAILURE
STORAGE_FAILURE
KMS_FAILURE
NETWORK_ERROR

These are not identity FAIL.

Return:

TECHNICAL_ERROR
or retry where safe.

--------------------------------------------------
42. RECAPTURE POLICY
--------------------------------------------------

Use RECAPTURE for:

blur
glare
dark image
bad angle
cropped document
face too small
poor selfie
bad framing
temporary capture problem

Prefer RECAPTURE before MANUAL_REVIEW when problem is recoverable.

--------------------------------------------------
43. VERIFICATION LEVELS
--------------------------------------------------

Support:

DOCUMENT_ONLY

DOCUMENT_FACE

DOCUMENT_FACE_LIVENESS

DOCUMENT_FACE_LIVENESS_NFC

Each level must declare required checks.

Never PASS with missing required checks.

--------------------------------------------------
44. ADVANCED RESULT OBJECT
--------------------------------------------------

Create a full internal result.

Example structure:

{
  "session_id": "...",
  "status": "VERIFIED",
  "decision": "PASS",
  "verification_level": "DOCUMENT_FACE_LIVENESS",

  "overall": {
    "decision": "PASS",
    "risk_level": "LOW",
    "reason_codes": [
      "DOCUMENT_VALID",
      "FACE_MATCH",
      "LIVENESS_PASS"
    ]
  },

  "document": {
    "type": "KH_NATIONAL_ID",
    "country": "KH",
    "classification": {},
    "quality": {},
    "ocr": {},
    "mrz": {},
    "barcode": {},
    "authenticity": {},
    "expiry": {}
  },

  "identity": {},

  "biometric": {
    "document_portrait_quality": {},
    "live_face_quality": {},
    "face_match": {}
  },

  "liveness": {},

  "nfc": {},

  "fraud": {},

  "cross_check": {},

  "review": {},

  "system": {
    "processing_time_ms": 0,
    "policy_version": "...",
    "ocr_engine_version": "...",
    "face_model_version": "...",
    "liveness_model_version": "...",
    "document_adapter_version": "..."
  }
}

--------------------------------------------------
45. RESULT STATUS PER CHECK
--------------------------------------------------

Every check uses:

PASS
FAIL
REVIEW
NOT_SUPPORTED
NOT_APPLICABLE
NOT_RUN
ERROR

Never:

NOT_RUN → PASS

--------------------------------------------------
46. FIELD-LEVEL RESULT
--------------------------------------------------

Store:

field name
raw_value
normalized_value
confidence
bounding_box
source
validation result
consistency result

Example:

{
  "field": "khmer_name",
  "raw_value": "...",
  "normalized_value": "...",
  "confidence": 0.94,
  "source": "VISUAL_OCR_FRONT",
  "validation": "PASS"
}

--------------------------------------------------
47. INTERNAL VS CLIENT VS END-USER RESULT
--------------------------------------------------

INTERNAL:
full technical evidence

CLIENT API:
safe decision + approved fields

END USER:
simple message

PASS:
Identity verification completed successfully.

REVIEW:
Your verification has been submitted for additional review.

FAIL:
We could not verify your identity.

TECHNICAL:
We couldn't complete verification right now. Please try again.

Do not accuse users of fraud in UI.

--------------------------------------------------
48. MANUAL REVIEW DASHBOARD
--------------------------------------------------

Reviewer sees:

front/back
passport page
document portrait
best live frame
OCR raw
OCR normalized
confidence
MRZ
QR/barcode
NFC
face band
liveness
fraud signals
cross-checks
decision trace
timeline
quality indicators

Actions:

APPROVE
REJECT
REQUEST_RECAPTURE

Require:

reason code
reviewer note
reviewer ID
timestamp
case version

Use optimistic concurrency:

CASE_CHANGED → 409

--------------------------------------------------
49. REVIEWER SECURITY
--------------------------------------------------

Separate customer API auth from reviewer auth.

Roles:

REVIEWER
AUDITOR

AUDITOR:
view permitted results only
cannot decide

REVIEWER:
view permitted evidence
approve/reject/recapture

Customer API keys must never access reviewer endpoints.

--------------------------------------------------
50. API SECURITY
--------------------------------------------------

Require:

hashed API keys
short-lived session tokens
per-session client token
scopes
organization isolation
RLS
rate limiting
idempotency
network restrictions where configured
audit logging
rotation
revocation

Protect against:

IDOR
cross-tenant access
privilege escalation
replay
session fixation
SQL injection
SSRF
path traversal
unsafe uploads
mass assignment

--------------------------------------------------
51. DATA TRANSFER
--------------------------------------------------

Transfer only necessary data.

Phone → backend:

ID evidence
face/liveness evidence
consent
NFC evidence
short-lived session credentials

Backend → client:

session ID
status
decision
reason codes
approved identity fields if permitted

Webhook:

IDs
status
decision
reason codes
session version

Do not put full biometrics or raw images into normal webhook payloads.

--------------------------------------------------
52. SENSITIVE DATA
--------------------------------------------------

Protect:

ID images
passport images
names
DOB
address
document numbers
selfies
face templates
NFC-derived identity
reviewer notes

Use:

TLS
encryption at rest
field encryption where appropriate
KMS
strict access control
minimal retention

Strip EXIF GPS.

--------------------------------------------------
53. BIOMETRIC STORAGE
--------------------------------------------------

Never expose face templates publicly.

Encrypt templates.

Retention-limit templates.

Prefer to process raw liveness frames in memory.

Discard unnecessary frames after result.

--------------------------------------------------
54. DATA ERASURE
--------------------------------------------------

Support data-erasure workflow.

Erase according to policy:

document images
selfies
identity fields
templates
NFC personal data
reviewer notes containing personal data

Retain only required audit metadata where legally/policy allowed.

--------------------------------------------------
55. WEBHOOK SECURITY
--------------------------------------------------

HTTPS only

HMAC-SHA256
timestamp
event ID
deduplication
secret rotation
retry
delivery logs

Protect against SSRF.

Do not follow redirects to internal/private network.

--------------------------------------------------
56. WORKER ARCHITECTURE
--------------------------------------------------

Heavy operations must not block API.

Separate:

API worker
OCR worker
document worker
fraud worker
webhook worker

Architecture:

Client
↓
API
↓
Queue
├ OCR
├ Document
├ Fraud
└ Webhook

Use safe queue/job locking.

--------------------------------------------------
57. GCP PRODUCTION TARGET
--------------------------------------------------

Recommended architecture:

Internet
↓
Cloud Load Balancer
↓
Cloud Armor
↓
FastAPI
↓
Cloud Run or GKE
↓
Worker services

Services:

Cloud SQL PostgreSQL
Cloud Storage
Cloud KMS
Secret Manager
Pub/Sub
Memorystore if needed
Artifact Registry
Cloud Logging
Cloud Monitoring

Do not expose DB publicly.

--------------------------------------------------
58. CLOUD KMS
--------------------------------------------------

Separate key purposes:

identity encryption
capture encryption
biometric encryption
webhook secret encryption

Support key rotation.

No hard-coded production secrets.

--------------------------------------------------
59. HEALTH CHECKS
--------------------------------------------------

Implement:

/health/live
/health/ready
/health/dependencies

Check:

PostgreSQL
Redis
queue
OCR worker
face model
liveness model
KMS
object storage
webhook worker

Do not mark READY if critical dependency unavailable.

--------------------------------------------------
60. OBSERVABILITY
--------------------------------------------------

Structured logs:

request ID
session ID
organization ID
job ID
latency
error type
reason code
version

Never log:

raw API key
reviewer token
face embedding
full ID number
raw selfie
encryption key

--------------------------------------------------
61. METRICS
--------------------------------------------------

Track:

sessions_created
sessions_verified
sessions_review
sessions_rejected
technical_errors
recapture_rate
OCR_failure_rate
Khmer_OCR_confidence
Khmer_OCR_CER
selfie_failure_rate
liveness_failure_rate
face_borderline_rate
NFC_success_rate
NFC_failure_rate
manual_review_rate
queue_depth
API latency
OCR latency
face latency
webhook failure rate

--------------------------------------------------
62. KHMER OCR METRICS
--------------------------------------------------

Measure:

CER
WER
field accuracy

Specifically:

Khmer name
DOB
document number
address
place of birth

Test by:

clean
blur
glare
dark
tilted
compressed
chat-app compressed

--------------------------------------------------
63. FACE ACCURACY METRICS
--------------------------------------------------

Measure:

FAR
FRR
TAR
ROC
AUC if useful

Track separately for:

good light
low light
old ID
glasses
camera quality
different age groups

Do not claim 100% face accuracy.

--------------------------------------------------
64. LIVENESS TESTING
--------------------------------------------------

Test:

real user
printed photo
photo displayed on phone
video replay
screen replay
simple prerecorded video

Report:

genuine pass rate
attack detection rate

Unsupported attack types must be listed honestly.

--------------------------------------------------
65. GOLDEN KYC TEST SET
--------------------------------------------------

Create test data for:

KH National ID
KH NSSF
KH Passport

Variants:

clean
blur
glare
dark
far away
cropped
tilted
compressed
screenshot
rephotographed
wrong side
wrong card
expired
OCR mismatch
MRZ mismatch

Expected result must be explicit.

--------------------------------------------------
66. REAL DEVICE VALIDATION
--------------------------------------------------

Automated tests are not enough.

Require tests using:

Android phone
iPhone
real Cambodian ID captures
real NSSF samples where permitted
real passports where permitted
physical NFC chip
different phones
different camera quality
different lighting

A feature tested only by mock:

NOT_REAL_WORLD_VALIDATED

--------------------------------------------------
67. SECURITY TEST MATRIX
--------------------------------------------------

Test:

expired QR
reused QR
cross-tenant QR
cross-tenant API token
token replay
session swapping
document replay
selfie replay
liveness replay
wrong face
wrong ID
expired ID
tampered image
invalid MRZ
invalid barcode
untrusted NFC signer
altered chip data
worker crash
DB outage
KMS outage

--------------------------------------------------
68. CONSISTENCY
--------------------------------------------------

Same evidence
+
same model version
+
same policy version

must produce the same deterministic decision.

Investigate unexplained nondeterminism.

--------------------------------------------------
69. NO SILENT EXCEPTIONS
--------------------------------------------------

Never use:

except:
    pass

Every failure needs:

status
reason code
trace ID
log
safe client response

No default PASS.

--------------------------------------------------
70. STARTUP SAFETY
--------------------------------------------------

Production must refuse startup if critical configuration is missing.

Examples:

TLS
DB TLS
RLS
encryption keys
KMS
secret manager
production host rules
policy version
face calibration version
liveness policy
trust store when NFC enabled

Fail closed.

--------------------------------------------------
71. PRODUCTION ALERTS
--------------------------------------------------

Alert on:

5xx spike
DB unavailable
queue backlog
OCR failure spike
Khmer OCR degradation
face service unavailable
liveness unavailable
unexpected REVIEW spike
unexpected FAIL spike
KMS failure
storage failure
webhook backlog
NFC trust-store failure

--------------------------------------------------
72. ADVANCED ADMIN RESULT DASHBOARD
--------------------------------------------------

Top:

KYC SESSION
Decision
Risk
Status

Sections:

Identity
Document
Khmer OCR
MRZ
Barcode/QR
Document Authenticity
Face
Liveness
NFC
Fraud
Cross-check
Decision Trace
Timeline
System Diagnostics

Colors:

green PASS
amber REVIEW
red FAIL
gray NOT_RUN / NOT_SUPPORTED

Also include labels/icons for accessibility.

--------------------------------------------------
73. TIMELINE
--------------------------------------------------

Show:

session created
consent
document uploaded
quality completed
OCR completed
portrait extracted
phone connected
liveness started
liveness passed
face compared
NFC completed
fraud completed
risk decision
review decision if any
webhook sent

Include timestamp and duration.

--------------------------------------------------
74. RISK DECISION TRACE
--------------------------------------------------

Store audit-friendly rule trace.

Example:

REQUIRE_DOCUMENT
PASS

REQUIRE_FACE_MATCH
PASS

REQUIRE_LIVENESS
PASS

REQUIRE_AUTHENTICITY
REVIEW

Final:
REVIEW

Reason:
DOCUMENT_AUTHENTICITY_UNVERIFIED

No service may secretly override the decision.

--------------------------------------------------
75. RELEASE GATES
--------------------------------------------------

Before production require:

[ ] Khmer OCR benchmark complete
[ ] Khmer name extraction verified
[ ] Place-of-birth extraction tested
[ ] capture thresholds calibrated
[ ] face threshold calibrated
[ ] FAR measured
[ ] FRR measured
[ ] liveness tested
[ ] mobile QR tested
[ ] Android tested
[ ] iPhone tested
[ ] physical passport NFC tested
[ ] CSCA trust strategy ready
[ ] tenant isolation passed
[ ] RLS passed
[ ] encryption passed
[ ] KMS configured
[ ] backup configured
[ ] restore tested
[ ] webhook retry tested
[ ] erasure tested
[ ] production container build tested
[ ] load test passed
[ ] monitoring active
[ ] alerting active
[ ] disaster recovery documented

--------------------------------------------------
76. PRODUCTION READINESS MATRIX
--------------------------------------------------

Produce a final table:

Component
Implemented
Automated Tested
Real Tested
Calibrated
Security Reviewed
Production Ready
Notes

Include:

Document Capture
KH National ID
KH NSSF
KH Passport
Khmer OCR
Khmer Name OCR
Address OCR
Place of Birth OCR
MRZ
QR/Barcode
Document Authenticity
Portrait Extraction
Mobile QR
Face Detection
Face Match
Liveness
Android
iPhone
NFC Android
NFC iOS
Passive Auth
Active Auth
CSCA
Fraud
Risk Engine
Manual Review
Tenant Isolation
Encryption
Webhooks
Workers
GCP
Monitoring
Backup
Restore

--------------------------------------------------
77. READINESS RESULT
--------------------------------------------------

Final result must be one of:

READY
READY_WITH_LIMITATIONS
NOT_READY

Never return READY if critical component is:

MOCK_ONLY
UNCALIBRATED
NOT_REAL_WORLD_VALIDATED
INSECURE
BROKEN
NOT_TESTED

--------------------------------------------------
78. RELIABILITY TARGET
--------------------------------------------------

Do not claim mathematically perfect KYC.

Target:

API availability >= 99.9%

Zero cross-tenant data leakage required

Zero default PASS required

No PASS with missing required evidence required

100% reason-code coverage target

100% auditable final decisions target

100% encrypted retained biometric templates required

100% critical security tests passing required

100% critical production dependencies monitored required

--------------------------------------------------
79. IMPLEMENTATION PROCESS
--------------------------------------------------

For every major change:

1. inspect existing code
2. identify current implementation
3. identify gap
4. explain risk
5. implement smallest safe fix
6. add migration if necessary
7. add tests
8. run tests
9. run regression tests
10. update readiness matrix
11. report result

Do not rewrite working components without reason.

--------------------------------------------------
80. FINAL DELIVERABLE
--------------------------------------------------

Produce:

KYC PRODUCTION READINESS REPORT

Include:

1. Existing architecture
2. Existing correct components
3. Problems discovered
4. Changes implemented
5. Cambodia OCR results
6. Khmer name OCR results
7. Place-of-birth OCR results
8. Document authenticity
9. Face calibration
10. Liveness validation
11. Mobile QR verification
12. Android validation
13. iPhone validation
14. NFC validation
15. Fraud engine
16. Risk engine
17. Manual review
18. Security
19. Privacy
20. Infrastructure
21. Monitoring
22. Performance
23. Backup/restore
24. Remaining limitations
25. Production blockers
26. Recommended next actions
27. Final readiness status

--------------------------------------------------
MOST IMPORTANT FINAL RULE
--------------------------------------------------

The goal is NOT:

"make the KYC say PASS as often as possible."

The goal is:

"make the KYC make the safest correct decision possible from available evidence."

The system must distinguish:

IMPLEMENTED

TESTED

CALIBRATED

REAL-WORLD VALIDATED

PRODUCTION READY

A secure REVIEW is better than a false PASS.

A recapture is better than incorrect OCR.

An explicit NOT_SUPPORTED is better than fake confidence.

A technical error is better than incorrectly rejecting a legitimate person.

Start by auditing the current repository.

Do not immediately rewrite architecture.

Create the readiness matrix first.

Then fix production blockers from highest risk to lowest risk.

Priority order:

1. Cambodia document capture
2. Khmer name OCR
3. Khmer address/place-of-birth OCR
4. Field-level OCR + validation
5. Face calibration
6. Guided mobile liveness
7. Phone QR handoff
8. ID portrait vs live face
9. Real Android/iPhone validation
10. Real ePassport NFC validation
11. Document authenticity
12. Security hardening
13. GCP deployment
14. Monitoring
15. Production readiness approval