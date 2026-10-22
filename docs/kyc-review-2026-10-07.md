# KYC correctness review — 7 October 2026

Final validation completed on 8 October 2026.

The application is integrated with its FastAPI backend, but remains **NOT_READY for production**.
This review preserves the existing document, face, tenancy, review and webhook architecture,
and the edits already present when the review started. It completes verifiable implementation
gaps and records release work that still requires implementation or external evidence.

## Corrections completed

| Finding | Corrected behavior |
| --- | --- |
| Positioning and guidance uploads inherited the 64 KB JSON limit | Multipart limits now admit one positioning frame or two guidance frames, while retaining per-frame byte/pixel limits and the memory-only spool policy |
| Interleaved movement frames were grouped together and could complete a challenge | Final assessment rejects frames outside the issued step order |
| Missing faces or a turned/unstable baseline could still produce an accepted challenge | Missing observations require recapture, and the baseline must meet the same frontal/stability criteria used by guidance |
| Continuity accepted scores between the face policy's fail and pass boundaries | Every observed face must meet the pass boundary; ambiguous continuity stays REVIEW |
| An expired selfie template could still be decrypted for liveness | Reference reads enforce retention before decrypting |
| The final face comparison used the separately submitted selfie | A completed challenge with sufficient continuity selects its best accepted frontal frame, stores its encrypted embedding, and compares it with the current unexpired document portrait |
| A technical liveness failure consumed the nonce | Known model/reference/storage failures permit retry with the same nonce within its original TTL; no liveness verdict or partial comparison is recorded |
| UNAVAILABLE document checks could aggregate to PASS | Availability has review severity and its reason codes remain visible |
| A later chip-portrait comparison could replace a document-portrait mismatch | Document face matching and NFC chip face matching remain independent checks |
| Some internal failures lacked an explicit outcome | Synchronous API failures use TECHNICAL_ERROR with reason codes and retry guidance, including database, service, model and overload failures |
| A failed status poll stopped automatic polling | The next poll is scheduled after success or failure; requests have a default 60-second timeout, with the existing shorter liveness limits |
| API export omitted device authentication on positioning/guidance | The generated SDK contract lists session-token access for both routes and documents technical error fields |
| Frontend planning still described a mock-only prototype | The plan and README now describe the connected application and its remaining release work |

The React client captures its accepted frontal baseline after challenge issuance. Raw liveness
frames remain in request memory and are discarded. The selected template uses the existing
LIVE_SELFIE storage source, separate biometric encryption and the earlier reference retention
deadline. Quality measurements, challenge provenance and the comparison are retained for review.
No database migration is required. The default liveness policy version is now
`ACTIVE-GEOMETRY-2026.10.5`; installations with an explicitly configured older version should
update that identifier when applying these changes. Calibration remains disabled.

## Verification

The review adds twelve backend regression cases. Eight reproduce incorrect pre-fix behavior:
oversized camera-frame rejection, interleaved-step acceptance, ambiguous continuity acceptance,
use of expired references, missing-face/turned-baseline acceptance, unavailable checks becoming
PASS, and NFC hiding document mismatch.
Additional cases cover best-frame selection/encrypted persistence and recovery from storage
failure or a corrupt encrypted reference. Browser coverage also reproduces the polling failure
before its correction.

Validation details are recorded in `artifacts/kyc-review-2026-10-07.json` and the private local
logs under `var/kyc-review-*`. Browser tests use a synthetic camera and mocked API evidence;
they establish interface behavior, not biometric or OCR accuracy.

The final complete backend run reports **507 tests in 217.166 seconds, OK, four skip entries**.
Those skips cover native model/photo fixtures and two isolated PostgreSQL tests without a
configured `TEST_DATABASE_URL`; one skip is a native-model class setup. All **eleven browser
scenarios pass**, including camera interruption, an expired offline challenge, stalled response
bodies and polling recovery after a failed request.

The frontend TypeScript check, lint and production build pass. The Vite configuration warning
was removed by using an ES-module-compatible source alias. The existing approximately 517 KB
JavaScript bundle still produces a size advisory. The TypeScript SDK's six tests and type check
pass; its type check uses the TypeScript compiler already installed with the frontend.
The dependency-free capture client smoke check and generated OpenAPI authentication/error
contract checks also pass.

The live local PostgreSQL security checker passes **24 checks with no failures**. It confirms
forced tenant RLS on 25 tables, no unsafe API-role attributes/memberships/ownership, permissions
within the approved matrix, hardened scoped erasure, default denial without tenant context,
and refusal to modify append-only audit/risk/review records. Probe transactions are rolled back.
The local loopback connection does not use TLS; this check does not replace deployment TLS,
migration or cross-tenant integration testing against an isolated target database.

## Remaining release work

- Implement an expiring, single-use phone claim with atomic consumption and device binding,
  mobile-only capture enforcement, desktop progress states and server-driven disclosure of
  challenge steps. Current links and challenge issuance still expose the existing full sequence.
- Implement a standalone signed result envelope and finish asynchronous worker/result error
  and per-check availability reporting. Existing result authentication and signed webhooks
  do not provide a standalone signed result.
- Complete native Android/iOS passport readers and deploy governed issuer barcode/CSCA trust
  inputs. Browser NFC support is absent.
- Obtain human-confirmed Khmer transcriptions and measure field accuracy/CER. Run face/PAD
  calibration on genuine users and attack media, including physical iPhone/Android sessions.
- Deploy and verify cloud KMS/secret/storage/queue integrations, dependency health checks,
  monitoring, backup/restore procedures and target-database tenant isolation. Local runtime
  privilege/RLS checks pass, while isolated migration/erasure integration tests remain skipped.

The complete component matrix and calibration limitations remain in the
[production readiness report](production-readiness-2026-10-07.md). Automated checks cannot
replace the missing calibration sets, physical-device trials or deployed operations evidence.
