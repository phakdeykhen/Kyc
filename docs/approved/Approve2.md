Extend my existing KYC platform with a complete advanced verification result system and production-readiness controls.

Do NOT rebuild the entire platform.

The system must provide a detailed result for every KYC session and must clearly explain:

- what was checked
- what passed
- what failed
- what is uncertain
- what is unsupported
- what requires review
- why the final decision was made

The system must never pretend to be 100% accurate.

Instead, design for:

- calibrated confidence
- deterministic decisions
- traceable evidence
- fail-safe REVIEW
- strong observability
- real-device testing
- production health checks
- retry/recovery
- no silent errors
- no fake PASS

# 1. FINAL RESULT TYPES

Every KYC session must end with one of:

VERIFIED
MANUAL_REVIEW
REJECTED
EXPIRED
CANCELLED
TECHNICAL_ERROR

Map business decision:

PASS
REVIEW
FAIL

Example:

VERIFIED
→ PASS

MANUAL_REVIEW
→ REVIEW

REJECTED
→ FAIL

TECHNICAL_ERROR
→ no identity decision

Never convert technical failure into FAIL automatically.

# 2. ADVANCED RESULT OBJECT

Create a detailed result response like:

{
  "session_id": "...",
  "status": "VERIFIED",
  "decision": "PASS",
  "verification_level": "DOCUMENT_FACE_LIVENESS_NFC",

  "overall": {
    "decision": "PASS",
    "risk_level": "LOW",
    "confidence": 0.97,
    "reason_codes": [
      "DOCUMENT_VALID",
      "FACE_MATCH",
      "LIVENESS_PASS",
      "NFC_VERIFIED",
      "NO_HIGH_RISK_FRAUD"
    ]
  },

  "document": {
    "document_type": "KH_PASSPORT",
    "country": "KH",
    "classification": {
      "status": "PASS",
      "confidence": 0.99
    },
    "quality": {
      "status": "PASS",
      "blur": 0.04,
      "glare": 0.03,
      "brightness": 0.91,
      "coverage": 0.95,
      "perspective": 0.94
    },
    "ocr": {
      "status": "PASS",
      "confidence": 0.96
    },
    "mrz": {
      "status": "PASS",
      "valid": true
    },
    "barcode": {
      "status": "NOT_APPLICABLE"
    },
    "authenticity": {
      "status": "PASS",
      "confidence": 0.95
    },
    "expiry": {
      "status": "PASS"
    }
  },

  "identity": {
    "name_match": "PASS",
    "dob_match": "PASS",
    "document_number_match": "PASS",
    "nationality_match": "PASS"
  },

  "biometric": {
    "document_portrait_quality": {
      "status": "PASS"
    },
    "live_face_quality": {
      "status": "PASS"
    },
    "face_match": {
      "status": "MATCH",
      "calibrated": true,
      "model_version": "..."
    }
  },

  "liveness": {
    "status": "PASS",
    "challenge_completed": true,
    "same_person_throughout": true,
    "attack_detected": false,
    "model_version": "..."
  },

  "nfc": {
    "status": "NFC_VERIFIED",
    "passive_authentication": "PASS",
    "active_authentication": "PASS",
    "dg1_match": "PASS",
    "dg2_face_used": true,
    "certificate_trust": "PASS",
    "trust_store_version": "..."
  },

  "fraud": {
    "status": "PASS",
    "risk_level": "LOW",
    "signals": []
  },

  "cross_check": {
    "status": "PASS",
    "mismatches": []
  },

  "review": {
    "required": false
  },

  "system": {
    "processing_time_ms": 4120,
    "policy_version": "...",
    "risk_engine_version": "...",
    "ocr_engine_version": "...",
    "face_model_version": "...",
    "liveness_model_version": "...",
    "document_adapter_version": "..."
  }
}

# 3. DO NOT EXPOSE EVERYTHING TO END USERS

Create separate result views.

INTERNAL RESULT:
full technical evidence

CLIENT API RESULT:
safe business result

END USER RESULT:
simple understandable message

Example end-user result:

Verification successful

or

We need to review your information

or

We could not verify your identity

Do not expose:

- raw anti-spoof score
- internal face threshold
- fraud-engine secrets
- biometric templates
- certificate internals
- attack heuristics

# 4. RESULT PER CHECK

Every check must have a status from:

PASS
FAIL
REVIEW
NOT_SUPPORTED
NOT_APPLICABLE
NOT_RUN
ERROR

Never use PASS when a check was not performed.

Example:

{
  "font_forensics": "NOT_SUPPORTED"
}

not:

{
  "font_forensics": "PASS"
}

# 5. EVIDENCE RESULT FORMAT

Every check should contain:

check_name
status
confidence
reason_codes
engine_version
timestamp

Example:

{
  "check_name": "FACE_MATCH",
  "status": "PASS",
  "confidence": 0.97,
  "reason_codes": ["FACE_MATCH"],
  "engine_version": "sface-v3",
  "timestamp": "..."
}

Where confidence has no meaningful calibrated interpretation, omit it.

# 6. DOCUMENT RESULT DETAIL

Return detailed document checks:

document classification
side detection
image quality
OCR
Khmer OCR
Latin OCR
MRZ
QR
barcode
expiry
document number format
DOB validity
security layout
portrait region
metadata
tamper indicators
screen reproduction
print reproduction
NFC consistency

Example statuses:

DOCUMENT_CLASSIFICATION_PASS
DOCUMENT_QUALITY_PASS
OCR_CONFIDENCE_LOW
MRZ_VALID
DOCUMENT_EXPIRED
DOCUMENT_AUTHENTICITY_UNVERIFIED

# 7. FIELD-LEVEL OCR RESULT

For each field store:

raw_value
normalized_value
confidence
bounding_box
source
validation
consistency

Example internal data:

{
  "field": "date_of_birth",
  "raw_value": "០៥.០៣.១៩៩៨",
  "normalized_value": "1998-03-05",
  "confidence": 0.96,
  "source": "VISUAL_OCR",
  "validation": "PASS"
}

Do not expose sensitive raw values unless caller is authorized.

# 8. MULTI-SOURCE IDENTITY RESULT

For every important field compare:

OCR
MRZ
QR
BARCODE
NFC

Example:

{
  "field": "date_of_birth",
  "sources": {
    "ocr": "1998-03-05",
    "mrz": "1998-03-05",
    "nfc_dg1": "1998-03-05"
  },
  "status": "PASS"
}

If mismatch:

{
  "status": "REVIEW",
  "reason": "FIELD_MISMATCH"
}

Never silently overwrite one source.

# 9. FACE RESULT DETAIL

Face result must include internally:

document_portrait_source
document_portrait_quality
live_frame_quality
model_name
model_version
alignment_version
threshold_policy_version
comparison_band
calibration_status

Possible result:

MATCH
BORDERLINE
NO_MATCH
UNCALIBRATED
ERROR

Never return MATCH if model calibration is incomplete.

# 10. LIVENESS RESULT DETAIL

Return:

challenge_id
challenge_version
steps_requested
steps_completed
same_person_throughout
quality_status
attack_detected
attack_category
liveness_status
model_version

Do not expose sensitive anti-spoof internals to customers.

Example internal result:

{
  "status": "PASS",
  "challenge_completed": true,
  "same_person_throughout": true,
  "attack_detected": false
}

# 11. NFC RESULT DETAIL

For passport NFC include:

chip_read_status
PACE/BAC status
SOD parsed
signature verified
Document Signer verified
CSCA trust
DG hash validation
DG1 result
DG2 result
DG15 result
Passive Authentication
Active Authentication
Chip Authentication if supported
certificate validity
trust_store_version

Possible status:

NFC_VERIFIED
NFC_READ
NFC_FAILED
NFC_NOT_AVAILABLE
NFC_NOT_SUPPORTED

# 12. FRAUD RESULT

Return a fraud summary:

{
  "status": "REVIEW",
  "risk_level": "MEDIUM",
  "signals": [
    {
      "code": "METADATA_EDITING_SOFTWARE",
      "severity": "MEDIUM"
    }
  ]
}

Possible signals:

DUPLICATE_DOCUMENT
REUSED_IMAGE
REUSED_SELFIE
SUSPICIOUS_METADATA
SCREEN_CAPTURE
PRINT_REPRODUCTION
PORTRAIT_REPLACEMENT
MRZ_VISUAL_MISMATCH
CHIP_DOCUMENT_MISMATCH
VELOCITY_ANOMALY
PRESENTATION_ATTACK_SUSPECTED

Only emit signals the system actually detects.

# 13. DECISION TRACE

The Risk Engine must produce a decision trace.

Example:

{
  "decision": "REVIEW",
  "trace": [
    {
      "rule": "REQUIRE_LIVENESS",
      "result": "PASS"
    },
    {
      "rule": "REQUIRE_FACE_MATCH",
      "result": "PASS"
    },
    {
      "rule": "REQUIRE_DOCUMENT_AUTHENTICITY",
      "result": "REVIEW"
    }
  ],
  "final_reason_codes": [
    "DOCUMENT_AUTHENTICITY_UNVERIFIED"
  ]
}

This trace must be append-only/auditable.

# 14. NO HIDDEN OVERRIDES

No service may secretly change the Risk Engine's result.

LLM must never override:

PASS
REVIEW
FAIL

Manual reviewer may act only according to allowed policy.

All manual overrides must be audited.

# 15. TECHNICAL ERROR VS IDENTITY FAILURE

Separate:

identity failure

from:

system failure

Examples:

FACE_MISMATCH
→ identity FAIL

LIVENESS_FAILED
→ identity FAIL

OCR_TIMEOUT
→ technical ERROR

DATABASE_TIMEOUT
→ technical ERROR

FACE_MODEL_UNAVAILABLE
→ technical ERROR

NFC_NETWORK_ERROR
→ technical/retry

Never reject a real user because an internal service crashed.

# 16. RETRY POLICY

Add explicit retry/recovery.

Recoverable:

blur
glare
low light
bad document position
camera interruption
temporary OCR failure
temporary network failure
NFC retryable failure

Return:

RETRY_ALLOWED

Security failures may not be freely retried forever.

Add attempt limits.

# 17. HEALTH CHECKS

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

Production should not report READY if critical dependencies are unavailable.

# 18. STARTUP SAFETY

In production, refuse startup if required controls are missing.

Examples:

production TLS
DB TLS
encryption keys
KMS configuration
secret manager
trusted allowed hosts
RLS
production policy version
face calibration version
liveness calibration/version
CSCA trust configuration for NFC-enabled production mode

Fail closed.

# 19. OBSERVABILITY

Add metrics:

kyc_sessions_total
kyc_pass_total
kyc_review_total
kyc_fail_total
kyc_technical_error_total
kyc_recapture_total

document_quality_failure_rate
ocr_failure_rate
khmer_ocr_failure_rate
face_match_borderline_rate
liveness_failure_rate
nfc_failure_rate
manual_review_rate

API p50/p95/p99
OCR p50/p95/p99
face p50/p95/p99
queue depth
DB pool usage
worker failures

# 20. ALERTING

Production alerts should trigger on:

high 5xx rate
DB unavailable
queue stuck
OCR failure spike
face-service unavailable
liveness-service unavailable
sudden REVIEW spike
sudden FAIL spike
webhook backlog
storage failure
KMS failure
certificate/trust-store failure

# 21. CONSISTENCY TESTING

Run the same evidence multiple times.

Expected:

same evidence
+
same policy version
+
same model version

→ same decision

Any nondeterministic decision must be investigated.

# 22. ZERO SILENT FAILURES

Every processing stage must have:

success
failure
reason code
trace ID

No bare:

except:
    pass

No swallowed exceptions.

No default PASS.

No default identity values.

# 23. PRODUCTION TEST MATRIX

Before production run:

clean valid ID + real owner
valid ID + wrong person
printed photo attack
screen replay attack
video replay
blurred ID
glare ID
expired ID
wrong document
altered image
MRZ mismatch
QR mismatch
NFC mismatch
cloned/replayed chip data where testable
network interruption
DB interruption
OCR worker interruption
face model interruption
mobile disconnect
expired QR
reused QR
cross-tenant token attack

Every scenario must have an expected outcome.

# 24. AUTOMATED REGRESSION SUITE

Every release must run:

unit tests
integration tests
API tests
database RLS tests
tenant isolation tests
OCR golden tests
MRZ tests
barcode tests
face tests
liveness tests
NFC tests
risk-engine tests
manual-review tests
webhook tests
security tests
load tests

Block deployment if critical tests fail.

# 25. PRODUCTION APPROVAL MATRIX

Generate:

Component
Implemented
Automated Tested
Real Tested
Calibrated
Security Reviewed
Production Ready

For:

Document Capture
KH ID
KH NSSF
KH Passport
OCR
Khmer OCR
MRZ
QR/Barcode
Document Authenticity
Portrait Extraction
Face Detection
Face Match
Liveness
Android Mobile
iPhone Mobile
QR Handoff
NFC Android
NFC iOS
CSCA
Fraud
Risk Engine
Manual Review
RLS
Encryption
Webhooks
Workers
GCP
Monitoring
Backup
Restore

# 26. FINAL READINESS RESULT

The final platform report must produce:

READY
READY_WITH_LIMITATIONS
NOT_READY

Never return READY if a critical feature is:

UNCALIBRATED
MOCK_ONLY
NOT_REAL_WORLD_TESTED
BROKEN
INSECURE

# 27. RELIABILITY TARGET

Do not promise "100% perfect."

Instead define engineering targets.

Example:

API availability target
>= 99.9%

No cross-tenant data leakage
required

No default PASS
required

No PASS from missing required evidence
required

100% auditability of final decisions
target

100% reason-code coverage for decisions
target

100% encryption of retained biometric templates
required

Critical security tests passing
required

Critical production dependencies monitored
required

These targets are acceptable.

Identity accuracy must be measured, not promised.

# 28. FACE ACCURACY REPORT

Produce:

FAR
FRR
TAR
ROC/AUC where useful
genuine score distribution
impostor score distribution
threshold policy
dataset size
test conditions

Do not claim 100% face accuracy.

# 29. OCR ACCURACY REPORT

For Khmer OCR measure:

CER
Character Error Rate

WER
Word Error Rate

field accuracy

Measure separately:

name
DOB
document number
address

Measure by quality:

clean
blur
glare
dark
compressed

# 30. LIVENESS TEST REPORT

Track:

genuine-pass rate
attack-detection rate

Test at least:

printed photo
phone-screen photo
screen video
recorded video
simple face replay

Clearly report unsupported attacks.

# 31. RESULT DASHBOARD

Create an internal admin result screen.

Top summary:

KYC #SESSION_ID

Decision:
PASS / REVIEW / FAIL

Risk:
LOW / MEDIUM / HIGH

Then sections:

Identity
Document
Face
Liveness
NFC
Fraud
Cross-check
Decision Trace
Timeline
System Diagnostics

Each section should use:

green = PASS
amber = REVIEW
red = FAIL
gray = NOT_RUN / NOT_SUPPORTED

Do not rely on colors only; include labels/icons.

# 32. TIMELINE

Show exact workflow:

Session created
↓
Consent received
↓
Document uploaded
↓
Document quality passed
↓
OCR completed
↓
Portrait extracted
↓
Phone connected
↓
Liveness passed
↓
Face comparison completed
↓
NFC completed
↓
Fraud analysis completed
↓
Risk engine decided
↓
Webhook sent

Include timestamps and durations.

# 33. EXPLAIN RESULT TO REVIEWER

For REVIEW cases, generate a concise summary using deterministic data.

Example:

Review required because:

- Khmer name OCR confidence is low
- MRZ and visual DOB agree
- Face match is MATCH
- Liveness passed
- Document authenticity is unverified

Do not use an LLM to change the decision.

An LLM may summarize existing evidence only.

# 34. CLIENT API RESULT

Expose a safe result such as:

{
  "session_id": "...",
  "status": "VERIFIED",
  "decision": "PASS",
  "reason_codes": [
    "DOCUMENT_VALID",
    "FACE_MATCH",
    "LIVENESS_PASS"
  ],
  "completed_at": "..."
}

Only return identity fields when the API key has permission.

# 35. END USER RESULT

PASS:

"Identity verification completed successfully."

REVIEW:

"Your verification has been submitted for additional review."

FAIL:

"We could not verify your identity."

RECAPTURE:

"Please take a new photo and try again."

TECHNICAL:

"We couldn't complete verification right now. Please try again."

Do not accuse the user of fraud in the end-user message.

# 36. SECURITY LOG

For every verification log:

session_id
organization_id
event
result
reason_code
timestamp
service
version
trace_id

Never log:

full API key
raw reviewer token
biometric template
full passport number
raw selfie image
encryption key

# 37. DATA INTEGRITY

Store hashes/version metadata so evidence and results cannot be silently changed.

Audit:

who
what
when
previous state
new state

Use append-only audit logging where appropriate.

# 38. DEPLOYMENT BLOCKERS

Do not deploy if:

face calibration missing
liveness calibration/validation missing
critical KYC tests failing
RLS tests failing
KMS unavailable
production secrets missing
database backup not configured
restore never tested
NFC advertised but never physically tested
risk engine can be bypassed
manual review can approve prohibited evidence
critical vulnerabilities unresolved

# 39. FINAL REPORT FORMAT

At the end produce:

KYC PRODUCTION READINESS REPORT

Overall:
READY / READY_WITH_LIMITATIONS / NOT_READY

Then:

System Health
Document Verification
OCR Accuracy
Face Verification
Liveness
NFC
Fraud Detection
Risk Engine
Manual Review
Security
Privacy
Performance
Infrastructure
Monitoring
Backup/Recovery
Mobile QR Verification

For every category provide:

Status
Evidence
Tests performed
Limitations
Production blocker
Recommended next action

# 40. MOST IMPORTANT RULES

Never fake a PASS.

Never treat missing checks as PASS.

Never silently repair conflicting identity data.

Never let an LLM make identity decisions.

Never confuse system errors with identity failures.

Never claim 100% accuracy.

Every decision must be explainable.

Every decision must be auditable.

Every model must have a version.

Every policy must have a version.

Every critical biometric threshold must be calibrated.

Every production feature must be tested using real-world conditions before it is marked production-ready.

Start by auditing the current repository.

First produce a readiness matrix.

Then fix critical blockers one by one.

After each fix:

run tests
report test result
update readiness matrix

Do not mark the system READY until all critical production gates pass.