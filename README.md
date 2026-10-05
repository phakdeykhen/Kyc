# Universal Identity Platform — Phases 1–4

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

See the design docs ([Phase 1](docs/architecture-phase1.md), [Phase 2](docs/architecture-phase2.md),
[Phase 3](docs/architecture-phase3.md), [Phase 4](docs/architecture-phase4.md))
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
│       └── 0003_phase3.py          field provenance and processing metadata
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
│   ├── documents/adapters/       khmer_label.py engine; kh_national_id.py (P3), kh_nssf.py (P4)
│   ├── documents/{khmer,preprocess,refine}.py  normalization, rectification, digit re-read
│   ├── ocr/tesseract.py          Khmer/Latin OCR engine (Phase 3)
│   ├── core/crypto.py            keyrings and encrypted PII fields (Phase 3)
│   └── web/capture/              development camera client at /capture (Phase 2)
├── scripts/
│   ├── configure_local.py        generates a private local .env
│   ├── bootstrap_local.py        migrates, provisions, and grants rights
│   ├── purge_captures.py         deletes expired captures and orphaned ciphertext
│   └── process_documents.py      processes or retries sessions in DOCUMENT_PROCESSING
├── infra/postgres-init.sh         restricted application database role
├── tests/                        state, ASGI API, schema, and migration tests
├── requests/phase{1..4}.postman.json  runnable API checks
├── artifacts/                    generated OpenAPI, PostgreSQL SQL, test report
└── prototypes/local-review/       earlier reference prototype, outside Phase 1
```

The package subdirectories also include `__init__.py` files. `.gitignore` and
`.dockerignore` exclude credentials, virtual environments, and prototype data.

## Run locally with Docker

Requirements: Python 3.12+ for the setup script and Docker with Compose.

```sh
python3 scripts/configure_local.py
docker compose up -d postgres
docker compose run --rm migrate
docker compose up -d api
```

If you already have an older `.env`, run `python3 scripts/configure_local.py` once.
It appends any missing capture and PII keys and leaves your existing secrets unchanged.
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
PYTHONPATH=src .venv/bin/python scripts/bootstrap_local.py
PYTHONPATH=src .venv/bin/python -m uvicorn kyc.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Schema changes run as an explicit migration operation. The API uses only the
restricted database role. An API start does not create tables or organizations.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `ENVIRONMENT` | `development` or `test`; production is deliberately gated in Phase 1 |
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
| `TEST_DATABASE_URL` | Optional isolated live test database, name ending `_test` |
| `REDIS_URL` | Reserved cache setting; integration is not implemented in Phase 1 |
| `GCP_PROJECT_ID`, `GCS_CAPTURE_BUCKET`, `GCS_BIOMETRIC_BUCKET`, `PUBSUB_TOPIC` | Reserved GCP integration settings |

`.env` is generated with mode 0600 and excluded from version control. Keep the
placeholder `.env.example` public; never publish a populated `.env`.

## Tests and migration checks

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m alembic upgrade 0002_phase2:0003_phase3 --sql > artifacts/phase3-postgresql.sql
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
(`document_classification`, `document_data`, `expiry`, with `mrz`/`barcode` as
`UNAVAILABLE`), `review_flags` and `decision: null`. A card that isn't a Cambodian ID
returns the session to `DOCUMENT_REQUIRED` for new photos. Passports
use `-F side=DATA_PAGE`. A poor photo returns `"capture_status": "RECAPTURE"` and
an instruction such as `HOLD_STILL`, and nothing is stored. Further uploads after
hand-off return 409, and more than `MAX_CAPTURE_ATTEMPTS` uploads return 429.

For an NSSF card, create the session with `"expected_document_type":"KH_NSSF"`. Its
result reports `expiry_status: NOT_APPLICABLE` and `mrz: NOT_APPLICABLE`, because the
card prints neither.

Import `requests/phase4.postman.json` to run the equivalent checks. Set the private
collection variables `api_key` and `organization_id`; the collection captures the
session ID after creation. Never export a collection with real credentials.

## Security concerns and next phase

The development credential is not production tenant authentication. Captures are
encrypted and retention-limited, but local keys live in `.env`. Production needs
Secret Manager/KMS and CMEK storage (Phases 17/19). Consent is not yet required
before capture. The quality thresholds are uncalibrated heuristics, and the gate never
judges authenticity. No biometric templates are returned by the public API. Migration
credentials must not be given to the API deployment. See
[Phase 2 security concerns](docs/architecture-phase2.md#4-security-concerns).

Extracted identity fields are encrypted with a separate PII keyring and never logged.
The adapter's layout assumptions and thresholds are uncalibrated. See
[Phase 3 security concerns](docs/architecture-phase3.md#4-security-concerns).

Phase 5 adds the passport and MRZ engine. Per `Document.md`, work pauses after Phase 4
until approval.
