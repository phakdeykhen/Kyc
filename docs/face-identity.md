# Optional face enrollment and identification

Status: **IMPLEMENTED SOFTWARE PATH / NOT_READY FOR PRODUCTION**. This is independent of the existing KYC readiness report and verdict service. `FACE_RECOGNITION_ENABLED=false` is the default. There is no approved biometric calibration artifact, approval key, deployment backup evidence or validated occlusion provider in this repository. Unit tests and synthetic browser runs do not establish biometric accuracy.

## Architecture and boundaries

The existing FastAPI, SQLAlchemy/Alembic, PostgreSQL, OpenCV YuNet/SFace and React/Vite stack is retained. The KYC verdict/risk engine, state machine, 1:1 verification, reviewer permissions and original biometric consent retain their existing behavior. Recognition never writes a KYC verdict. Enrollment is optional and requires an existing final `VERIFIED` KYC session belonging to the authenticated account. Declining or withdrawing enrollment leaves completed KYC unchanged.

`src/kyc/face_identity/` owns the optional feature. Migration `0013_face_identity` adds nine tenant-scoped tables with forced PostgreSQL RLS, scoped foreign keys and B-tree indexes:

| Table | Purpose |
| --- | --- |
| `face_subjects` | Opaque subject UUID, trusted staff/customer classification, verified KYC reference and separately encrypted minimal profile |
| `face_actors` | Hashed, expiring credentials; kind, purposes, authorized enrollment populations, permitted fields and kiosk branch |
| `face_consents` | Future-recognition purpose, notice version, grant time, expiry and withdrawal |
| `face_enrollments` | Subject, consent, active/review/revoked/expired status and model/policy binding |
| `face_templates` | Separately encrypted embedding, key version and quality metadata |
| `face_capture_sessions` | Actor-bound purpose, random server steps, rotating one-use ticket, expiry and encrypted temporary evidence |
| `face_sample_receipts` | Tenant-keyed camera-byte HMACs for cross-session replay rejection; 24-hour retention |
| `face_recognition_events` | Outcomes/reasons, actor/device, timestamp, model/policy and encrypted internal review evidence |
| `face_deletions` | Biometric-free deletion tombstones and external-ledger synchronization state |

The ordinary `kyc_app` role cannot read enrollment templates, camera evidence or recognition events. It can read subject/enrollment metadata for KYC erasure. `kyc_biometrics` has a separate login/connection and a declared least-privilege matrix in `db/privileges.py`. Browsers never receive either database credential. The ordinary API role may invoke only the scoped `erase_face_identity(uuid)` function after the same tenant's KYC session has been erased. This function cannot be used to erase another tenant or a non-erased KYC session.

`FACE_RECOGNITION_KEYS` is an independent AES-256-GCM keyring. Associated data binds tenant, record, purpose and template model. Original KYC reference templates retain their original biometric keyring. Minimal profile fields use the existing PII cipher, independently of face vectors. `FACE_RECOGNITION_LEDGER_KEY` signs the external deletion journal. Never reuse these keys or expose vectors through client APIs, logs, metrics, URLs or ordinary profile records.

## Authentication and provisioning

Existing applicant `kst_` links, partner API keys and reviewer tokens cannot enroll or scan through these endpoints. They do not prove which account is currently authenticated. New `fri_` credentials are high-entropy, hashed at rest, tenant-scoped and revocable. Person sessions expire in ten minutes; enrollment additionally requires account authentication within ten minutes. Registered kiosks expire after eight hours and require a branch; operators expire after one hour.

The repository has no existing end-user identity provider. A trusted integration must authenticate the account, resolve its server-side user ID, and invoke the trusted provisioning boundary. `manage_face_identity.py person` implements this boundary for operators/integration testing: its `--authenticated-user-id` must exactly equal the server-owned KYC session's `user_id`. Never expose this command or its identity assertion as an unauthenticated web API. Production SSO/account authentication and secure device credential delivery remain integration release gates. The UI accepts a short-lived issued credential in memory; it does not promote a KYC link into account authentication.

Prepare the schema and separate development secrets without enabling recognition:

```sh
PYTHONPATH=src .venv/bin/python scripts/bootstrap_local.py
.venv/bin/python scripts/configure_face_identity.py
PYTHONPATH=src .venv/bin/python scripts/manage_face_identity.py init-role
PYTHONPATH=src .venv/bin/python scripts/manage_face_identity.py init-ledger
```

The setup script preserves existing `.env` values and prints no secrets. Development's ledger path is `var/face-deletions.jsonl`. Production must mount independently protected storage outside database restore sets. It also requires HTTPS, a TLS biometric database connection, the restricted service login, independent keys and a trusted approval public key. `init-role` changes only the dedicated `kyc_biometrics` login and grants; run the catalog checker afterward.

Example trusted provisioning, after authenticating the same account through your service:

```sh
PYTHONPATH=src .venv/bin/python scripts/manage_face_identity.py person \
  --organization ORG_UUID --kyc-session VERIFIED_KYC_UUID \
  --authenticated-user-id AUTHENTICATED_ACCOUNT_ID \
  --population STAFF \
  --profile /private/path/minimal-profile.json --output var/person-credential.json
PYTHONPATH=src .venv/bin/python scripts/manage_face_identity.py device \
  --organization ORG_UUID --branch BRANCH_REFERENCE --output var/kiosk-credential.json
```

The example classifies a staff member through trusted administration. Customer account provisioning defaults to `CUSTOMER`. A kiosk credential always searches only `STAFF`; operators default to `CUSTOMER` and need explicit `--operator-populations` permissions to broaden that scope. Personal credentials resolve only their own subject. Clients cannot choose or change the target population. An existing subject's classification cannot silently change through the provisioning command. Duplicate reviewers receive explicit access to both populations for private duplicate resolution; this is separate from profile authorization.

Profile input permits only `display_name`, `internal_reference`, `department` and `membership_status`, each a short string. KYC status is always resolved on the server. Profile copies expire under the organization's existing PII retention period; refresh them only from an authorized account/profile source. Credentials are written once to mode-0600 files. Deliver them through the authenticated account/device channel; do not distribute credentials through public links. The app keeps them only in memory.

Use `operator --duplicate-review` for explicitly authorized duplicate reviewers. `revoke --actor UUID` immediately revokes a registered credential. `suspend --subject UUID` removes that subject from eligibility; `resume` does not override revoked/expired enrollment, consent, ledger, model or KYC checks. All commands require the tenant and trusted administrator database access.

## Capture and optional enrollment

The verified-result page offers **Enable Face Recognition** only for `VERIFIED`. `/face/enroll` requires a person credential, explains purpose, permitted fields, active and approved backup retention, notice version, withdrawal and the non-biometric alternative. A new explicit grant is required for every enrollment/re-enrollment. The original KYC selfie is never silently enrolled.

Capture starts only when the applicant chooses it. One mounted video renders independently of serial camera capture/server inference. The server reveals only the current randomly selected step, accepts a rotating one-use frame ticket, enforces a 120-second maximum session, 15-second ticket lifetime, 200ms minimum spacing and two qualifying frames per step. It resets interrupted holds after two seconds. There are at most 96 checked frames per attempt. The client uploads at most one 640px JPEG at a time, rejects checks more than two seconds old and discards results after cancellation, hidden/paused/interrupted camera or unmount. It does not initialize the camera again for each step.

Exactly one face, usable light/focus/size/pose and verified visibility are required. Supported movement and continuity checks are repeated against encrypted accepted samples at completion. Completion must occur within ten seconds of the last accepted sample. One best frontal probe is selected; correlated frames do not multiply confidence. Enrollment compares that probe with the unexpired, compatible document-portrait template from the verified KYC session under separately approved 1:1 enrollment rules. Missing reference evidence requires recovery/fresh verification, never enrollment by fallback to an old selfie.

Raw pixels are discarded after each frame request. Only encrypted temporary embeddings, geometry and quality persist until completion or session expiry; they are not a searchable enrollment. Completed/failed samples are destroyed immediately. Expired samples are excluded immediately and physically removed by maintenance. Exact byte replay is rejected across sessions within the replay receipt retention window. A browser cannot cryptographically prove that all media came from a physical camera; random challenge, replay and supported PAD controls require real attack/device evaluation, and virtual camera injection remains an explicit validation limitation.

**Current blocker:** `OpenCVFaceEngine` explicitly cannot establish eye visibility/severe occlusion from five landmarks. The new path therefore requires a validated `visibility(image, face, frontal=...)` adapter, an approved `visibility_provider` identity and `occlusion_supported=True`; it also rejects any returned `unverified_checks`. The existing detector is not relabeled as a validated occlusion provider. Tests inject a clearly synthetic provider. An actual validated adapter plus independent PAD/device evidence is required before this feature can be enabled with real users. KYC's existing unverified/review behavior remains intact.

## Identification and disclosure

This first implementation uses **bounded exact 1:N comparison**, not ANN. It reads only this tenant's active, unexpired, consented, non-suspended enrollments with compatible model and exact approved policy fingerprint, whose KYC remains verified and unerased. Actor and approved-policy population scopes, personal subject restrictions and external deletion denials are applied before limiting/searching the gallery. A staff kiosk cannot identify a customer from that customer's enrollment. The private duplicate-quarantine check can compare approved populations within the tenant without revealing a profile. The service decrypts eligible vectors in request memory, performs exact cosine comparisons and discards working references. There is no plaintext vector index or persistent plaintext vector cache. Ordinary vector search cannot operate directly on these application-encrypted embeddings.

The approved `max_population` is enforced: exceeding it fails closed instead of silently truncating the gallery. A tenant-scoped PostgreSQL transaction advisory lock serializes population mutations, withdrawal, search and disclosure. This deliberately bounded implementation needs realistic throughput/latency measurements. If ANN is added later, treat its plaintext index as biometric storage with separate ACLs/encryption/tenant partitioning, immediate revocation/delete propagation, measured candidate recall and exact final re-ranking. This implementation claims no ANN performance.

Minimum match and competing-candidate margin come only from an approved policy. A nearest neighbor is not automatically a match. Outcomes are `MATCHED`, `NO_MATCH`, `AMBIGUOUS`, `RETRY_REQUIRED`, `LIVENESS_FAILED`, `EXPIRED` and `TECHNICAL_ERROR`. Uncertainty never changes the KYC verdict or automatically rejects the person's identity.

Only `MATCHED` can issue a random 30-second recognition reference. The server binds it to tenant, capture session, actor/device, purpose, policy and the profile-read endpoint. The client never supplies a subject ID. Resolving the reference rechecks actor/device validity, population eligibility, consent, expiry, KYC and role/purpose permissions. Completion and disclosure require idempotency keys; the reference cannot be consumed under an unrelated operation key or action. Re-reading the result rotates the reference.

| Requester | Disclosure |
| --- | --- |
| Person account | Only that account's enrolled subject and permitted minimal fields |
| Registered staff kiosk | Staff population only; display name, internal reference, department and current KYC status, intersected with device permissions |
| Authorized operator | Its assigned populations and minimal fields within its configured field allowlist and `customer_service` purpose |
| Anonymous/public device | No recognition session or personal profile |

Membership status is optional for authorized personal/operator displays. Profile photographs are not currently permitted by this module; original ID photos are never used as a substitute. Document numbers, DOB, addresses, ID images, finances and other sensitive records are absent from this API. Access to those needs a separate authenticated, authorized workflow; a recognition reference cannot unlock them.

Kiosk results clear after at most fifteen seconds; other results clear within thirty. They also clear when the tab is hidden, camera flow is canceled, the page leaves or the user signs out. All API responses inherit `Cache-Control: no-store`; profiles/references/credentials are never put in browser storage or persistent device history. English and Khmer copy, visible arrows/text and existing Khmer-capable fonts are provided. A staff-assisted verification link uses the existing authorized console as the non-biometric alternative. QR issuance/redemption is not implemented by this module and must not be represented as available without an integrator's separate QR flow.

## API

Every feature request requires `X-Organization-ID` and `Authorization: Bearer fri_…`. Completion, profile consumption and duplicate decisions require `Idempotency-Key` (16–128 URL-safe characters).

| Method | Route |
| --- | --- |
| GET | `/health/face-recognition` (independent readiness; no biometric thresholds) |
| GET | `/v1/face-recognition/capabilities` |
| POST | `/v1/face-enrollments/sessions` (`consent`, `notice_version`; no subject parameter) |
| POST | `/v1/face-enrollments/sessions/{identifier}/frames` (multipart `ticket`, `frame`) |
| POST | `/v1/face-enrollments/sessions/{identifier}/complete` |
| GET / DELETE | `/v1/me/face-enrollment` |
| POST | `/v1/recognition/sessions` (`purpose`) |
| POST | `/v1/recognition/sessions/{identifier}/frames` |
| POST | `/v1/recognition/sessions/{identifier}/complete` |
| GET | `/v1/recognition/sessions/{identifier}/result` |
| POST | `/v1/recognition/sessions/{identifier}/profile` (`recognition_reference`; no subject parameter) |
| GET | `/v1/face-enrollments/duplicates` (explicit internal reviewer permission) |
| POST | `/v1/face-enrollments/duplicates/{identifier}/resolve` |

The OpenAPI export defines a distinct `FaceActorToken` scheme and scanner response allowlists. Scanners receive neither candidate lists/scores nor thresholds. An explicitly authorized internal duplicate reviewer can inspect encrypted, pseudonymous comparison evidence. Duplicate enrollment is quarantined as `DUPLICATE_REVIEW`; it never merges identities or exposes the other profile to the applicant. Resolution is coded/audited and idempotent. Allowing two distinct people does not override the ambiguity decision when both later compete for a scan. Re-enrollment creates a new grant/template and supersedes/deletes the old active template only after successful fresh evidence.

## Calibration and approval

KYC's 1:1 calibration and a `VERIFIED` verdict do not approve 1:N identification. The protocol example deliberately contains null thresholds, targets and sample counts and cannot enable the feature. Select decision parameters on development identities, lock the complete protocol before held-out evaluation, then run:

```sh
PYTHONPATH=src .venv/bin/python scripts/evaluate_face_identity.py \
  --protocol /private/frozen-protocol.json --dataset /private/held-out-data.json \
  --output var/face-identity-evaluation.json
```

The private dataset supplies `protocol_sha256`, `evaluation_started_at`, `synthetic`, disjoint `development_subjects`/`evaluation_subjects`, a `gallery`, `probes`, `pad_trials` and `verification_trials`. Gallery/probe records contain opaque `subject`, the compatible `model_id`, a normalized 128-dimensional `embedding` and `population`. A gallery population classifies the enrollment; a probe population names the authorized gallery being searched. Each independent probe also supplies predeclared `camera`, `lighting`, `appearance`, `user_group`, optional capture recovery outcome, and measured `end_to_end_ms`. PAD trials supply independent held-out subject, boolean attack label and provider acceptance. Verification trials supply reference/probe records. See `evaluation.py` and its synthetic contract tests for the exact schema. Do not put this biometric dataset in the repository.

The report contains aggregates and hashes only: open-set FPIR, genuine-search FNIR (including failed capture), ambiguity/retry rates, PAD attack acceptance/genuine rejection and enrollment 1:1 false acceptance/rejection. It reports upper bounds from two-sided 95% Wilson intervals, minimum independent trial checks, camera/lighting/appearance/group coverage, gallery size, exact-search timing and supplied capture-to-authorized-result p95. Repeated probes and PAD observations from the same identity cannot inflate independent sample counts. Correlated/repeated-condition experiments require separate predeclared protocols. The candidate recall value of 1 denotes full exact-gallery enumeration; it is not a claim of perfect identity recognition. Definitions/context: [NIST FRTE 1:N identification](https://pages.nist.gov/frvt/html/frvt1N.html).

The policy predeclares its supported populations. Evaluation uses the same scoped galleries as runtime: a customer known to a different gallery is a non-mate for a staff-only search. Every approved population needs its own genuine/non-mate measurements and gallery size at least the requested validated capacity. The reported `max_population_tested` is the smallest tested approved gallery, not a sum that could hide an untested population. Unsupported populations remain unavailable even if another population has an approval.

Measured comparison time excludes encryption/database/HTTP/camera costs. The end-to-end and PAD inputs must come from the physical-device evaluation harness and be independently reviewed. The tool cannot establish their provenance merely from JSON, and it does not approve its own output. Test success and a zero observed error rate in a small sample cannot serve as biometric validation. No real FPIR/FNIR/PAD/device/population accuracy or production latency has been measured in this implementation task.

An independent authority signs a canonical JSON payload with Ed25519. `FACE_RECOGNITION_APPROVAL_KEY` points only to its public key. The bundle is `{ "payload": { "policy": ..., "evaluation": ..., "approval": ... }, "signature": "BASE64_ED25519_SIGNATURE" }`; sign `policy.canonical(payload)`. `approval` contains `scope="face-identification"`, `approved_by`, timezone-aware future `expires_at`, `evaluation_sha256`, and reviewed evidence references for `device_validation`, `pad_validation`, `occlusion_validation`, `backup_restore_validation`, `security_review`, `enrollment_verification_validation`. The signature covers policy, evaluation and those references together. Signing keys and actual approvals are not supplied by the app.

Release evidence references must be 64-character SHA-256 hashes of the reviewed evidence, not boolean approval flags. The loader verifies signature, expiry, policy/report hash binding, compatible pinned model, independent/predeclared non-synthetic evaluation, required measurement bounds, validated population and those references. Session/enrollment `policy_version` stores the canonical policy's SHA-256, including its human version label and all decision parameters. Reusing a version label cannot silently change thresholds for old sessions/templates. Model or policy changes require planned re-enrollment or an explicitly evaluated migration; incompatible templates are excluded.

## Withdrawal, retention, backup and restore

Withdrawal immediately revokes grants, invalidates pending enrollment sessions/references and removes active/pending template rows. No vector index needs separate deletion. It first writes a durable HMAC-chained denial outside the database backup set. The same tenant lock prevents a new match/profile operation from overtaking the withdrawal transaction. Expired/revoked/withdrawn/suspended entries are excluded on every search and profile lookup.

Schedule privileged maintenance at least every minute per tenant:

```sh
PYTHONPATH=src .venv/bin/python scripts/manage_face_identity.py maintain --organization ORG_UUID
```

It deletes expired enrollment templates, clears expired capture evidence and references, purges 24-hour replay receipts, clears expired profile copies, removes encrypted internal match evidence after seven days and purges events after thirty days. New KYC erasure covers feature profiles, actors, consents, templates, temporary samples and references as well as original KYC data; KYC verdict/audit preservation remains unchanged. If a ledger append fails, deletion still disables/removes active data. A pending, unsynchronized denial blocks identification until maintenance successfully synchronizes the journal. Journal loss/corruption/checkpoint rollback makes recognition fail closed.

Backups can retain encrypted deleted templates until their separately approved backup retention expires. The app cannot claim to have deleted copies from an external backup provider that has not been configured. The policy/notice carries `backup_retention_days`, which must match actual approved operations. Key rotation does not instantly erase individual records from backups that still have decryptors. Reseal active/pending templates with `rotate-templates`; retain previous decryptors only for the documented session/review/backup lifecycle, then retire them through secret management.

Before backup/restore drills, export the journal checkpoint with `checkpoint --output PATH` and place it in independent immutable operator storage. Before exposing any restored database:

```sh
PYTHONPATH=src .venv/bin/python scripts/manage_face_identity.py verify-restore \
  --organization ORG_UUID --trusted-checkpoint /independent/trusted-checkpoint.json
```

This verifies the external checkpoint/chain and removes restored enrollments covered by denials. Searches also consult the independent journal, so a restored `ACTIVE` row does not by itself restore consent. Local tests restore deleted enrollment/template/consent rows while retaining the external journal and prove denial plus physical cleanup; they also test journal truncation/tampering. **They do not prove cloud backup expiry or an external immutable checkpoint deployment.** Restoring both an old database and its old journal/checkpoint together cannot be detected by local files alone. That is why independent checkpoint custody, measured backup expiry and a deployed restore drill are release blockers. The local journal has a bounded 16 MiB read limit and fails closed at capacity; plan governed journal partitioning/compaction against immutable denials before scaling.

## Readiness matrix

| Area | Implemented / software validation | Production evidence |
| --- | --- | --- |
| KYC verdict isolation and optional consent | Enforced; enrollment requires own verified account; decline/withdraw do not change verdict | Existing KYC release gates unchanged |
| Fresh enrollment and server challenge | Session/ticket/actor binding, ordered two-frame holds, replay/expiry/continuity and calibrated reference gate | Supported physical cameras, attacks and injection evaluation pending |
| Occlusion/visibility | Fail-closed provider contract and rejection of unverified checks | **Blocked: current YuNet five landmarks are insufficient** |
| 1:N decisions | Exact bounded search, no-match/ambiguity rules and policy/model binding | **FPIR/FNIR, ambiguity/retry and realistic population validation unmeasured** |
| Profile authorization | Separate actor/device/purpose/ref checks and minimal allowlists; no sensitive fields | Production account/SSO and device credential delivery integration pending |
| Database isolation | Forced RLS on all nine new tables; separate least-privilege role; scoped erasure | Target deployment role/TLS checks required |
| Encryption | Separate AES-GCM keyring, encrypted transient evidence/profile separation | Managed key custody/rotation and memory/core-dump controls require deployment review |
| Privacy lifecycle | Immediate withdrawal, expiry, deletion journal, restore-denial and retention tests | External backups/checkpoint custody/expiry/restore drill unvalidated |
| User/kiosk UX | English/Khmer, serial capture, stale/hidden-camera rejection and auto-clear browser scenarios | Native iOS/Android, accessibility, camera diversity and Khmer language review pending |
| Operations | Actor/device revocation, tenant lock, durable attempt limit, bounded outcome counters | Distributed capacity/abuse monitoring, dashboards and alerts require deployment validation |
| Attendance | No attendance action is implemented or inferred from recognition | Separate authorization, branch/device, shift and deduplication module required if later requested |

Run `scripts/check_face_identity.py` for independent policy/provider/crypto/ledger/database readiness. It does not promote KYC to production readiness. Do not set `FACE_RECOGNITION_ENABLED=true` until all feature gates and the existing relevant KYC/deployment gates have approved evidence.

## Reproduction and evidence

The encrypted-gallery CPU benchmark ran twenty repetitions per population on arm64:

| Synthetic templates | p50 | p95 |
| --- | --- | --- |
| 100 | 2.504 ms | 2.634 ms |
| 1,000 | 25.478 ms | 25.970 ms |
| 10,000 | 260.643 ms | 265.604 ms |

These measurements include AES-GCM decryption, validated template deserialization and exact comparison. They exclude database, HTTP, camera, quality/PAD and authorization, so they are not production end-to-end latency or biometric accuracy. Reproduce with `scripts/benchmark_face_identity.py --repeat 20 --output PATH`. The complete, explicitly synthetic result is [face-identity-search-benchmark.json](../artifacts/face-identity-search-benchmark.json). It does not approve any enrollment population or enable recognition.

```sh
PYTHONPATH=src .venv/bin/python -m unittest tests.test_face_identity tests.test_face_identity_evaluation tests.test_schema -v
# TEST_DATABASE_URL must name a disposable database ending _test:
PYTHONPATH=src .venv/bin/python -m unittest tests.test_face_identity_postgres -v
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t .
cd Frontend
npm run type-check
npm run build
```

`scripts/test_face_identity_browser.cjs` uses a synthetic Chromium camera and mocked provider responses. It covers matched/ambiguous disclosure, auto-clear/no persistent storage, optional consent, Khmer layout, readiness denial, camera denial, stale checks, and VERIFIED-only enrollment entry. Existing KYC browser/capture/SDK regressions are also retained. The final local run passed all 578 backend tests (including 42 new feature tests) in 67.365 seconds, nine feature browser scenarios and eleven existing KYC browser scenarios. Exact scopes and remaining blockers are recorded in [face-identity-validation.json](../artifacts/face-identity-validation.json); the concise backend result is [face-identity-python-tests.txt](../artifacts/face-identity-python-tests.txt). Browser evidence is `artifacts/face-identity-browser.json` and `artifacts/face-identity-khmer.png`. These are software evidence, not approval artifacts or accuracy measurements.

The complete local run enables both optional native fixtures separately: `KYC_FACE_NATIVE_FIXTURE` is the pinned OpenCV Lena fixture required by the native-model smoke tests, while `KYC_LIVENESS_NATIVE_FIXTURE=realtime-face-liveness-detector/misc/face.jpg` supplies a frontal public face for the photo-transformation attack regression. No threshold or baseline gate is relaxed to accommodate either fixture. PostgreSQL validation used a disposable local database ending `_test`, never the user's KYC database.
