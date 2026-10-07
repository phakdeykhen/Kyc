# Phase 17 — Security and privacy hardening

The user authorized this phase on 6 October 2026. It completes application controls
for spec §§22–23 before load testing (Phase 18) and GCP deployment (Phase 19).

## Design

**Production configuration fails closed.** `ENVIRONMENT=production` refuses a development
API key, a plaintext TCP database connection, missing encryption/HMAC keys, reused key
material between purposes, a wildcard Host allow-list, private webhook destinations,
disabled document consent, an administrative database user or an enabled development capture client. API documentation
defaults off and JSON logging defaults on. Every incoming Host is checked except health
probes. PostgreSQL supports `sslmode=require`, `verify-ca` and `verify-full`; use
`verify-full` with the server CA to authenticate its hostname as well as encrypting
traffic. A Unix socket is accepted only when every configured host is a socket and no
`hostaddr` or libpq service override can introduce TCP. See the
[PostgreSQL TLS documentation](https://www.postgresql.org/docs/18/libpq-ssl.html).

**HTTP responses limit disclosure.** API responses use `Cache-Control: no-store`,
`nosniff`, a restrictive content-security policy, `X-Frame-Options: DENY`, `no-referrer`
and `Cross-Origin-Resource-Policy: same-origin`. Production adds HSTS. Bounded uploads
stay in memory through the largest permitted 64 MiB liveness request, so rejected
oversized parts also cannot spill plaintext to temporary files. Capture and
review pages have explicit policies for their own scripts, images and cameras.
Unexpected exceptions return a generic message and a request ID; validation responses
omit rejected values. Deployment must terminate HTTPS at a trusted proxy and configure
Uvicorn's trusted proxy addresses. HSTS does not itself establish HTTPS.

**Security events avoid identity values.** Refusals have a request ID, method, route
template, status/reason code, canonical organization UUID, client address and credential
type. They omit raw paths, query strings, bodies, credentials and attacker-controlled
organization values. Exception summaries omit exception messages, SQL parameters and
source lines. Known token/key patterns are additionally redacted. Uvicorn raw URL access
logs are disabled. Keep proxy logs free of credential-bearing URLs and bodies too.
This follows the data-minimization guidance in the
[OWASP logging checklist](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html).

**Credentials expire and cannot delegate more authority.** Reviewer tokens now have
expiry and operator rotation/deactivation. Pre-phase-17 tokens without expiry are
refused in production and must be rotated. API-key IPv4/IPv6 CIDR allow-lists use the
connection's client address; IPv4-mapped IPv6 addresses are normalized. Child keys
inherit their creator's restrictions and exact expiration, and cannot widen scope,
rate, network access or lifetime. CIDRs are not device-token restrictions. An API key's
existing child keys are independent credentials; revoking a parent does not revoke them.

**Consent precedes document processing.** `POST /v1/kyc/{id}/consent` records an explicit
`DOCUMENT_PROCESSING` grant under the current policy and is idempotent. Server and device
tokens can record it for their authorized session; the integrator must display the policy
and collect the person's agreement first. Production document upload refuses missing,
revoked or stale-policy consent before storage or OCR. Existing explicit biometric
consent remains required for selfies. Erasure handles consent withdrawal.

**Erasure removes personal artifacts while preserving coded history.** A server key with
the independent `data:erase` scope can call `POST /v1/kyc/{id}/erase`; the operator script
can process every session for a user reference within one organization. Sessions are
locked; unfinished ones are closed, device tokens and idempotency identifiers are cleared,
and personal/biometric artifacts are deleted. Consent/user references become `ERASED`,
consents are revoked, stored webhook user references are rewritten and review free-text
notes are removed. Status, risk/review reason codes and audit history remain. Erasure is
idempotent and tenant scoped. A coded audit event records counts, never the deleted values.

Database changes commit before encrypted object cleanup is scheduled. A failed commit
does not delete captures. Cleanup failures log a count and the retention sweep retries
orphan removal after its 15-minute grace. Therefore the HTTP response proves database
erasure; process interruption or storage failure can delay physical object removal.
Backups and payloads already delivered to partners have separate lifecycle obligations.

**Audit and decision history stays append-only.** The API role has no direct UPDATE or
DELETE on `audit_logs`, `risk_assessments` or `manual_reviews`. The schema's
`erase_review_notes(uuid)` function is the sole narrow exception: it can set only a
review note and its key version to NULL for an already-erased session in the current
tenant. It cannot alter the coded decision. Its security-definer search path is fixed;
PUBLIC execution is revoked. Migrations and maintenance use a separate administrative role.

**Encryption rotation includes actual files.** Captures, identity fields, biometrics
and webhook secrets have separate AES-256-GCM keyrings and the document lookup HMAC key
is independent. Legacy webhook secrets remain readable via the PII keyring until resealed.
Inventory checks authenticated file envelopes as well as DB ciphertext versions,
including unreferenced capture objects. Rekey preserves associated-data binding and
atomically replaces files; an interrupted DB update can be repaired from the authenticated
envelope. No key can be retired while old live data or orphans still use it.

**SDKs protect transport.** Python and TypeScript clients require HTTPS for remote APIs,
allow loopback HTTP for development, reject credentials/query/fragment in API URLs and
refuse redirects so custom credential headers or captures cannot reach a redirect target.
Custom transports must enforce the same guarantees.

## Files and migrations

```text
config/production.env.example       production configuration template, no secrets
docs/architecture-phase17.md        design, operations and limits
migrations/versions/
  0011_phase17.py                   CIDRs, reviewer expiration, erased timestamp
  0012_phase17_finalize.py          nullable erased review notes and narrow DB function
requests/phase17.postman.json       consent, network restriction and erasure checks
scripts/
  security_check.py                 effective privilege/RLS audit and API-role probes
  rotate_keys.py                    version inventory and resealing
  erase_subject.py                  one session or all sessions for a tenant user
src/kyc/
  core/{hardening,observability}.py  production gate and privacy-safe security logging
  db/privileges.py                  authoritative API-role privilege matrix
  services/{consent,erasure,rekey}.py
  tenancy/network.py                normalized CIDR rules and containment
  webhooks/secrets.py               independent webhook secret encryption
tests/
  test_hardening.py                 configuration, HTTP, credentials and logging
  test_privacy.py                   consent, erasure, rollback and complete key rotation
  test_rekey.py                     actual envelopes, interrupted rotation and orphans
  test_sdk_security.py              HTTPS configuration and real redirect refusal
  test_postgres_phase17.py          live restricted-role erasure/function isolation
```

Existing configuration, API routes/schemas, models, reviewer tooling, SDKs, capture UI,
bootstrap, Compose and package metadata are updated. `0011_phase17` had already been
applied locally; corrective schema work uses `0012_phase17_finalize` rather than
rewriting the applied revision. Downgrade after erasing review notes is refused rather
than recreating the deleted notes; retain the nullable schema. Run bootstrap to upgrade
and reset privileges:

```sh
.venv/bin/python scripts/configure_local.py
PYTHONPATH=src .venv/bin/python scripts/bootstrap_local.py
PYTHONPATH=src .venv/bin/python scripts/security_check.py --json artifacts/phase17-security-check.json
```

Bootstrap requires `MIGRATION_DATABASE_URL`; the API deployment must never receive it.
The privilege checker includes effective PUBLIC/inherited permissions, column updates,
RLS and append-only probes. A failure requires remediation before production.

## Environment and Docker

New settings are `WEBHOOK_SECRET_KEYS`, `ALLOWED_HOSTS`, `EXPOSE_API_DOCS`,
`ENABLE_CAPTURE_CLIENT`, `REQUIRE_DOCUMENT_CONSENT`, `DOCUMENT_CONSENT_POLICY_VERSION`,
`REVIEWER_TOKEN_MAX_DAYS` and `LOG_FORMAT`. See [.env.example](../.env.example) and
[production.env.example](../config/production.env.example).

`configure_local.py` preserves existing secrets and appends a new independent webhook
key. Docker Compose supplies that key to both API and webhook workers, and passes the
Host/consent/reviewer/logging settings and docs/capture switches. The API and webhook
containers run as UID 10001 without Linux capabilities and with a read-only filesystem;
the API has a private writable capture volume. Compose remains
a development configuration; its plaintext database network is refused in production.
Docker execution is pending because Docker is not installed on this machine.

For Phase 19, inject version-pinned secrets using Secret Manager. Its
[Cloud Run integration](https://docs.cloud.google.com/run/docs/configuring/services/secrets)
can provide the existing settings without application secret-fetching code. Direct
Cloud KMS envelope encryption, CMEK GCS storage and deployment/IAM have not been
implemented or provisioned in this phase.

## API and operator checks

Provision a separate erasure key; ordinary default keys do not get `data:erase`:

```sh
PYTHONPATH=src .venv/bin/python scripts/manage_tenants.py key create "$ORG" "Privacy operator" \
  --scope data:erase --expires-in-days 7 --allow-cidr 127.0.0.1/32
```

After collecting agreement on the person's device:

```sh
curl --fail --silent --show-error -X POST "$BASE/v1/kyc/$SESSION/consent" \
  -H "Authorization: Bearer $CLIENT_TOKEN" -H "X-Organization-ID: $ORG" \
  -H 'Content-Type: application/json' -d '{"scope":"DOCUMENT_PROCESSING","granted":true}'
curl --fail --silent --show-error -X POST "$BASE/v1/kyc/$SESSION/erase" \
  -H "X-API-Key: $ERASURE_KEY" -H "X-Organization-ID: $ORG" \
  -H 'Content-Type: application/json' -d '{}'
```

Import [phase17.postman.json](../requests/phase17.postman.json) with private environment
variables. Expect missing consent → 422, renewed grant → 201/repeat → 200, missing
erasure scope → 403, another tenant's session → 404 and a revoked device token → 401.

```sh
PYTHONPATH=src .venv/bin/python scripts/erase_subject.py "$ORG" --user-id "$USER_REFERENCE"
PYTHONPATH=src .venv/bin/python scripts/create_reviewer.py rotate "$REVIEWER_ID" \
  --organization "$ORG" --expires-in-days 30
PYTHONPATH=src .venv/bin/python scripts/rotate_keys.py inventory "$ORG"
PYTHONPATH=src .venv/bin/python scripts/rotate_keys.py reseal "$ORG"
```

Rotation order: generate independent new keys with unique version labels, prepend them
while retaining every old key, deploy to all readers/writers, reseal, run retention to
remove old orphan files, inventory every organization, then retire keys only when no
old version remains. The PII lookup HMAC key is not an encryption keyring: changing it
requires rebuilding lookup hashes, which this tool does not implement. Restoreable
backups also need their original keys until their retention expires.

## Limits and validation

Local integration proves application and PostgreSQL behavior; it does not certify a
production deployment or legal compliance. KMS/IAM/CMEK, distributed rate limiting,
mTLS/request signing, SSO/MFA, external log-sink access/retention, backup erasure and
partner deletion remain deployment or separate integration work. There is no physical
chip/device validation or calibrated liveness/face-match claim. Log client addresses
only for a defined security retention period. See [BUILD_PROGRESS.md](../BUILD_PROGRESS.md)
and `artifacts/phase17-*` for the executed validation and its exact results.

Phase 18 requires its own authorization under `Document.md` §31.
