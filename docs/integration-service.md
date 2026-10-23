# Multi-tenant integration service (v1)

The API supports POS, HR, schools, banking/fintech, customer onboarding and other authorized integrations through the same verification contracts. The KYC engine does not implement attendance, employee lookup, financial eligibility, payments or account access. An integrating backend uses the returned external subject ID to look up its own business record and applies its own authorization and policy.

**Production KYC and identification remain disabled by default.** A sandbox result is synthetic even when its outcome is `VERIFIED`. Synthetic security tests and UI screenshots do not establish document, liveness, or identification accuracy.

## Model and isolation

| Concept | Implementation |
| --- | --- |
| Tenant | Existing `Organization`; no parallel tenant database or duplicate tenant identifier |
| Application | Integration configuration and separately hashed credentials inside one tenant |
| External subject | `(organization_id, external_id)`; the same ID in other tenants is independent |
| Verification attempt | Existing KYC session plus an application/subject/workflow binding |
| Final result | Versioned decision/check snapshot; permitted identity values stay encrypted |
| Face enrollment | Existing separately consented enrollment in the biometric service |
| Registered device | Application/purpose/population-bound device with a revocable hashed token |
| Face capture session | Short-lived authorization envelope bound to a subject or registered device |

Composite foreign keys prevent tenant-mismatched links. All 42 tenant tables have forced PostgreSQL RLS; the migration and privilege matrix include the new tables. Files and encrypted fields retain tenant-associated encryption contexts. Jobs/outbox workers enter transaction-local tenant context. Rate/quota keys include the tenant; recognition queries, frame replay receipts and deletion-ledger entries are tenant scoped. The face engine's shared model weights contain no enrollment population.

Application API keys cannot access the legacy tenant-wide session, key, webhook or administration APIs. Session tokens can capture for one attempt and read applicant status; they cannot retrieve authoritative results. Face capture tokens also bind application, purpose, population, intended subject/device and expiry. Device tokens alone cannot create searches.

## Install and configure

1. Apply Alembic revision `0014_integrations`, after the existing face-identity migration. Re-run `scripts/bootstrap_local.py` to apply the declared runtime grants. Reapply the separate biometric-role grants with the existing face configuration tooling: that role now also needs read access to verification bindings. Back up and review migrations before deploying to a live database.
2. Set `INTEGRATION_PUBLIC_BASE_URL` to the public **HTTPS origin** serving FastAPI's `/verify/{id}` and `/verify-assets/*`. It must not contain a path, credentials, query or fragment. Configure `ALLOWED_HOSTS`, TLS termination and existing production settings.
3. Provision a tenant with `scripts/manage_tenants.py`. A tenant operator needs `applications:manage`, the scopes it will grant, and (as applicable) `devices:manage`, `webhooks:manage`, `usage:read` and `data:erase`.
4. Create an application and its credentials. Keep tenant administration and application secrets in server secret managers. Never embed these secrets in frontend/mobile bundles, URLs or screenshots.
5. Keep `scripts/deliver_webhooks.py` running; background notification is an optimization, and the persistent outbox is the retry source. Continue the existing tenant retention/erasure/rekey workers. Retention clears expired result identity envelopes and usage buckets; rekey inventories and reseals `verification_results` before retiring PII keys.

Tenant quota configuration:

```sh
python scripts/manage_tenants.py org quota TENANT_UUID --daily-sessions 10000
```

Application configuration uses `POST /v1/applications` and full replacement with `PUT /v1/applications/{id}`. The sandbox/production mode is immutable; create a separate application to change environments. `DELETE` disables an application, its credentials and outstanding capture access.

```json
{
  "name": "School HR integration",
  "mode": "sandbox",
  "branding": {"display_name": "Example School", "color": "#164e63"},
  "workflows": {
    "staff-onboarding": {
      "country": "KH",
      "expected_document_type": "KH_NATIONAL_ID",
      "verification_level": "DOCUMENT_FACE_LIVENESS"
    }
  },
  "return_urls": ["https://client.example/kyc/return"],
  "identity_fields": [],
  "check_fields": ["expiry", "liveness", "face_match"],
  "populations": ["STAFF"],
  "purposes": ["self_profile", "staff_lookup"],
  "profile_fields": ["kyc_status"],
  "daily_session_quota": 1000,
  "rate_limit_per_minute": 120
}
```

Callback/webhook URLs are operator-configured HTTPS destinations, checked against private, loopback, link-local, metadata and mixed public/private DNS addresses. Return URLs must match a configured URL **exactly**; no suffix, wildcard, prefix or arbitrary `next` parameter is accepted. The server never fetches browser return URLs. Webhook delivery re-resolves and pins a validated public IP, verifies TLS against the hostname, and refuses redirects. Public session requests do not accept a webhook URL.

Issue application credentials with `POST /v1/applications/{id}/credentials`, for example:

```json
{"name":"HR backend","scopes":["sessions:write","sessions:read","results:checks","usage:read"],"expires_in_days":90,"rate_limit_per_minute":120}
```

Available application scopes: `sessions:write`, `sessions:read`, `results:identity`, `results:checks`, `enrollments:write`, `recognition:write`, `recognition:read`, `usage:read`, `data:erase`. They cannot exceed the operator's scopes, expiry, networks or limits. Identity/check access requires both the credential scope and the application's field allowlist. No masked identity fields are implicitly disclosed by this public result API.

Rotate by issuing a second application credential, deploying it to the backend, then deleting the old credential at `/v1/applications/{id}/credentials/{credential_id}`. Secrets are shown once and stored only as hashes. Existing session tokens have their own short lifetime; disable the application to revoke all its hosted access immediately.

Application and credential request limits use shared database minute buckets across API workers. Daily UTC tenant/application quotas include both verification and optional face-session creation. Quota failures return `429` and `Retry-After`; idempotent creation retries do not consume another session quota. Capture endpoints retain their per-session/actor admission limits.

## Quick start: hosted verification

For a synthetic sandbox credential, after provisioning the tenant:

```sh
python scripts/provision_sandbox.py --tenant TENANT_UUID \
  --return-url https://client.example/kyc/return --output var/sandbox.json
```

The file is created exclusively with mode `0600`; use synthetic subjects/images only. It contains a 30-day application credential and a sample workflow. No credentials are committed to this repository.

The backend authenticates its own employee/customer and creates an attempt:

```sh
curl "$KYC_BASE_URL/v1/verification-sessions" \
  -H "X-Organization-ID: $KYC_TENANT_ID" \
  -H "X-API-Key: $KYC_API_KEY" \
  -H 'Idempotency-Key: onboarding-456-attempt-1' \
  -H 'Content-Type: application/json' \
  --data '{"external_subject_id":"EMP-00123","workflow_id":"staff-onboarding","reference":"onboarding-456","return_url":"https://client.example/kyc/return","locale":"km"}'
```

Response fields are `session_id`, `hosted_verification_url`, `capture_token`, `expires_at`, `capture_expires_at`, `sandbox`. IDs are opaque UUIDs; never derive business meaning or authorization from them. Tokens are valid for at most `INTEGRATION_CAPTURE_TTL_SECONDS` (default 600 seconds), bounded by session expiry.

Send only the hosted URL to the applicant. The token and tenant appear in its URL fragment, which is not sent in HTTP requests. The hosted page removes the fragment and uses tab-scoped session storage to recover a reload; it has no API-key field. Branding, workflow and return URL come from authenticated session context. Document consent and selfie consent precede uploads. Recognition consent is separate. Main consent/completion copy supports Khmer; some capture/help guidance remains English and needs localization review.

Retry an identical creation with the same `Idempotency-Key` to recover the same session and issue a fresh capture token; this revokes the previous capture token without extending the session lifetime. A changed body returns `409`. Completed attempts return no new capture token. Start another attempt when the session is closed or expired.

NFC-required workflows use `DOCUMENT_FACE_LIVENESS_NFC`. The existing authenticated NFC evidence/challenge API remains available to authorized capture devices. Browsers cannot read ePassport chips: the hosted page explicitly explains that limitation and does not skip required NFC or claim success. A validated native NFC capture/handoff remains a deployment blocker for those workflows.

The redirect/return link is navigation only. It carries no signed result or authorization. The backend retrieves:

```sh
curl "$KYC_BASE_URL/v1/verification-sessions/SESSION_UUID" \
  -H "X-Organization-ID: $KYC_TENANT_ID" -H "X-API-Key: $KYC_API_KEY"
curl "$KYC_BASE_URL/v1/verification-sessions/SESSION_UUID/result" \
  -H "X-Organization-ID: $KYC_TENANT_ID" -H "X-API-Key: $KYC_API_KEY"
```

Before a decision, `outcome` is `null` and `result_version` is zero. Final outcomes are `VERIFIED`, `MANUAL_REVIEW`, `REJECTED`, `EXPIRED`, `TECHNICAL_ERROR`. `MANUAL_REVIEW` may later be superseded by an authorized review; versions strictly increase. Results preserve their outcome/check snapshot after original document retention, while identity envelopes have a separate PII retention deadline and are cleared on erasure. They never expose embeddings, candidate lists or raw face scores.

For a deterministic sandbox scenario only, the backend can `POST /v1/verification-sessions/{id}/sandbox-result` with `{"outcome":"VERIFIED"}` or any other final outcome. Production applications reject this endpoint. Terminal provider/worker failures can call the internal `technical_failure` hook; transient infrastructure errors remain retryable HTTP errors and do not invent a final decision.

The backend sample is [backend.py](../examples/integration/backend.py); it uses the existing secure server transport, not a new mobile SDK. Install `pip install -e sdk/python`, set `KYC_BASE_URL`, `KYC_API_KEY`, `KYC_TENANT_ID`, `KYC_RETURN_URL`, then run `python examples/integration/backend.py`.

## Webhooks and receiver behavior

An operator registers `/v1/applications/{id}/webhooks` with `{"url":"https://client.example/webhooks/kyc"}`. It receives only that application's `verification.result.updated` events. Payloads carry opaque session/application identifiers, outcome, sandbox marker and `result_version`; clients fetch any permitted identity fields over authenticated API. No subscription is required for decision persistence.

```json
{
  "id": "opaque-event-uuid",
  "type": "verification.result.updated",
  "api_version": "2026-10-06",
  "organization_id": "tenant-uuid",
  "created_at": "2026-10-23T00:00:00+00:00",
  "data": {"session_id":"session-uuid","application_id":"application-uuid","outcome":"VERIFIED","result_version":7,"sandbox":true}
}
```

The decision, stable event ID and deliveries commit together, before any HTTP delivery. The sender signs the **exact serialized request bytes** using HMAC-SHA256 over `<timestamp>.<raw-body>`. `KYC-Signature` carries `t=...` and one or more `v1=...` signatures. Verify before parsing or acting. Reject timestamps over 300 seconds old or in the future; persist event IDs for replay/deduplication. A valid signature alone does not make a duplicate new work.

Deliveries are at least once and may arrive out of order. Return 2xx after durable receipt/processing, acknowledge duplicates, and update local state only when the authoritative result version is newer. Keep sandbox results separately from production records. Never use arrival time to overwrite newer results. Retryable receiver failures should return non-2xx.

The runnable [webhook_receiver.py](../examples/integration/webhook_receiver.py) validates raw bodies, timestamps, rotated secrets and expected tenant/application, fetches the authoritative result, and transactionally deduplicates events and compares versions in SQLite. It commits no receipt when processing fails. It performs no attendance, payment, account access or other business action. Set `KYC_APPLICATION_ID`, `KYC_WEBHOOK_SECRETS` and the backend variables above; run:

```sh
PYTHONPATH=examples/integration:sdk/python uvicorn webhook_receiver:app
```

Operators manage signing-secret overlap, endpoint deletion, test delivery, delivery lists and manual redelivery through the existing `/v1/webhooks/{endpoint_id}` API. `POST /rotate-secret` shows a new secret once; both sign during configured overlap. `/deliveries/{delivery_id}/attempts` preserves attempt history across manual redelivery; redelivery retains the event ID. Retries use bounded exponential-style backoff and leases; the default is eight attempts over approximately eleven hours. A worker crash after sending can cause a duplicate, which the receiver must handle.

## Optional recognition

Recognition uses the existing independently keyed biometric service and its approved identification/ambiguity policy. A KYC release cannot enable it. Sandbox verification cannot enroll real faces.

For a backend-authenticated subject after real `VERIFIED` KYC:

```http
POST /v1/face-enrollment-sessions
X-Organization-ID: TENANT_UUID
X-API-Key: APPLICATION_SECRET
Content-Type: application/json

{"external_subject_id":"EMP-00123","verification_session_id":"VERIFIED_SESSION_UUID","purpose":"self_profile","population":"STAFF"}
```

The response supplies an opaque session ID, short-lived `fct_` capture token, purpose/population and expiry. On the person's device, `POST /v1/face-enrollment-sessions/{id}/capture` with that bearer token and `{"consent":true,"notice_version":"CURRENT_NOTICE"}`. The current notice is from the independently approved face policy. A backend application key cannot submit that device endpoint. Capture fresh frames using rotating tickets at `/frames`, then `/complete` with `Idempotency-Key`; a completed live capture is mandatory. Enrollment also verifies the new face against the retained verified document portrait. If that reference is unavailable, retry KYC rather than reusing an old KYC selfie as enrollment.

Device recognition starts with an operator-created device (`POST /v1/applications/{id}/devices`). Its populations/purposes cannot exceed the application. Keep its one-time `kdev_` credential in the authorized kiosk/capture device; revoke with `DELETE /v1/applications/{id}/devices/{device_id}`. Create:

```json
{"device_id":"REGISTERED_DEVICE_UUID","purpose":"staff_lookup","population":"STAFF"}
```

at `POST /v1/recognition-sessions`. The device starts `/recognition-sessions/{id}/capture` and submits frames/completion with the session bearer token **and** `X-Device-Token`. Every call checks revocation, expiry, population and purpose. `customer_service` requires a CUSTOMER population; self recognition requires the backend's authorized subject and its real verified attempt.

The backend reads `GET /v1/recognition-sessions/{id}/result` with `recognition:read`. Outcomes are `MATCHED`, `NO_MATCH`, `AMBIGUOUS`, `RETRY_REQUIRED`, `LIVENESS_FAILED`, `EXPIRED`, `TECHNICAL_ERROR`. Only an authorized current `MATCHED` result includes `recognition_reference`, `external_subject_id` and the explicitly permitted profile fields. Matching searches only eligible live/consented enrollments in the tenant's permitted population, excluding sandbox/unbound legacy enrollments for public integrations. No embeddings, candidate lists or similarity scores are returned.

## Operations, deletion and version policy

`GET /v1/integration/usage` returns scoped counts, current daily usage and recent deliveries. `/v1/integration/dashboard` renders the same tenant/application delivery dashboard as HTML; expose it through an authenticated admin backend proxy, without embedding service credentials in browser code. The tenant operator can list applications/credentials; application readers see their own usage only.

`DELETE /v1/verification-sessions/{id}` erases its PII, artifacts, encrypted result identity, references and capture access while preserving decision codes/history. `DELETE /v1/integration/subjects/{external_subject_id}` erases all attempts within the caller's application scope (or all tenant attempts for an authorized tenant operator). The shared external ID is pseudonymized only after no other unerased application attempts remain. KYC erasure also revokes related biometric enrollment through the existing privileged erasure function and deletion ledger.

OpenAPI is exported at [sdk/openapi.json](../sdk/openapi.json); `/docs`, `/redoc` and `/openapi.json` follow `EXPOSE_API_DOCS`. Regenerate with `python scripts/export_openapi.py`. Within `/v1`, additive fields/events are allowed; consumers must tolerate unknown response fields and event types. Breaking changes require `/v2`, migration guidance and at least 180 days of announced overlap. Deprecated routes will carry `Deprecation` and `Sunset` headers when dates are actually scheduled; no sunset is currently scheduled.

## Release blockers

The KYC integration gate defaults closed and, outside tests, requires production configuration plus validated comparison/liveness settings. Configuration declarations alone do not constitute validation; complete the existing [KYC readiness report](production-readiness-2026-10-07.md), representative document/attack evaluation, operational review and deployment checks before opening it.

Identification additionally requires its independently signed, unexpired calibration policy, validated visibility/liveness provider, representative identification/ambiguity measurements, independent encryption/deletion ledger, restricted biometric DB role and measured population capacity. The current implementation does not supply that approved evidence; identification remains disabled. See [face identity readiness](face-identity.md). Native NFC handoff and full Khmer guidance review remain separate release work. No live deployment, real tenant migration, production secret provisioning, or production enablement is performed by this implementation.
