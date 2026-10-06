# Phase 16 — Signed webhooks and SDKs

Status: implemented on 6 October 2026.

## 1. Design (spec §27–§28)

Customer backends learn about session outcomes through signed webhooks instead of
polling. Integrators use Python and TypeScript SDKs that keep sensitive logic on the server.

```
apply_event (any state change) ──► recorded on the DB session
        │ before COMMIT, same transaction
        ▼
webhook_deliveries (outbox): one row per subscribed endpoint, status PENDING
        │ after COMMIT
        ▼
dispatcher (API background threads)      scripts/deliver_webhooks.py (retries / worker mode)
   claim: FOR UPDATE SKIP LOCKED + 5 min lease
   resolve host → refuse non-public IPs → connect to that exact IP (TLS checks the hostname)
   POST JSON  +  KYC-Signature: t=…,v1=HMAC-SHA256(secret, "t.body")
        │
   2xx → DELIVERED      else → retry with backoff, ABANDONED after WEBHOOK_MAX_ATTEMPTS
```

### Events

| Event | When |
| --- | --- |
| `kyc.document.accepted` | the document was read and accepted |
| `kyc.selfie.required` | next step is the selfie |
| `kyc.processing` | all evidence is in; deciding |
| `kyc.review.required` | sent to manual review |
| `kyc.verified` / `kyc.rejected` | final outcome (engine or reviewer) |
| `kyc.recapture.required` | new document photos needed (unreadable, or a reviewer asked) |
| `kyc.expired` | the session expired before completion |
| `webhook.test` | `POST /v1/webhooks/{id}/test`, to that endpoint only |

The first six come from the spec; `recapture.required` and `expired` were added because
an integrator cannot otherwise learn them without polling. An endpoint subscribes to a
list of events, or to all events with an empty list.

The payload is `{id, type, api_version, created_at, organization_id, data}`. `data` holds:
- the session ID, the integrator's own `user_id`, the status and previous status, `session_version` and the level;
- for review and final events, the engine's `decision` (result, reason codes, policy
  version) and the latest `review` (action and reason code).

There are never any identity fields, document numbers, images or reviewer notes. The
receiver fetches `/result` with its API key, and only a key holding `results:identity`
sees the identity unmasked.

### Transactional outbox

Events are recorded where every transition happens: `apply_event`. They become rows in a
`before_commit` hook, so the deliveries and the state change commit or roll back
together. A rolled-back transition never sends a webhook, and a committed one is never
lost if the process dies. The hook runs on every SQLAlchemy session, so the document
worker and scripts produce events too. Their deliveries wait for the retry worker when
no dispatcher is attached.

### Signing, replay protection, idempotency

- **Secret.** Each endpoint has a `whsec_` secret (256 bits), shown once. It is stored
  sealed with the PII keyring (AES-GCM, bound to organization and endpoint), because
  the server must be able to recover it to sign.
- **Signature.** `KYC-Signature: t=<unix>,v1=<hex>` signs the timestamp and the exact
  body bytes. Receivers reject timestamps outside five minutes, which stops replays.
- **Deduplication.** `KYC-Event-ID` is identical across retries and redeliveries, and
  the database enforces one delivery per endpoint per event. Receivers deduplicate on
  it, because delivery is at-least-once.
- **Rotation.** `POST /v1/webhooks/{id}/rotate-secret` issues a new secret. The old one
  keeps signing alongside it for `WEBHOOK_SECRET_OVERLAP_HOURS` (default 24), so the
  header carries two `v1` values and receivers can switch over without dropping events.

### Delivery and retries

- Waits after each failure: 30 s, 2 min, 10 min, 30 min, 1 h, 3 h, 6 h (12 h beyond).
  With the default 8 attempts the last try is about 11 h after the first, and the
  delivery is then `ABANDONED`.
- A `DELIVERED` or `ABANDONED` delivery can be re-sent with `/redeliver`, under the
  same event ID. A delivery still waiting for its retry cannot (409), so it is never
  sent twice at once.
- Claims lock rows with `SKIP LOCKED` and push `next_attempt_at` forward by a 5-minute
  lease, then send with no transaction open. The API's threads and any number of
  workers can therefore run together. A crash after the claim just means a retry after
  the lease.
- `consecutive_failures` is tracked per endpoint. Deleting an endpoint abandons its
  pending deliveries and keeps the history.
- Responses are not stored: only the status code and a short error code (`HTTP_500`,
  `TIMEOUT`, `TLS_ERROR`, `CONNECTION_ERROR`, `TARGET_NOT_ALLOWED`, `ENDPOINT_DISABLED`).
- Finished deliveries are purged by `scripts/purge_captures.py` after
  `WEBHOOK_DELIVERY_RETENTION_DAYS` (default 30).

### SSRF protection

The URL is chosen by a customer, so:
- only `https` is accepted, without credentials or a fragment;
- at creation **and again at every delivery**, the host is resolved and *every*
  address must be public. Loopback, RFC 1918, link-local (including the cloud metadata
  address 169.254.169.254), CGNAT, multicast, reserved and IPv4-mapped forms are refused;
- the connection goes to the checked IP, so DNS rebinding between check and connect
  cannot redirect it, while TLS still verifies the certificate for the hostname;
- redirects are not followed, the response read is capped at 1 KB, and the timeout is 10 s.

`WEBHOOK_ALLOW_PRIVATE_TARGETS=true` lifts the address rule (and allows `http`) for a
receiver on a developer's own machine. The live test shows that a worker without it
refuses such a target at delivery time.

### SDKs

| | Python `sdk/python` | TypeScript `sdk/typescript` |
| --- | --- | --- |
| Server client (API key) | `KYCClient` | `KYCClient` |
| Device client (session client token) | `SessionClient` | `SessionClient` |
| Webhook verification | `verify_webhook` | `verifyWebhook` (Web Crypto) |
| Dependencies | standard library | none (fetch, FormData, Web Crypto) |

Both cover sessions, idempotent creation, client tokens, every capture step, results,
API keys and webhook management. `sdk/openapi.json`, exported by
`scripts/export_openapi.py`, is the contract for generating Kotlin, Swift and Dart
clients. The native mobile SDKs (camera, NFC chip reading) are **not built**;
[sdk/README.md](../sdk/README.md) specifies what they must do. In every case the device
captures and the server decides.

## 2. Files

```
src/kyc/webhooks/events.py         event types; transition → events
src/kyc/webhooks/outbox.py         record in apply_event; materialize before commit; notify after commit
src/kyc/webhooks/signing.py        whsec_ secrets; KYC-Signature sign/verify
src/kyc/webhooks/delivery.py       URL checks, public-address resolution, pinned HTTP(S) sender
src/kyc/webhooks/dispatcher.py     claim/lease, send, record, backoff, abandon
src/kyc/services/webhooks.py       endpoint CRUD, rotation, test events, deliveries, redelivery
src/kyc/api/webhook_routes.py      /v1/webhooks…
src/kyc/services/sessions.py       ~ apply_event records transitions
src/kyc/services/retention.py      ~ purge finished deliveries
src/kyc/main.py                    ~ dispatcher wired to the session factory
src/kyc/db/models.py               + WebhookEndpoint, WebhookDelivery
migrations/versions/0010_phase16.py  + both tables, forced RLS
scripts/deliver_webhooks.py        retry worker (--loop)
scripts/export_openapi.py          sdk/openapi.json
sdk/python/kyc_sdk/                client.py, webhooks.py
sdk/typescript/src/                client.ts, webhooks.ts, index.ts; test/sdk.test.ts
compose.yaml                       ~ webhook settings; `webhooks` worker service (tools profile)
requests/phase15-16.postman.json   tenants, keys, client tokens, webhooks
tests/test_webhooks.py (18), tests/test_sdk.py (3), tests/test_postgres.py ~ webhook RLS
```

## 3. Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `WEBHOOK_DELIVERY_MODE` | `background` | API sends right after commit; `worker` leaves all sending to the worker |
| `WEBHOOK_TIMEOUT_SECONDS` | 10 | per attempt |
| `WEBHOOK_MAX_ATTEMPTS` | 8 | then `ABANDONED` |
| `WEBHOOK_SECRET_OVERLAP_HOURS` | 24 | old secret keeps signing after rotation |
| `WEBHOOK_DELIVERY_RETENTION_DAYS` | 30 | finished deliveries purged after this |
| `WEBHOOK_ALLOW_PRIVATE_TARGETS` | false | development receivers on this machine only |

`PII_ENCRYPTION_KEYS` must be set: endpoint secrets are sealed with it (503 otherwise).

## 4. Security concerns and limits

- **Shared keyring.** Signing secrets use the PII keyring with their own associated
  data. A dedicated KMS key is Phase 17.
- **Ordering.** Background threads and retries mean events may arrive out of order.
  Every event carries `data.session_version`, which increases with each state change:
  receivers should keep the highest version they have seen, not the last to arrive.
- **Background threads are per API instance.** If a process stops, its queued sends are
  picked up by the worker after the lease. Run `deliver_webhooks.py --loop` in every
  deployment. Phase 19 can move this to Pub/Sub.
- **Enumerating organizations.** The worker lists organizations through the migration
  role, which needs a role that bypasses RLS. It can be given organization IDs instead.
- **No auto-disable.** An endpoint that keeps failing is not switched off;
  `consecutive_failures` is reported for operators. There is no per-endpoint rate limit.
- **The `user_id` in payloads** is the integrator's own reference. Integrators should not
  put personal data such as an email address or phone number in it.
- **SDKs.** The SDKs are unpublished (no npm or PyPI release). The mobile SDKs remain to be built.
- **Docker** configuration was updated but not executed: Docker is not installed on
  this machine.
