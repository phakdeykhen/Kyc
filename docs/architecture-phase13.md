# Phase 13 — Deterministic risk engine

Status: implemented on 6 October 2026.

## 1. Design (spec §19)

The risk engine is the only component that decides. It is a pure function of the stored
evidence and a versioned policy: the same inputs always give the same PASS, REVIEW or
FAIL, with reason codes and a trace of every rule that fired. No model, no LLM, and no
configuration can override it toward a more lenient result.

```
reaches PROCESSING
  → SessionAssessor (post-commit task, or POST /v1/kyc/{id}/verify), session row locked
      fraud analysis (Phase 12) → collect_evidence() → policy.resolve(level, country, document)
      → evaluate() → risk_assessments row + audit RISK_ASSESSED
  → PASS   ASSESSMENT_PASS   → VERIFIED       (state machine re-checks its evidence guard)
    REVIEW ASSESSMENT_REVIEW → MANUAL_REVIEW
    FAIL   ASSESSMENT_FAIL   → REJECTED
```

The engine reads the same `collect_evidence()` that builds `GET /result`, so the client
and the decision always see identical checks.

### Evaluation order

1. **Required evidence for the level.** A required check that is missing gives
   `EVIDENCE_MISSING_<CHECK>`; one that is UNAVAILABLE gives `<CHECK>_UNAVAILABLE`. Both
   are REVIEW. NOT_APPLICABLE is accepted only for `expiry` and `cross_check`.

   | Level | Required checks |
   | --- | --- |
   | DOCUMENT_ONLY | document_quality, document_classification, document_data, expiry, cross_check, fraud |
   | DOCUMENT_FACE | + portrait_quality, face_quality, face_match |
   | DOCUMENT_FACE_LIVENESS | + liveness |
   | DOCUMENT_FACE_LIVENESS_NFC | + nfc |

2. **Check rules.** Every reported check is matched against the rule table. These map to
   **FAIL**: `LIVENESS_FAILED`, `FACE_MISMATCH`, `EXPIRED_DOCUMENT`, and
   `HIGH_RISK_TAMPER_SIGNAL` (fraud, barcode signature or chip authentication).
   These map to **REVIEW**: `LOW_OCR_CONFIDENCE`, `FIELD_MISMATCH`, `FACE_SCORE_BORDERLINE`,
   `LIVENESS_INCONCLUSIVE`, `NFC_NOT_VERIFIED`, `MRZ_INVALID`, `EXPIRY_UNKNOWN`, and others.
   An unknown non-pass value maps to REVIEW (`CHECK_<NAME>_<VALUE>`), never to FAIL.
3. **Calibration.** A face match that is REVIEW under an uncalibrated policy adds
   `FACE_MATCH_UNCALIBRATED`.
4. **Authenticity.** A PASS needs at least one source showing the document is genuine,
   not merely self-consistent: a verified chip, a valid signed barcode, or supported
   document forensics. Without one the result is REVIEW `DOCUMENT_AUTHENTICITY_UNVERIFIED`
   (spec: reading is not authenticating).
5. **Fraud signals.** HIGH tamper signals FAIL; other HIGH and MEDIUM signals give REVIEW
   (`FRAUD_<SIGNAL>`); LOW signals are ignored.

FAIL beats REVIEW beats PASS. The decision carries the reason codes of its own level; the
full trace (every rule, including lower-level hits) is stored in
`risk_assessments.check_summary`. A PASS reports `DOCUMENT_VALID`, `FACE_MATCH`,
`LIVENESS_PASS`, `NFC_VERIFIED` (as the level requires) and `NO_FRAUD_SIGNALS`.

### Defence in depth for VERIFIED

A PASS still has to satisfy the state machine's `VerificationEvidence` guard (document
valid, face match PASS, liveness PASS and NFC verified, as the level requires). If the
guard refuses, the session goes to MANUAL_REVIEW with `VERIFICATION_EVIDENCE_GUARD`, never
to VERIFIED.

### Configurable, tighten-only policy

`RISK_POLICY_FILE` (optional JSON) holds a version and per-country/per-document overrides:

```json
{"version": "KH-STRICT-2026.10.1",
 "overrides": [{"country": "KH", "document_type": "KH_PASSPORT",
                "require": ["barcode"],
                "check_rules": {"issuing_country:REVIEW": "FAIL"},
                "signal_rules": {"DOCUMENT_USED_BY_ANOTHER_USER": "FAIL", "*:LOW": "REVIEW"}}]}
```

Loading validates that every override only **tightens** the policy: IGNORE → REVIEW →
FAIL, extra required checks, fewer NOT_APPLICABLE allowances. A file that would loosen any
rule is rejected at startup. Some rules are floors that no configuration can lower:
liveness FAIL, fraud FAIL and HIGH tamper signals always FAIL. Every assessment records
the policy version, a hash of the file and the overrides that applied.

### API

- `POST /v1/kyc/{session}/verify` decides a PROCESSING session now, or returns the decision
  already made (idempotent: it never reassesses). It answers 409 with
  `VERIFICATION_NOT_READY`, `SESSION_EXPIRED` or `DOCUMENT_NOT_PROCESSED`.
- `GET /v1/kyc/{session}/result` now carries
  `decision: {result, reason_codes, policy_version, assessed_at}`.
- The capture page shows the person only "Identity verified", "Your details are being
  reviewed" or "We could not verify your identity", never reason codes. It keeps polling
  while the session is PROCESSING.

### Storage and privileges

`risk_assessments` is an append-only history: `kyc_app` has SELECT and INSERT only, so a
decision cannot be rewritten through the API role. It holds check values, signal codes,
the trace and policy metadata, never identity values.

## 2. Files

```
src/kyc/risk/policy.py            built-in policy, override file schema (tighten-only), floors
src/kyc/risk/engine.py            evaluate(): required evidence, rules, authenticity, signals
src/kyc/services/risk.py          SessionAssessor, authenticity sources, POST /verify, worker query
src/kyc/services/results.py       ~ collect_evidence() shared with the engine; decision in result
src/kyc/api/routes.py             ~ POST /verify; post-commit assessment replaces fraud-only trigger
src/kyc/api/schemas.py            ~ ResultDecision, VerifyResponse
src/kyc/web/capture/capture.js    ~ final outcome for the person; polls while PROCESSING
scripts/process_documents.py      ~ assesses every PROCESSING session
tests/test_risk.py                + 15 tests: rules, levels, authenticity, overrides, API, guard
```

No migration: `risk_assessments` and the ASSESSMENT_* state transitions have existed since
Phase 1. Bootstrap grants `kyc_app` SELECT and INSERT on `risk_assessments`.

## 3. Validation (6 October 2026)

- **333 tests: 333 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6 (RLS now
  exercised on `risk_assessments`) and native face models
  ([artifacts/phase13-tests.txt](../artifacts/phase13-tests.txt)). The capture-page
  simulation passes.
- Live HTTP as `kyc_app` with real OCR and models
  ([artifacts/phase13-live-e2e.json](../artifacts/phase13-live-e2e.json)):

| Case | Status | Decision · reasons |
| --- | --- | --- |
| Genuine passport + verified chip | MANUAL_REVIEW | REVIEW · uncalibrated face match, low OCR confidence, liveness evidence missing (bypassed for the test) |
| Cloned chip | REJECTED | FAIL · `HIGH_RISK_TAMPER_SIGNAL`, `FRAUD_NFC_CHIP_CLONE_SUSPECTED` |
| Document only, real OCR | MANUAL_REVIEW | REVIEW · `DOCUMENT_AUTHENTICITY_UNVERIFIED`, `LOW_OCR_CONFIDENCE`, … |
| Expired passport | REJECTED | FAIL · `EXPIRED_DOCUMENT` |
| `/verify` before liveness | 409 | `VERIFICATION_NOT_READY` |
| `/verify` twice on a decided session | 200, 200 | identical response |
| Strict KH policy file, same passport by another user | REJECTED | FAIL · `FRAUD_DOCUMENT_USED_BY_ANOTHER_USER`, policy `KH-STRICT-2026.10.1` |
| `kyc_app` UPDATE of a decision | refused | `permission denied for table risk_assessments` |

5 assessments, 0 failures.

## 4. Limits and what this means today

- With face and liveness thresholds uncalibrated, and no document forensics, **no face
  session and no document-only session without a signed barcode or verified chip can
  reach VERIFIED**. They go to MANUAL_REVIEW. This is intended until calibration (and
  forensics) are done; the reviewer dashboard is Phase 14.
- Lowering the built-in policy (for example accepting document-only PASS without
  authenticity evidence) is deliberately a code change with a new policy version, not a
  configuration switch.
- Webhooks for `kyc.verified`, `kyc.rejected` and `kyc.review.required` are Phase 16.
- The rule set and the reason codes need review by compliance before production.
