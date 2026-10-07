# Phase 15 — Multi-tenant API and API keys

Status: implemented on 6 October 2026.

## 1. Design (spec §24–§26)

The platform now serves many organizations. Each organization has its own scoped,
revocable API keys. Each request names its organization and proves it. A caller can
only reach its own organization's records: the database's row-level security enforces
this, not just application code.

```
customer backend ──X-API-Key: kyc_… + X-Organization-ID──► API
   │  key looked up by SHA-256 inside that organization's RLS context
   │  → revoked / expired / unknown → 401   organization suspended → 403
   │  → scopes checked per route → 403      per-key rate limit → 429 Retry-After
   │
   ├─ POST /v1/kyc/sessions (Idempotency-Key) ──► session
   ├─ POST /v1/kyc/{id}/client-token ──► kst_… ──► user's phone / browser
   │                                         │  Authorization: Bearer kst_…
   │                                         └─► capture + status for that one session only
   └─ GET /v1/kyc/{id}/result ── identity masked unless results:identity
```

### Credentials

| Credential | Format | Who holds it | Scope |
| --- | --- | --- | --- |
| API key | `kyc_` + 256 random bits | the customer's server | listed scopes |
| Session client token | `kst_` + 256 random bits | the end user's device | `session:capture` on one session |
| Reviewer token (Phase 14) | `rvw_` + 256 random bits | a reviewer | review API only |
| Development key | from `.env` | local development and tests | every scope, one organization |

- Only the SHA-256 of each credential is stored. They carry 256 random bits, so a hash
  cannot be guessed (unlike a password). Secrets are shown once and cannot be recovered.
- A key is looked up after `set_config('app.organization_id', …)`, so the `api_keys`
  row is visible only inside its own organization. The same key presented with another
  `X-Organization-ID` is simply not found (401).
- Keys may expire (`expires_in_days`). They are revoked immediately with
  `DELETE /v1/api-keys/{id}` or the admin script. `last_used_at` is refreshed at most
  once a minute.
- Suspending an organization (`organizations.active = false`) stops all its API keys,
  client tokens and reviewer tokens at once (403). Its data is kept.
- The development key is now optional. It is refused if it starts with a provisioned
  prefix. Production mode stays disabled until Phase 17.

### Scopes

| Scope | Allows |
| --- | --- |
| `sessions:write` | create sessions, upload evidence, issue client tokens, `POST /verify` |
| `sessions:read` | session status and results, with identity fields masked |
| `results:identity` | unmasked identity fields in results |
| `webhooks:manage` | webhook endpoints (Phase 16) |
| `keys:manage` | list, create and revoke the organization's keys |

The reference endpoints `/v1/document-types` and `/v1/countries`, and the
`/v1/organization` profile, accept any API key. A key created through the API can only
have scopes and a rate limit its creator already holds, so `keys:manage` cannot be used
to escalate privileges. `session:capture` belongs to client tokens and can never be
granted to a key.

### Session client tokens

`POST /v1/kyc/{id}/client-token` (`sessions:write`) returns a token for the user's device.
The token:
- works only on paths for that session;
- allows the capture endpoints (documents, selfie, liveness, NFC and their challenges)
  and `GET /v1/kyc/{id}` (status);
- gets 403 on results, verify, other routes and key management;
- gets 401 for any other session or organization;
- expires with the session;
- is replaced, and the old one revoked, when a new one is issued.

This means an API key never needs to ship inside a mobile app or web page. The result,
with its identity data, stays between the platform and the customer's backend.

### Masking by permission (spec §25)

Without `results:identity`, the result shows only the first letter of each name word
(`S** S*****`) and the birth year (`1990-**-**`). Sex and nationality stay visible, the
document number stays masked as before, and `identity_masked: true` is set. Reviewers
see what their own roles allow (Phase 14).

### Idempotent session creation

`POST /v1/kyc/sessions` accepts `Idempotency-Key` (8–128 characters from `A-Z a-z 0-9 . _ : -`).
- A retry with the same key and the same body returns the original session with
  `200` and `Idempotent-Replayed: true`.
- The same key with a different body returns `409`.
- Keys are unique per organization (a database constraint). A concurrent duplicate is
  caught by a savepoint and replayed, not duplicated. Keys do not expire.

### Rate limits

Each credential has a fixed one-minute window:
- API keys use their own `rate_limit_per_minute` (default 600).
- Client tokens use `CLIENT_TOKEN_RATE_LIMIT_PER_MINUTE` (default 120).
- The development key uses `API_RATE_LIMIT_PER_MINUTE`.

Over the limit returns `429` with `Retry-After`. The counter is held in each process (see Limits).

### Audit

`audit_logs` record `API_KEY_CREATED`, `API_KEY_REVOKED`, `CLIENT_TOKEN_ISSUED` and
`SESSION_CREATE_REPLAYED`. The admin script also records the `ORGANIZATION_*` actions.
Each session event's actor is now the credential, `api_key:<id>` or `session_client:<id>`,
and `kyc_sessions.created_by` records which key created the session.

### Provisioning

`scripts/manage_tenants.py` uses the migration role and writes each organization inside
its own RLS context. It can:
- create, show, list, suspend and reactivate organizations;
- set per-organization retention;
- create, list and revoke keys.

The API role (`kyc_app`) can read organizations but never change them. On `api_keys` it
may `SELECT` and `INSERT`, and `UPDATE` only `last_used_at` and `revoked_at`.

## 2. Files

```
src/kyc/tenancy/keys.py            credential formats, scopes, hashing
src/kyc/tenancy/ratelimit.py       per-credential fixed-window limiter
src/kyc/api/dependencies.py        ~ API key / client token / development key authentication; require(scopes)
src/kyc/api/tenant_routes.py       + GET /v1/organization; GET/POST /v1/api-keys; DELETE /v1/api-keys/{id}
src/kyc/api/routes.py              ~ scopes per route; Idempotency-Key; POST /v1/kyc/{id}/client-token
src/kyc/services/tenancy.py        + organization view, key create/list/revoke, audit
src/kyc/services/sessions.py       ~ idempotent creation, client-token issue, created_by
src/kyc/services/results.py        ~ identity masking without results:identity
src/kyc/review/access.py           ~ suspended organizations block reviewers
src/kyc/db/models.py               + ApiKey; Organization.active; KYCSession idempotency/client-token columns
migrations/versions/0009_phase15.py  + api_keys (forced RLS), columns, unique (organization, idempotency key)
scripts/manage_tenants.py          + organization and key administration
scripts/bootstrap_local.py         ~ api_keys grants; development organization optional
tests/test_tenancy.py              + 18 tests; tests/test_postgres.py ~ api_keys RLS
```

## 3. Environment

| Variable | Meaning |
| --- | --- |
| `DEVELOPMENT_API_KEY`, `DEVELOPMENT_ORGANIZATION_ID` | Now optional; local all-scope key for one organization |
| `API_RATE_LIMIT_PER_MINUTE` | Development key limit and ceiling for keys created through the API (default 600) |
| `CLIENT_TOKEN_RATE_LIMIT_PER_MINUTE` | Per session client token (default 120) |

## 4. Security concerns and limits

- **Rate limits are per process.** With N API instances a key can make N × its limit.
  Phase 19 moves the counter to Memorystore. There is no global per-organization quota yet.
- **Keys are bearer secrets.** Anyone holding one can use it until it is revoked. There
  is no IP allow-list, mTLS or request signing yet (Phase 17).
- **Client tokens are bearer secrets on user devices.** They are limited to one
  session's capture steps and expire with it, so a leaked token can at most add
  evidence to that session. That evidence still goes through every server-side check.
- **`keys:manage` is powerful.** It cannot escalate scopes, but a compromised key with it
  can create equal keys. Give it only to a small administrative key.
- **Hashes are unkeyed SHA-256.** With 256-bit random secrets, a stolen `api_keys` table
  cannot be reversed or guessed against offline, so no pepper is used.
- **`org list` needs a role that bypasses RLS.** That is fine locally because the
  migrator owns the database. In Cloud SQL, use a separate administrative role.
- Organization records and retention are changed only by the admin script. There is
  no organization self-service API and no billing or quotas.
