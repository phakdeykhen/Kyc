# Phase 15 — Multi-tenant API and API keys

Updated 6 October 2026.

## 1. Design (spec §24, §26)

Before Phase 15, every client used one shared `DEVELOPMENT_API_KEY`, bound to one
development organization. Phase 15 lets many organizations use the same API, each with
its own credentials. No organization can reach another's records or keys.

### Credentials

- A key looks like `kyc_<16 hex>_<43 url-safe chars>`. The `kyc_<16 hex>` prefix is public:
  it is stored, listed and safe to log. The full key carries 256 random bits after the
  prefix and is never stored. `api_keys.secret_sha256` holds its SHA-256. A plain hash is
  enough because the secret is random, unlike a password.
- Every request sends `X-Organization-ID` and `X-API-Key`. The key is looked up by prefix
  inside that organization's row-level-security context. A key presented with another
  organization's ID is simply not found (401). Its existence is not revealed, and a
  constant-time comparison runs even when no row matches.
- Keys can expire (≤ 730 days) and be revoked. Rotation issues a replacement with the
  same name and scopes, and the old key keeps working for a grace period (default 24 h;
  0 revokes it at once). `last_used_at` is updated at most once a minute.
- `organizations.active = false` suspends an organization: every key gets 403 and the
  data is untouched. The credential is checked first, so the suspension is only revealed
  to a valid key holder.
- The Phase 1 `DEVELOPMENT_API_KEY` remains an optional fallback for the development
  organization, outside production only. Empty or unset disables it. It holds all scopes
  and is not rate-limited.
- Client keys, reviewer tokens (Phase 14) and the development key are separate. A client
  key never reaches review endpoints, and a reviewer token never reaches client endpoints.

### Scopes

| Scope | Endpoints |
| --- | --- |
| `sessions:create` | `POST /v1/kyc/sessions` |
| `sessions:read` | `GET /v1/kyc/{session}` |
| `captures:write` | document, selfie, liveness and NFC uploads and challenges |
| `sessions:verify` | `POST /v1/kyc/{session}/verify` |
| `results:read` | `GET /v1/kyc/{session}/result` (identity fields) |
| `keys:manage` | `/v1/api-keys` list, issue, rotate, revoke |

Reference data (`/v1/countries`, `/v1/document-types`) and `GET /v1/me` need any valid
key. Without explicit scopes a key gets everything except `keys:manage`. A key holding
`keys:manage` can only issue or rotate keys whose scopes it holds itself, so key
management cannot escalate privileges. A capture-only key (`captures:write` and
`sessions:read`) suits a mobile app or browser that should never see identity results.

### Provisioning

Organizations are created by an operator with `scripts/manage_tenants.py` over the
migration connection: `org-create`, `org-suspend`, `org-activate`, `key-create`,
`key-list`, `key-rotate` and `key-revoke`. Within an organization, the `keys:manage` API
handles the rest. Every creation, rotation, revocation and organization change is
written to `audit_logs`. Requests made with a key are audited as `api_key:<id>` instead
of the shared `development-partner`.

### Rate limits

Each key gets `organizations.api_rate_limit_per_minute` requests per one-minute fixed
window (default 120, range 1–100 000). Responses carry `X-RateLimit-Limit` and
`X-RateLimit-Remaining`, and a 429 carries `Retry-After`. The counter is held in each
process, so N API replicas allow up to N times the limit. A shared Redis counter
(`REDIS_URL` is already reserved) belongs to the load and deployment phases.

### Idempotent session creation

`POST /v1/kyc/sessions` accepts `Idempotency-Key` (1–128 characters of `A-Z a-z 0-9 _ . : -`).
`idempotency_keys` stores the key, a SHA-256 of the canonical request body and the
session ID. Retries behave as follows:
- Same key and same body within 24 h: the original session is returned with
  `Idempotent-Replayed: true`.
- Same key with a different body: 422.
- Two concurrent first requests: the unique constraint lets one through, and the other
  gets 409 to retry.
- A key older than 24 h may be reused.

Keys are scoped per organization.

## 2. Files

| File | Purpose |
| --- | --- |
| `src/kyc/tenancy/keys.py` | Key format, scopes, issuing, revocation, rotation |
| `src/kyc/tenancy/ratelimit.py` | Per-key fixed-window limiter |
| `src/kyc/api/dependencies.py` | Authentication, `TenantContext.scopes`, `Scope(...)` route guard |
| `src/kyc/api/key_routes.py` | `GET /v1/me`, `/v1/api-keys` management |
| `src/kyc/api/routes.py` | Scope on each client endpoint; `Idempotency-Key` |
| `src/kyc/services/sessions.py` | Idempotent creation |
| `src/kyc/db/models.py` | `ApiKey`, `IdempotencyKey`, `Organization.active` / `api_rate_limit_per_minute` |
| `migrations/versions/0009_phase15.py` | Tables, columns, forced RLS on both new tables |
| `scripts/manage_tenants.py` | Operator CLI |
| `scripts/bootstrap_local.py` | `kyc_app` grants: `SELECT, INSERT, UPDATE` on `api_keys` (no DELETE); `SELECT, INSERT, DELETE` on `idempotency_keys` |
| `tests/test_tenancy.py` | 19 tests |
| `requests/phase15.postman.json` | Postman workflow |

The RLS policy count is now 24.

## 3. Validation (6 October 2026)

- **361 tests: 353 passed, 8 skipped, 0 failures**, live PostgreSQL 16 RLS test included
  ([artifacts/phase15-tests.txt](../artifacts/phase15-tests.txt)). The skipped tests need
  Tesseract Khmer fonts and native face models, which were not installed on this runner.
  They do not touch Phase 15 code.
- The live PostgreSQL test checks that `api_keys` and `idempotency_keys`:
  - are invisible across organizations and without tenant context
  - refuse writes under another organization's ID
  - refuse an idempotency record pointing at another organization's session
- Live HTTP: uvicorn connected as the restricted `kyc_app` (NOBYPASSRLS), with
  organizations and keys provisioned by `manage_tenants.py`. All 23 steps matched
  ([artifacts/phase15-live-e2e.json](../artifacts/phase15-live-e2e.json), secrets redacted):
  - an idempotent retry returned the same session, and a different body got 422
  - a key presented with the wrong organization got 401
  - a foreign session read got 404
  - a capture-only key was refused results, session creation and key minting
  - rotation grace and revocation worked
  - the rate limit gave 429 with `Retry-After`
  - a suspended organization got 403
  - `kyc_app` has no DELETE on `api_keys`, and no plaintext key is stored
- The live run found and fixed a bug: the idempotency lookup used `SELECT … FOR UPDATE`,
  which needs the UPDATE privilege that `kyc_app` deliberately lacks. The lock was
  unnecessary because the unique constraint already settles races, so it was removed.
- OpenAPI: [artifacts/openapi-phase15.json](../artifacts/openapi-phase15.json).

## 4. Security notes and limits

- The key secret is shown once. Losing it means rotating or issuing a new one.
- Rate limits are per process (see above). There is no per-IP limit and no limit on
  failed authentication yet.
- Failed authentication and 403 scope denials are not audited. There is no organization
  context to write under for a bad key, and auditing every denial could be abused.
  Monitoring belongs to Phase 17.
- Operators provision organizations from a shell with the migration role. There is no
  operator web console, SSO or MFA (Phase 17).
- Key hashing uses SHA-256 with no server-side pepper. A database dump does not reveal
  usable keys because they are random, but an HMAC with a KMS-held pepper would add a
  layer (Phase 17).
- Webhooks and SDKs are Phase 16. Production mode stays disabled until Phase 17.
