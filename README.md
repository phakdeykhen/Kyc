# Universal Identity Platform — Phases 1–5 and 8–9

Phase 1 implements the architecture contracts, a 17-table PostgreSQL schema with
tenant policies, a frozen Alembic migration, and the KYC session state machine.

Phase 2 adds document capture. Clients upload each document side, and a
deterministic quality gate scores blur, glare, brightness, shadow, coverage,
perspective and resolution. It answers **ACCEPTED** or **RECAPTURE** with
actionable instructions. Accepted captures are stored encrypted (AES-256-GCM) with
retention deadlines.

Phase 3 adds the Cambodia National ID adapter. Once both sides pass the gate, the
document engine corrects perspective, runs Khmer + Latin OCR (Tesseract 5), classifies
each side, and extracts and validates the card fields. It stores them encrypted and
moves the session on to `SELFIE_REQUIRED`. Uncertain readings are flagged for review,
never silently corrected. No final decision is made yet; that is the risk engine
(Phase 13).

Phase 4 adds the Cambodia NSSF member card. Phase 3's extraction became a shared
Khmer label engine (`KhmerLabelAdapter`), so each card type is now a small
`CardLayout`. Each adapter also detects when the photo shows the other Khmer card.

Phase 5 adds Cambodia passport visual fields, a TD1/TD2/TD3 MRZ engine, and
`GenericMRZAdapter` for MRZ-only passport extraction. It validates MRZ check digits,
compares visual fields with the MRZ, encrypts MRZ text and records consistency
metadata. The national ID's back MRZ now uses the same engine. Passing checks mean
the reading is consistent; they do not establish authenticity or a final decision.
Phase 6 adds international document support: the printed data page of any country's
passport (cross-checked with its MRZ), generic national ID and residence cards (TD1/TD2
MRZ plus labelled fronts), and an `issuing_country` check of the MRZ issuer against the
session country. Phase 7 adds the QR/barcode engine: codes on every side are decoded,
parsed (JWS, JSON, key=value, AAMVA PDF417, ICAO VDS), compared with the printed fields,
and verified cryptographically only against keys in `BARCODE_TRUST_STORE`. See
[Phase 6](docs/architecture-phase6.md) and [Phase 7](docs/architecture-phase7.md).

Phase 10 adds active liveness: after the selfie, the person follows a random,
single-use sequence of head movements. Facial landmark geometry checks the movements,
and the person must stay the same throughout. Frames are never stored. The uncalibrated
geometry heuristic returns REVIEW at best, including uncertain flat-face findings;
identical-image replay remains a FAIL. See [Phase 10](docs/architecture-phase10.md).

The [face liveness detector source](realtime-face-liveness-detector/README.md)
is vendored as ordinary files in this KYC repository. Its KYC notes identify the
application's liveness entry points and preserve the upstream documentation and
license.

Phases 8–9 add CPU face detection, selfie quality recapture, aligned face embeddings,
and 1:1 comparison against the accepted document portrait. Selfie submission requires
explicit biometric consent. Face photos and templates are encrypted with separate
keyrings and retention deadlines. The default comparison policy is uncalibrated and
always returns `REVIEW`; its cosine score is not an identity probability. Liveness
and the final risk decision remain later stages.

Phase 15 makes the API multi-tenant. Organizations are provisioned with
`scripts/manage_tenants.py` and get scoped, expiring, revocable API keys (`kyc_…`). Each
key is stored only as a hash and found only inside its own organization's row-level
security context. A suspended organization is locked out completely.
- Routes enforce scopes. Results mask identity unless the key holds `results:identity`.
- `Idempotency-Key` makes session creation safe to retry.
- Per-key rate limits return 429.
- Session client tokens (`kst_…`) let a phone or browser capture evidence for one
  session without ever holding an API key.

See [Phase 15](docs/architecture-phase15.md).

Phase 17 adds production configuration checks, HTTP security headers, privacy-safe
security logging, CIDR-restricted keys, expiring reviewer tokens, current-policy document
consent, personal-data erasure and encryption-key resealing. Remote SDK transports require
HTTPS and refuse redirects. Run bootstrap again to apply `0012_phase17_finalize`, then
`scripts/security_check.py` to verify effective API-role privileges and tenant isolation.
See [Phase 17](docs/architecture-phase17.md) for setup, curl/Postman checks, rotation and
the remaining deployment controls.

Phase 16 adds signed webhooks and SDKs. Every session state change becomes a webhook
delivery in the same database transaction (a transactional outbox).
- **Signing.** Each delivery is signed with HMAC-SHA256 over a timestamp and the body,
  so replays can be refused. Each carries a stable event ID for deduplication.
- **Retries.** Failed deliveries are retried with backoff by the API and by
  `scripts/deliver_webhooks.py`.
- **SSRF.** Targets must resolve only to public addresses. This is checked again at
  every delivery, and the connection goes to the checked address.
- **SDKs.** `sdk/python` and `sdk/typescript` provide server clients, device clients and
  webhook verification. `sdk/openapi.json` is the contract for the mobile SDKs, which
  are not built yet.

See [Phase 16](docs/architecture-phase16.md) and [sdk/README.md](sdk/README.md).

See the design docs ([Phase 1](docs/architecture-phase1.md), [Phase 2](docs/architecture-phase2.md),
[Phase 3](docs/architecture-phase3.md), [Phase 4](docs/architecture-phase4.md),
[Phase 5](docs/architecture-phase5.md), [Phases 8–9](docs/architecture-phase8-9.md))
and [build progress](BUILD_PROGRESS.md).
The original requirements are preserved in [Document.md](Document.md).

## Directory tree and created files

```text
6-KYC/
├── Document.md                    original brief + appended stage tracker
├── BUILD_PROGRESS.md              phase status and validation evidence
├── README.md                      setup, environment, and curl tests
├── pyproject.toml                 Python 3.12+ package definition
├── requirements.lock              pinned runtime dependencies
├── .env.example                   documented variables; no real secrets
├── Dockerfile                     non-root Python 3.13 API image
├── compose.yaml                   local API, PostgreSQL, and migration job
├── alembic.ini
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_phase1.py          frozen schema and RLS policies
│       ├── 0002_phase2.py          capture metadata, one image per side
│       ├── 0003_phase3.py          field provenance and processing metadata
│       └── 0004_phase8_9.py        biometric consent, capture and template metadata
├── src/kyc/
│   ├── main.py                    FastAPI factory and health endpoints
│   ├── api/                      routes, schemas, tenant dependencies
│   ├── core/config.py            typed configuration and production gate
│   ├── db/                       models and tenant-scoped transactions
│   ├── domain/                   enums, canonical fields, state machine
│   ├── engines/contracts.py      independent verification interfaces
│   ├── engines/capture_quality.py  safe decoder + document quality gate (Phase 2)
│   ├── documents/requirements.py required sides per document type (Phase 2)
│   ├── storage/captures.py       AES-256-GCM capture store and keyring (Phase 2)
│   ├── services/sessions.py      transactional session lifecycle and audit
│   ├── services/captures.py      capture orchestration (Phase 2)
│   ├── services/retention.py     expiry purge and orphan sweep (Phase 2)
│   ├── services/documents.py     document engine orchestration (Phase 3)
│   ├── services/results.py       masked client result (Phase 3)
│   ├── documents/adapters/       shared engine; national ID, NSSF, KH passport, generic MRZ
│   ├── documents/{khmer,preprocess,refine}.py  normalization, rectification, digit re-read
│   ├── ocr/tesseract.py          Khmer/Latin OCR engine (Phase 3)
│   ├── mrz/{parser,reader}.py    TD1/TD2/TD3 parsing, dedicated MRZ OCR (Phase 5)
│   ├── core/crypto.py            keyrings and encrypted PII fields (Phase 3)
│   ├── biometrics/               YuNet quality, SFace alignment, embeddings and cosine comparison
│   ├── storage/biometrics.py     independently keyed encrypted biometric templates
│   ├── services/biometrics.py    consented selfie and reference-portrait orchestration
│   └── web/capture/              document + consented selfie camera client at /capture
├── scripts/
│   ├── configure_local.py        generates a private local .env
│   ├── download_face_models.py   pinned model/license provisioning and verification
│   ├── test_capture_client.cjs    dependency-free capture-flow smoke checks
│   ├── bootstrap_local.py        migrates, provisions, and grants rights
│   ├── purge_captures.py         deletes expired captures and orphaned ciphertext
│   └── process_documents.py      processes or retries sessions in DOCUMENT_PROCESSING
├── infra/postgres-init.sh         restricted application database role
├── tests/                        state, ASGI API, schema, and migration tests
├── requests/phase{1..5}.postman.json  document API checks
├── requests/phase8-9.postman.json  consented face-quality and comparison checks
├── artifacts/                    generated OpenAPI, PostgreSQL SQL, test report
└── prototypes/local-review/       earlier reference prototype, outside Phase 1
```

The package subdirectories also include `__init__.py` files. `.gitignore` and
`.dockerignore` exclude credentials, virtual environments, and prototype data.

## Run locally with Docker

Requirements: Python 3.12+ for the setup script and Docker with Compose.

```sh
python3 scripts/configure_local.py
python3 scripts/download_face_models.py --destination var/models
docker compose up -d postgres
docker compose run --rm migrate
docker compose up -d api
```

If you already have an older `.env`, run `python3 scripts/configure_local.py` once.
It appends any missing capture, PII and biometric keys and leaves your existing secrets unchanged.
The Docker image installs Tesseract with the Khmer models. When running Python
directly, install them yourself (macOS: `brew install tesseract tesseract-lang`).

Open `http://127.0.0.1:8000/docs`, or `http://127.0.0.1:8000/capture/` for the
camera client. Use the generated `.env` values for both
`X-API-Key` and `X-Organization-ID` in authenticated requests. Liveness is at
`/health/live`; database/migration readiness is at `/health/ready`.

Stop the containers with `docker compose down`. The PostgreSQL and capture volumes
persist between restarts. Run retention with `docker compose run --rm purge` (schedule
it hourly). Docker is not installed on the build machine, so the Compose commands
have not been executed. The equivalent non-Docker path was run end to end against
live PostgreSQL 18 (see BUILD_PROGRESS.md).

## Run Python directly

Use PostgreSQL with the `kyc_migrator` and `kyc_app` roles created by
`infra/postgres-init.sh`, or start only the Compose database as above.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/configure_local.py
.venv/bin/python scripts/download_face_models.py --destination var/models
PYTHONPATH=src .venv/bin/python scripts/bootstrap_local.py
PYTHONPATH=src .venv/bin/python -m uvicorn kyc.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Schema changes run as an explicit migration operation. The API uses only the
restricted database role. An API start does not create tables or organizations.
Phases 8–9 require schema revision `0004_phase8_9`. Rerun the bootstrap command above
for existing installations to apply migrations and refresh the restricted-role
grants for face artifacts and MRZ results. Alembic alone does not apply role grants.
The face-model provisioner downloads a pinned OpenCV Zoo bundle, verifies SHA-256
digests and keeps both license notices. The API uses local model files and never
downloads weights during a request. Run the provisioner again with `--verify-only`
to check installed files without network access. Missing or corrupt models make
selfie processing unavailable (HTTP 503).

## Environment variables

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `development`, `test` or `production`; production requires the [Phase 17 security configuration](docs/architecture-phase17.md) |
| `DEVELOPMENT_API_KEY`, `DEVELOPMENT_ORGANIZATION_ID` | Optional since Phase 15: a local all-scope key bound to one organization |
| `DATABASE_URL` | Restricted application connection, `postgresql+psycopg2://...` |
| `MIGRATION_DATABASE_URL` | Separate owner connection used only for migration/bootstrap |
| `DEVELOPMENT_API_KEY` | Generated secret, minimum 32 characters |
| `DEVELOPMENT_ORGANIZATION_ID` | UUID bound to the development credential |
| `POSTGRES_PASSWORD` | Local Compose migration-role password |
| `KYC_APP_PASSWORD` | Local Compose restricted-role password |
| `SESSION_TTL_SECONDS` | Session lifetime, 60–3600 seconds; default 900 |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | Bounded API database pool; defaults 5 and 5 |
| `CAPTURE_ENCRYPTION_KEYS` | `version:base64(32 bytes)[,older:key]`; first entry encrypts, others decrypt. Uploads return 503 without it |
| `CAPTURE_STORAGE_DIR` | Ciphertext directory; `/var/lib/kyc/captures` volume in Docker |
| `MAX_CAPTURE_BYTES` | Upload limit, default 10 MiB (max 25 MiB) |
| `MAX_CAPTURE_PIXELS` | Decoded pixel limit, default 40 million |
| `MAX_CAPTURE_ATTEMPTS` | Capture attempts per session, default 20 |
| `PII_ENCRYPTION_KEYS` | Keyring for extracted identity fields (same format as capture keys); separate from capture keys |
| `PII_HMAC_KEY` | base64 32-byte key for document-number lookup hashes; set together with `PII_ENCRYPTION_KEYS` |
| `TESSERACT_CMD`, `OCR_LANGUAGES`, `OCR_TIMEOUT_SECONDS` | OCR engine; defaults `tesseract`, `khm,eng`, 20 s |
| `DOCUMENT_PROCESSING_MODE` | `inline` (after the last side is accepted) or `deferred` (worker script) |
| `BIOMETRIC_ENCRYPTION_KEYS` | Independent AES-256-GCM keyring for face templates; same versioned format as capture keys |
| `FACE_MODELS_DIR` | Local pinned YuNet/SFace ONNX models; default `var/models` |
| `MAX_SELFIE_BYTES`, `MAX_SELFIE_PIXELS` | Upload/decoded limits; defaults 5 MiB and 12 million pixels |
| `MAX_SELFIE_ATTEMPTS` | Selfie attempts per session; default 10 |
| `BIOMETRIC_CONSENT_POLICY_VERSION` | Recorded consent-policy version; default `BIOMETRIC-CONSENT-2026.10.1` |
| `FACE_MATCH_POLICY_VERSION` | Version recorded with each comparison; default `SFACE-COSINE-UNCALIBRATED-2026.10.1` |
| `FACE_MATCH_CALIBRATED` | Default `false`: all comparisons return `REVIEW` |
| `FACE_MATCH_PASS_THRESHOLD`, `FACE_MATCH_FAIL_THRESHOLD` | Operator policy settings, defaults 0.363/0.20; inactive for automatic PASS/FAIL while uncalibrated |
| `FACE_MATCH_CALIBRATION_REFERENCE` | Required evidence reference with a distinct policy version when calibrated mode is enabled; setting it does not validate the model |
| `TEST_DATABASE_URL` | Optional isolated live test database, name ending `_test` |
| `API_RATE_LIMIT_PER_MINUTE` | Development-key limit and ceiling for keys created via the API; default 600 (Phase 15) |
| `CLIENT_TOKEN_RATE_LIMIT_PER_MINUTE` | Per session client token; default 120 (Phase 15) |
| `WEBHOOK_DELIVERY_MODE` | `background` (API sends after commit; worker sends retries) or `worker` (Phase 16) |
| `WEBHOOK_TIMEOUT_SECONDS`, `WEBHOOK_MAX_ATTEMPTS` | Per-attempt timeout (10 s) and attempts before `ABANDONED` (8) |
| `WEBHOOK_SECRET_OVERLAP_HOURS` | Hours the previous secret keeps signing after a rotation; default 24 |
| `WEBHOOK_DELIVERY_RETENTION_DAYS` | Finished deliveries purged after this; default 30 |
| `WEBHOOK_ALLOW_PRIVATE_TARGETS` | Development only: allow `http` and private addresses for a local receiver; default false |
| `WEB_CONCURRENCY` | uvicorn worker processes (about one per vCPU); each has its own pool and admission limit (Phase 18) |
| `MAX_CONCURRENT_REQUESTS` | `/v1/` requests in flight per process; 0 = pool capacity minus 3 (Phase 18) |
| `REQUEST_QUEUE_TIMEOUT_SECONDS` | Longest an excess request queues before 503 + `Retry-After`; default 5 |
| `REDIS_URL` | Reserved cache setting; integration is not implemented in Phase 1 |
| `GCP_PROJECT_ID`, `GCS_CAPTURE_BUCKET`, `GCS_BIOMETRIC_BUCKET`, `PUBSUB_TOPIC` | Reserved GCP integration settings |

`.env` is generated with mode 0600 and excluded from version control. Keep the
placeholder `.env.example` public; never publish a populated `.env`.

## Tests and migration checks

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
node scripts/test_capture_client.cjs
(cd sdk/typescript && npm test)              # TypeScript SDK (Node 22.18+)
PYTHONPATH=src .venv/bin/python -m alembic upgrade 0003_phase3:0004_phase8_9 --sql > artifacts/phase8-9-postgresql.sql
```

Fast tests execute the real FastAPI ASGI application and transactional service
against an isolated SQLite test database. They do not open a web server socket.
The frozen migration is upgraded, compared with ORM metadata, downgraded, and
upgraded again. Its PostgreSQL SQL is checked separately for native types and
all seventeen forced RLS policies. SQLite is refused outside test mode.

For a live PostgreSQL RLS check, install the pinned dependencies and set
`TEST_DATABASE_URL` to a dedicated database with a name ending `_test`. Use a
test administrator that can create a temporary schema and role. The test creates
a fresh schema and restricted role, checks tenant visibility and default-deny
access, and removes its resources. It does not modify existing schema tables.

```sh
PYTHONPATH=src .venv/bin/python -m unittest tests.test_postgres -v
```

Quality-gate tests use synthetic, non-personal document images generated in
`tests/images.py`, and no real identity documents are needed or included. The Phase 3
end-to-end test renders a fictional SPECIMEN Cambodian ID with system Khmer fonts and
reads it with real Tesseract. It is skipped automatically when Tesseract `khm` or the
fonts are missing.
Phase 5 also renders fictional Cambodia and foreign passport data pages and tests
real MRZ OCR. These fixtures require the macOS Khmer Sangam MN, Arial and Courier
New Bold fonts, plus Pillow RAQM. Docker supplies OCR models but does not supply
those fixture fonts. Synthetic OCR tests do not establish real-document accuracy.

### Load and performance tests (Phase 18)

Run these against a running API with a provisioned key for a dedicated test organization;
they create real sessions. See [docs/architecture-phase18.md](docs/architecture-phase18.md).

```sh
export KYC_LOAD_API_KEY=...       # from scripts/manage_tenants.py key create ... --rate-limit 100000
python scripts/load_test.py --organization ORG --scenario mixed --concurrency 1,8,32,64 --duration 20
python scripts/benchmark_stages.py cpu
python scripts/benchmark_stages.py pipeline --organization ORG --sessions 8 --parallel 1,4
# OCR tier for DOCUMENT_PROCESSING_MODE=deferred:
python scripts/process_documents.py ORG --parallel 4 --loop 1
```

## Curl checks

From the project root, load the locally generated variables in your terminal:

```sh
set -a
source .env
set +a
```

Create a session. Expect HTTP 201, a UUID4 `session_id`, and status `CREATED`:

```sh
curl --fail --silent --show-error http://127.0.0.1:8000/v1/kyc/sessions \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo-customer","country":"KH","expected_document_type":"KH_NATIONAL_ID","verification_level":"DOCUMENT_FACE_LIVENESS"}' \
  -o /tmp/kyc-session-response.json
```

Retrieve the created session and pending result:

```sh
KYC_SESSION_ID=$(python3 -c 'import json; print(json.load(open("/tmp/kyc-session-response.json"))["session_id"])')
curl --fail --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_SESSION_ID" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID"
curl --fail --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_SESSION_ID/result" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID"
```

The result has `decision: null`, `identity: null`, and no asserted passing checks.
A request without credentials returns 401. A correct credential with another
organization header returns 403. A foreign session queried through the authorized
organization returns 404. A client-supplied `status` or `organization_id` in the
creation body returns 422.

Upload the document front, then the back. Each response carries `capture_status`,
`quality` scores, `reason_codes`, `instructions`, per-side progress and `next_step`:

```sh
curl --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_SESSION_ID/documents/front" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID" \
  -F file=@front.jpg
curl --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_SESSION_ID/documents" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID" \
  -F side=BACK -F file=@back.jpg
```

After both sides pass, the upload response shows `DOCUMENT_PROCESSING`. The document
engine then runs, and a few seconds later the session reads `SELFIE_REQUIRED`. `/result`
then contains the masked document number, identity fields, per-check results
(`document_classification`, `document_data`, `expiry`, `mrz`, and
`mrz_consistency` when MRZ parsing succeeds; `barcode` remains `UNAVAILABLE`),
`review_flags` and `decision: null`. A card that isn't a Cambodian ID
returns the session to `DOCUMENT_REQUIRED` for new photos. Passports
use `-F side=DATA_PAGE`. A poor photo returns `"capture_status": "RECAPTURE"` and
an instruction such as `HOLD_STILL`, and nothing is stored. Further uploads after
hand-off return 409, and more than `MAX_CAPTURE_ATTEMPTS` uploads return 429.

For an NSSF card, create the session with `"expected_document_type":"KH_NSSF"`. Its
result reports `expiry_status: NOT_APPLICABLE` and `mrz: NOT_APPLICABLE`, because the
card prints neither.

### Tenants, API keys and client tokens (Phase 15)

Provision an organization and a server key (rerun bootstrap first to apply `0009_phase15`):

```sh
PYTHONPATH=src .venv/bin/python scripts/manage_tenants.py org create "Acme Bank"
ORG=<organization_id from the output>
PYTHONPATH=src .venv/bin/python scripts/manage_tenants.py key create $ORG "Acme backend" \
  --scope sessions:write --scope sessions:read --scope results:identity --scope webhooks:manage --scope keys:manage
KEY=<the kyc_… key printed once>
```

Create a session idempotently (a retry returns 200 with `Idempotent-Replayed: true`),
then issue a device token:

```sh
curl -s http://127.0.0.1:8000/v1/kyc/sessions -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG" \
  -H 'Idempotency-Key: signup-42-attempt' -H 'Content-Type: application/json' \
  -d '{"user_id":"customer-42","country":"KH","expected_document_type":"KH_NATIONAL_ID"}'
curl -s -X POST "http://127.0.0.1:8000/v1/kyc/$SESSION/client-token" -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG"
# On the device: capture and status only, for this one session
curl -s "http://127.0.0.1:8000/v1/kyc/$SESSION/documents/front" -H "Authorization: Bearer $CLIENT_TOKEN" \
  -H "X-Organization-ID: $ORG" -F file=@front.jpg
```

Manage keys with a `keys:manage` key. Keys created this way get at most the creator's
scopes and rate limit:

```sh
curl -s http://127.0.0.1:8000/v1/organization -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG"
curl -s http://127.0.0.1:8000/v1/api-keys -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG" \
  -H 'Content-Type: application/json' -d '{"name":"read-only reporting","scopes":["sessions:read"],"expires_in_days":90}'
curl -s -X DELETE "http://127.0.0.1:8000/v1/api-keys/$KEY_ID" -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG"
```

Expected refusals:
- a revoked, expired or unknown key, or a key sent with another organization's ID: 401;
- a suspended organization or a missing scope: 403;
- a client token on `/result`, `/verify` or another route: 403;
- a client token on another session: 401;
- too many requests: 429 with `Retry-After`.

### Webhooks (Phase 16)

With a `webhooks:manage` key (`PII_ENCRYPTION_KEYS` must be configured):

```sh
curl -s http://127.0.0.1:8000/v1/webhooks -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG" \
  -H 'Content-Type: application/json' -d '{"url":"https://api.example.com/kyc/events","event_types":["kyc.verified","kyc.rejected","kyc.review.required"]}'
# → {"id": …, "secret": "whsec_…"}  (the secret is shown once)
curl -s -X POST "http://127.0.0.1:8000/v1/webhooks/$ENDPOINT/test" -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG"
curl -s "http://127.0.0.1:8000/v1/webhooks/$ENDPOINT/deliveries?status=ABANDONED" -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG"
curl -s -X POST "http://127.0.0.1:8000/v1/webhooks/$ENDPOINT/rotate-secret" -H "X-API-Key: $KEY" -H "X-Organization-ID: $ORG"
PYTHONPATH=src .venv/bin/python scripts/deliver_webhooks.py --loop 15   # retries; run alongside the API
```

Verify deliveries with `kyc_sdk.verify_webhook` or `verifyWebhook` from the TypeScript SDK
(see [sdk/README.md](sdk/README.md)). A private or non-https URL returns 422. A local
receiver needs `WEBHOOK_ALLOW_PRIVATE_TARGETS=true` and is for development only. The
[Phases 15–16 Postman collection](requests/phase15-16.postman.json) covers tenants, keys,
client tokens and webhooks.

### Passport data-page workflow

Create a Cambodia passport session, then upload one full data-page photograph with
the entire MRZ visible. Use the same authenticated result endpoint as above:

```sh
curl --fail --silent --show-error http://127.0.0.1:8000/v1/kyc/sessions \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo-passport-holder","country":"KH","expected_document_type":"KH_PASSPORT","verification_level":"DOCUMENT_FACE_LIVENESS"}' \
  -o /tmp/kyc-passport-session-response.json
KYC_PASSPORT_SESSION_ID=$(python3 -c 'import json; print(json.load(open("/tmp/kyc-passport-session-response.json"))["session_id"])')
curl --fail --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_PASSPORT_SESSION_ID/documents" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID" \
  -F side=DATA_PAGE -F file=@passport-data-page.jpg
curl --fail --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_PASSPORT_SESSION_ID" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID"
curl --fail --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_PASSPORT_SESSION_ID/result" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID"
```

For another country's passport, create a new session with `"country":"TH"` (or its
appropriate ISO alpha-2 code) and `"expected_document_type":"PASSPORT"`, then upload
`side=DATA_PAGE`. This selects `GenericMRZAdapter`, which extracts a TD3 passport
MRZ only. International visual-zone extraction belongs to Phase 6. The creation
country is client-supplied and does not verify the issuing state.
`/v1/countries` reports country-specific adapters under
`verification_adapters_available` (`KH`) and MRZ-only passport support under
`any_country_document_types` (`PASSPORT`).

Accepted capture hands off to `DOCUMENT_PROCESSING`. Poll session/result again if
processing has not finished; in deferred mode run
`PYTHONPATH=src .venv/bin/python scripts/process_documents.py`. A readable passport
moves to `SELFIE_REQUIRED`. The result includes `checks.mrz`, available
`checks.mrz_consistency`, an alphanumeric masked number such as `*****02C3`, permitted
identity fields, review flags and `decision: null`. A nullable `mrz` object exposes
format, validity, check-digit outcomes and field consistency. Raw MRZ and per-field
provenance are not returned. Generic MRZ-only extraction reports
`checks.mrz_consistency: NOT_APPLICABLE` because it has no independent visual fields.
Visual/MRZ disagreements remain review flags; a generic passport with an unreadable
or invalid MRZ returns to `DOCUMENT_REQUIRED` for recapture.

Import `requests/phase5.postman.json` for separate Cambodia and generic passport
workflows. Set the private collection variables `api_key` and `organization_id`,
adjust `passport_country` for the generic session, and select a file in each
`Upload DATA_PAGE` request. Each workflow captures its own session ID after creation.
Earlier phase collections remain available for the card workflows. Never export a
collection with real credentials.

### Selfie quality and 1:1 comparison

After the document reaches `SELFIE_REQUIRED`, submit a clear, consented selfie:

```sh
curl --fail --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_SESSION_ID/selfie" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID" \
  -F biometric_consent=true -F file=@selfie.jpg
```

Missing or false consent returns 422 before face processing. Quality recapture
returns actionable `instructions`; choose a new photo and submit again. An unusable
document portrait returns `DOCUMENT_REQUIRED` and needs new document photos.
An accepted selfie includes `comparison` with cosine score, result, policy and model
provenance; default uncalibrated comparisons return `REVIEW`. `/result` reports
`face_comparison`, `checks.face_match`, capture-quality review reasons and
`decision: null`. Templates and internal thresholds are never returned.

For `DOCUMENT_FACE_LIVENESS`, accepted submission moves to `LIVENESS_REQUIRED`;
liveness remains unimplemented in Phase 10. `DOCUMENT_FACE` moves to `PROCESSING`
while the later risk engine remains pending. A face comparison does not establish
liveness or document authenticity. Use the
[Phases 8–9 Postman collection](requests/phase8-9.postman.json) for consent and
response checks. At `/capture/`, create a session or enter an existing ready-session
UUID; consent gates front-camera capture and selfie upload.

Schedule `scripts/purge_captures.py` to remove expired face photos, templates and
quality evidence. Organization capture/template retention settings both default
to 24 hours. Expired templates also remove their dependent comparisons.

## Security and deployment limits

Production refuses development credentials; use provisioned API keys. Captures are
encrypted and retention-limited, with independent keyrings for captures, identity,
biometrics and webhook secrets. Local keys live in `.env`; production secret injection,
KMS and CMEK storage are deployment work. Explicit document consent is required in
production, and biometric consent is required before selfie processing. Erasure clears
personal artifacts and free-text review notes while preserving coded decisions and audits.
The quality thresholds remain uncalibrated. No biometric templates are returned by the
public API. Migration
credentials must not be given to the API deployment. See
[Phase 2 security concerns](docs/architecture-phase2.md#4-security-concerns).

Extracted identity fields are encrypted with a separate PII keyring and never logged.
The adapter's layout assumptions and thresholds are uncalibrated. See
[Phase 3 security concerns](docs/architecture-phase3.md#4-security-concerns).

MRZ text is encrypted under the PII keyring; `mrz_results` contains only format,
validity, digit outcomes and field-consistency metadata. See
[Phase 5 security concerns](docs/architecture-phase5.md#5-security-concerns-and-limits)
for unconfirmed passport layout assumptions, date-century heuristics and unsupported
MRZ deviations. The Phase 5 suite passed 175/175 tests with live PostgreSQL; MRZ tenant
RLS and restricted-role real-OCR passport workflows passed. See
[PostgreSQL passport evidence](artifacts/phase5-postgres-e2e.json). The user authorized
Phases 8–9 after Phase 5. The shared workspace also contains the Phase 6 international
adapters and Phase 7 barcode engine. Face-quality usability does not prove visible eyes,
absence of occlusion, document authenticity or liveness. See
[Phases 8–9 limitations](docs/architecture-phase8-9.md#security-and-limits).
