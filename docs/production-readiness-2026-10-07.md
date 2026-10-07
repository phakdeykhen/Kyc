# KYC production readiness report — 7 October 2026

**Readiness: NOT_READY for production.** The camera flow and Khmer extraction fixes are implemented and exercised locally. Real-device biometric validation, calibrated OCR/face/PAD performance, a secure mobile claim flow, and production infrastructure remain release blockers.

Scope: the existing platform and the [approved master prompt](<approved/MASTER PROMPT — PRODUCTION-GRADE KYC PLATFORM.md>). This report distinguishes implementation, automated checks, local document samples, calibration, and production evidence. Existing working services were preserved.

The subsequent [correctness review](kyc-review-2026-10-07.md) fixes additional camera request limits,
liveness ordering/continuity/retention, best-live-frame matching, unavailable-check aggregation,
independent document/NFC face results, technical-error responses and polling recovery. Its final
validation evidence supplements the earlier runs below: the full suite reports 507 tests,
OK with four skip entries; eleven browser scenarios pass; and 24 local PostgreSQL security
checks pass. Production readiness remains **NOT_READY**.

## Existing architecture and working controls

The platform uses FastAPI, SQLAlchemy/Alembic, PostgreSQL, Tesseract/OpenCV, YuNet/SFace, and a React/Vite browser client. Document adapters share extraction and validation contracts. Session transitions, evidence aggregation, fraud signals and the risk decision are deterministic. OCR and biometric uncertainty do not become a default PASS.

Existing controls include scoped API/session/reviewer credentials, tenant filters and PostgreSQL row-level policies, separate encryption keyrings for captures/PII/biometrics/webhook secrets, consent, erasure, review permissions, reason-coded decisions, audit records, signed webhook delivery with retries, deferred document workers, and admission control. Production configuration already checks runtime database privileges, TLS, encryption keys, allowed hosts and consent. Local runtime privilege/RLS checks now pass; deployed operations and isolated target-database migration/erasure integration tests still require validation.

## Problems found and changes implemented

- Camera startup could ignore failed playback or stale permission promises. Startup now waits for usable video frames; stale streams are stopped. Ended/paused streams cannot supply frozen frames.
- The idle liveness circle obscured the camera. The preview now opens inside the circle, and Start face scan opens a dark mobile scan view with one visible instruction and directional cue.
- Challenges started before the applicant was positioned. A new `/v1/kyc/{session_id}/liveness/position` endpoint checks face count, placement, image quality and frontal positioning without creating a challenge, storing frames or consuming an attempt. Two consecutive ready frames are required before issuance.
- A single correct movement frame could complete a step. Guidance and final assessment now require consecutive movement frames. Missing identity-continuity evidence cannot produce PASS. Completion fills the ring and briefly shows a completion check before advancing.
- Camera/connection failures and exhausted attempts had poor recovery. Camera interruption, cancellation, request timeouts, unmounting and attempt exhaustion now have explicit handling. Exhausted sessions require a fresh verification link; attempt counters are preserved.
- Khmer name/address extraction relied too heavily on a whole-card OCR block. Dedicated field crops now use label/word geometry, script-specific OCR, several image variants and voting. Bold name rows also use raw-line segmentation. Winning confidence is not increased merely because correlated passes agree; disagreement is flagged.
- Scanner ink blocks and complete 4:3 camera frames could be mistaken for card corners, cropping away fields or rotating a landscape ID sideways. Card-shaped scanner frames are preserved; invalid quadrilaterals are rejected, with constrained edge/paper-boundary recovery for complex backgrounds. Original evidence remains unchanged.
- MRZ-like text could be appended to a wrapped address. MRZ markers, filler runs and MRZ-pass regions now stop visual field extraction. Khmer normalization records script/order/noise problems rather than guessing replacements.
- Review text now uses Khmer-capable font fallbacks and normal word wrapping. Display font support does not establish OCR accuracy.

No database migration was required for these fixes. The local KYC APIs, including the port-8010 process used by the phone frontend, were reloaded with the positioning route and source reload enabled. The browser development server serves the updated interface.

## Verification evidence

- Complete backend regression run: **495 tests, four skipped**. One capture test still expected the previous quality-policy version. That assertion was updated to `DOC-CAPTURE-HEURISTIC-2026.10.3`; the affected **18 capture API tests were rerun and all passed**. The other executed tests in the complete run passed. The original run log retains the stale assertion failure for traceability.
- Final focused document, geometry, liveness, passport and generic-document regressions: **151 tests, OK, one skipped**.
- Frontend TypeScript, ESLint and production build: **passed**. The build reports an existing large JavaScript chunk and Vite configuration warning.
- Behavioral browser checks: **six passed** — guided completion/retry, completed capture, exhausted attempts, camera denial, cancellation during positioning and a stalled response body timing out before a challenge is issued. They use a synthetic camera and mocked API evidence; they verify interface behavior, not biometric accuracy.
- The screenshot-matching front card was reprocessed after a crop review. Its name, address and birthplace fields produced candidates, all flagged uncertain. The earlier four-card run is not counted as current evidence because the name crop was subsequently corrected.
- Live API schema check: the new positioning route is loaded. The actual phone frontend proxies to port 8010; that API was reloaded with its configuration preserved. An unauthenticated POST through `https://192.168.20.59:3000` now returns the expected **401**, rather than the old **404**.

Skipped coverage includes optional live PostgreSQL tests and an unavailable native liveness attack fixture. Fixtures and browser simulations do not establish physical iPhone/Android performance.

Local evidence: `var/face-khmer-full-tests.log`, `var/face-khmer-full-tests-final.log`, `var/face-khmer-final-regressions.log`, `var/capture-policy-contract-tests.log`, `var/liveness-browser-results.json`. A non-personal summary is stored in `artifacts/face-khmer-verification-2026-10-07.json`. The private native OCR report is `var/khmer-field-benchmark-final.json` (mode 0600); it contains personal data and is intentionally excluded from version control.

## Cambodia OCR results and remaining accuracy gap

| Local sample | Front recognized | Khmer name present | Address present | Birthplace present | Current status |
|---|---|---|---|---|---|
| photo_2026-10-07_10-26-41.jpg | Yes | Yes | Yes | Yes | All three candidates flagged uncertain |

The sample is the document represented in the user's review screenshot. Review found that the former Latin-row anchor could move the Khmer crop away from its printed top row; that anchor is removed and the template crop stays on the Khmer row. The corrected run still leaves the name, address and birthplace uncertain and requiring review. This does **not** demonstrate that every printed Khmer character is correct. The other three images need a fresh run against the corrected locator.

Human-confirmed transcriptions were not supplied, so CER, exact name accuracy and exact address accuracy are **not measured**. Printed display fonts/security patterns remain difficult for the installed model. No Latin name, MRZ name, inferred transliteration or location guess is used to manufacture a Khmer value. An authoritative province/district/commune dataset is not configured.

The installed `khm.traineddata` has the same SHA-256 as the pinned official `tessdata_best` Khmer model. The defect was therefore not resolved by simply replacing an old Khmer model. An optional `OCR_TESSDATA_DIR` supports an isolated model installation; production needs a reproducible model manifest and a labelled evaluation set before selecting a replacement.

The crop, border, scaling and segmentation choices follow the [Tesseract image-quality guidance](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html); available model families are described in the [official trained-data documentation](https://tesseract-ocr.github.io/tessdoc/Data-Files.html). Model reference: [pinned tessdata_best revision](https://github.com/tesseract-ocr/tessdata_best/tree/e12c65a915945e4c28e237a9b52bc4a8f39a0cec).

Reproduce locally:

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmark_khmer_ocr.py \
  ID/KhmerID/photo_2026-10-07_10-26-41.jpg \
  --output var/khmer-field-benchmark.json
```

To measure accuracy, add `--expected` with a private JSON file mapping each filename to confirmed `full_name_local`, `address` and `place_of_birth` values. The script calculates CER and exact match; expected text never replaces an OCR value.

## Readiness matrix

“Code/tests” means this work inspected relevant controls and/or regression coverage. It is not a deployed security audit. “Fixture” includes synthetic documents, mocked engines or generated certificates. “No evidence” means no physical/deployed validation was performed in this task. No row is asserted production ready without its release evidence.

| Component | Implemented | Automated tested | Real tested | Calibrated | Security reviewed | Production ready | Notes |
|---|---|---|---|---|---|---|---|
| Document capture | Yes | Yes | Local photos | No | Code/tests | No | PARTIAL; quality thresholds and automatic best-frame capture need validation |
| Perspective normalization | Yes | Yes | Four local photos | No | Code/tests | No | Fixed whole-frame/ink-region warps; broader backgrounds still need testing |
| KH National ID | Yes | Yes | Four local photos | No | Code/tests | No | UNCALIBRATED; no official specimen certification |
| KH NSSF | Yes | Fixture | No evidence | No | Code/tests | No | PARTIAL; adapter coverage needs real cards |
| KH Passport | Yes | Fixture | No evidence | No | Code/tests | No | PARTIAL; country-specific layout coverage |
| Generic passport | Yes | Fixture | No evidence | No | Code/tests | No | PARTIAL; supported layouts/MRZ formats need country coverage |
| Generic National ID | Yes | Fixture | No evidence | No | Code/tests | No | PARTIAL; generic labels do not establish every country's template |
| Residence/driving cards | Yes | Fixture | No evidence | No | Code/tests | No | PARTIAL; future adapters use existing contract |
| Khmer OCR | Yes | Yes | Four local photos | No | Code/tests | No | UNCALIBRATED; no measured CER across fonts/devices |
| Khmer name OCR | Yes | Yes | One local photo after crop correction | No | Code/tests | No | PARTIAL; candidate remains uncertain |
| Address OCR | Yes | Yes | One local photo after crop correction | No | Code/tests | No | PARTIAL; MRZ contamination fixed; exact text unmeasured |
| Place-of-birth OCR | Yes | Yes | One local photo after crop correction | No | Code/tests | No | PARTIAL; local geographic ground truth unavailable |
| Latin OCR | Yes | Yes | Local photos | No | Code/tests | No | PARTIAL; broad font/language accuracy unmeasured |
| MRZ | Yes | Yes | Local photos | N/A for checksums | Code/tests | No | Software checks covered; OCR/check digits do not prove authenticity |
| QR/barcode | Yes | Fixture | No evidence | N/A for signatures | Code/tests | No | PARTIAL; real issuer formats/keys required |
| Document authenticity | Partial | Fixture | No evidence | No | Code/tests | No | PRODUCTION_BLOCKER; unsigned OCR alone remains unverified |
| Portrait extraction | Yes | Yes | No physical match test | No | Code/tests | No | PARTIAL; pose/occlusion/layout accuracy needs real evidence |
| Mobile QR/claim | Partial | Link tests only | No evidence | N/A | Code/tests | No | PRODUCTION_BLOCKER; single-use phone claim/device binding absent |
| Face detection/quality | Yes | Yes | No physical session | No | Code/tests | No | UNCALIBRATED; eye visibility and occlusion explicitly unverified |
| Face matching | Yes | Yes | No calibration set | No | Code/tests | No | Best accepted liveness frame now compared to document portrait; FAR/FRR and device/demographic coverage absent |
| Guided liveness | Yes | Yes; eleven UI cases | No physical attack test | No | Code/tests | No | UNCALIBRATED; ordering, continuity, retention and technical retry guards added; unsupported attacks remain |
| Android browser | Yes | Simulation | No evidence | No | Limited | No | NOT_REAL_WORLD_TESTED |
| iPhone Safari | Yes | Simulation | No evidence | No | Limited | No | NOT_REAL_WORLD_TESTED; real permission/resume/rotation testing required |
| NFC Android | Server only | Fixture | No evidence | N/A | Code/tests | No | NOT_SUPPORTED by the browser flow; native chip client absent |
| NFC iOS | Server only | Fixture | No evidence | N/A | Code/tests | No | NOT_SUPPORTED by the browser flow; native chip client absent |
| Passive authentication | Yes | Certificate fixtures | No real chip | N/A | Code/tests | No | PARTIAL; trusted deployment inputs and real passports required |
| Active authentication | Yes, supported algorithms | Fixture | No real chip | N/A | Code/tests | No | PARTIAL; chip/algorithm coverage and replay validation required |
| CSCA trust | Loader/validation | Fixture | No trusted deployment | N/A | Code/tests | No | PRODUCTION_BLOCKER; governed issuer trust store required |
| Fraud | Yes | Yes | No attack corpus | No | Code/tests | No | PARTIAL; duplicate/velocity rules exist, broader spoof/tamper support limited |
| Risk decisions/trace | Yes | Yes | No production outcomes | No for heuristic inputs | Code/tests | No | COMPLETE software path; missing evidence/authenticity forces REVIEW |
| Manual review/timeline | Yes | Yes | No reviewer acceptance test | N/A | Code/tests | No | PARTIAL; operational role/dual-control requirements need validation |
| Tenant isolation | Yes | Yes; live DB tests skipped | No deployment evidence | N/A | Code/tests | No | PRODUCTION_BLOCKER until runtime-role/RLS tests run on target DB |
| Encryption/erasure | Yes, local keyrings | Yes | No cloud deployment | N/A | Code/tests | No | PARTIAL; KMS-managed envelope encryption and rotation operations absent |
| Signed result API | Authenticated JSON | API tests | No verifier integration | N/A | Code/tests | No | PARTIAL; standalone signed result envelope absent |
| Webhooks | Yes, HMAC/retries | Yes | No recipient acceptance test | N/A | Code/tests | No | COMPLETE software path; deployed delivery/rotation evidence required |
| Workers | Yes | Yes | No production workload | N/A | Code/tests | No | PARTIAL; cloud queue integration and capacity evidence absent |
| GCP/KMS | Configuration placeholders | No deployed checks | No evidence | N/A | No deployment | No | PRODUCTION_BLOCKER; Cloud SQL/GCS/PubSub/KMS infrastructure not provisioned |
| Monitoring/alerts | Logs/liveness/DB readiness | Some tests | No deployed signals | N/A | Code/tests | No | PARTIAL; dependency health, dashboards and alerts absent |
| Backup | No verified workflow | No | No evidence | N/A | No deployment | No | PRODUCTION_BLOCKER; retention/encryption/RPO plan and backups required |
| Restore | No verified drill | No | No evidence | N/A | No deployment | No | PRODUCTION_BLOCKER; measured recovery/RTO evidence required |

## Remaining master-prompt gaps and release gates

1. **OCR calibration:** obtain exact transcriptions for a consented Khmer golden set covering printed font families, old/new cards, holograms, glare, crop/rotation, device and lighting variations. Measure name/location exact accuracy and CER. Train/select an OCR model on that evidence if the current model cannot meet the release target. A low-confidence candidate is not a solved spelling problem.
2. **Biometrics and PAD:** retain uncalibrated REVIEW behavior until FAR/FRR and genuine/impostor measurements exist. Validate printed-photo, screen/photo/video replay, face swaps, landmark instability, masks and injection on actual hardware. Current five-landmark movement geometry does not establish validated 3D yaw/pitch/roll. Best accepted liveness-frame selection and encrypted comparison with the document portrait are now implemented; their physical-device and accuracy validation remains outstanding.
3. **Mobile-only capture:** add an opaque 2–5 minute, single-use QR handoff token, atomic phone claim, device/session binding and desktop progress states. Current share links can be opened on desktops or multiple browsers, and the selfie stage permits file uploads. The challenge currently reveals its complete future movement sequence; server-driven step disclosure remains to implement.
4. **Authenticity/NFC:** real issuer-signed barcode formats and trust keys, supported document-forensics signals, native Android/iOS NFC readers, real passport SOD/DG/AA validation and governed CSCA certificate lifecycle are required. Readable OCR, MRZ or chip data is not authenticity evidence. The risk engine already sends absent authenticity evidence to REVIEW.
5. **Operations/security:** provision GCP/KMS and secret management, verify target-database tenant isolation, complete result signing, dependency readiness checks, production metrics/alerts and backups/restore drills. Existing startup checks do not enforce every master-prompt model/trust/calibration dependency.
6. **Error/result contracts:** synchronous API service/model/storage/database failures now carry TECHNICAL_ERROR, a reason code and retry guidance. Unavailable document checks cannot aggregate to PASS. Complete the asynchronous worker/result availability contract and exercise worker/storage/database/model failures in deployment. Do not classify an unavailable internal service as an identity failure.
7. **Performance:** the four native OCR samples took 20.95–44.61 seconds on this workstation, averaging 34.41 seconds. Use the existing deferred document-worker path for production and measure concurrency/queue latency after the new multipass workload. These timings and historic load artifacts do not establish an availability or throughput SLA.
8. **Device/release validation:** run physical iPhone Safari and Android Chrome flows with camera allow/deny, cancellation, slow/disconnected network, background/resume, portrait/landscape, actual head movements and attempts exhausted. Validate reviewer decisions, signed result consumers and webhook consumers, then update every matrix row with evidence.

Existing sessions with exhausted attempts need a fresh session. Existing stored OCR results must be recaptured/reprocessed to use the new extraction path; this change does not rewrite historical evidence.

**Final readiness remains NOT_READY.** The immediate interface/extraction changes are implemented; unresolved spelling, biometric accuracy and production infrastructure are recorded as limitations and release blockers.
