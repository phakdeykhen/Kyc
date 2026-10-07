# Phase 18 — Load and performance testing

Status: implemented on 6 October 2026. The user authorized Phases 17 and 18 together.

## 1. What was measured

Phase 18 measures how the API and the document pipeline behave as load grows, finds
what breaks first, fixes what the measurements show, and records the numbers that size
the Phase 19 deployment.

```
scripts/load_test.py          closed-loop HTTP load: N virtual users, each on one keep-alive
                              connection, sending the next request as soon as the last returns
scripts/benchmark_stages.py   cpu:      CPU cost of each engine, in process
                              pipeline: upload a SPECIMEN passport through the live API and time
                                        how long the session spends in DOCUMENT_PROCESSING
```

Scenarios: `health` (no database), `status_poll` (device polling with a client token),
`result` (integrator read), `create_session` (with `Idempotency-Key`), `document_upload`
(quality gate + encrypted storage) and `mixed` (70% status, 10% result, 10% create,
10% organization). All data is synthetic. The API ran as the restricted `kyc_app` role
against live PostgreSQL with a provisioned API key whose rate limit exceeded the test rate.

**Environment and its limits.** The runs used a shared 12-core developer laptop running
other applications. Its 1-minute load average reached 52 during the OCR runs. The
client, API, PostgreSQL and OCR all shared the machine. Use the numbers to compare
configurations and to see where the system breaks, not as production capacity. Phase 19
must repeat the runs on the target Cloud Run and Cloud SQL sizes (section 5).

## 2. Findings

### 2.1 Defect: the API stalled once 64 or more requests were in flight

| Scenario, single process | 8 users | 32 users | 64 users |
| --- | --- | --- | --- |
| status_poll | 119 rps, p95 86 ms | 119 rps, p95 451 ms | **6 rps, p95 10.4 s, 503s** |
| result | 53 rps | 52 rps | **4 rps, p95 10.7 s, 503s** |
| create_session | 100 rps | 94 rps | **5 rps, p95 10.5 s, 503s** |

Cause: synchronous endpoints run on Starlette's 40-thread pool, and the request's
database session is a `yield` dependency, so a request holds its pooled connection while
it waits for a thread to run the endpoint. With more than 40 requests in flight, every
thread could be waiting for a connection (the pool holds 5 + 5) while every connection
was held by a request waiting for a thread. Nothing moved until the 10-second pool
timeout returned 503. Throughput fell about 20-fold, which a traffic spike would trigger.

**Fix: per-process admission control** (`kyc/core/admission.py`). An ASGI middleware
allows at most `MAX_CONCURRENT_REQUESTS` `/v1/` requests in flight per process. By
default that is the pool capacity minus 3, reserved for the webhook dispatcher and
out-of-transaction audit writes. Excess requests wait on the event loop, which costs
little. A request that waits longer than `REQUEST_QUEUE_TIMEOUT_SECONDS` (5 s) gets
`503` with `Retry-After: 1` and `reason_code: OVERLOADED`. The slot is held until the
application call returns, including background work such as inline document processing.
`/health/*` is never queued.

After the fix the same process kept serving at 64 and 128 users, with latency growing as
queueing (status_poll at 128 users: 81 rps, p95 1.9 s, no errors). Only the `result` read
at 128 users shed 18 requests (5%) with 503 + `Retry-After`, as designed.

### 2.2 Status polls waited on a row lock held through OCR

`GET /v1/kyc/{id}` loaded the session `FOR UPDATE`. `DocumentProcessor.process` holds
that same row lock, and one open transaction, for the whole OCR run (about 5–6 s per
passport). A device polling for its result therefore blocked for the entire OCR.

**Fix:** status reads now load the row without a lock (`get_session(..., lock=False)`).
They take the lock only in the rare case where they must record the session's expiry, and
then re-check under it. The answer is a single row, so it stays consistent. The `result`
endpoint keeps its lock, because it assembles evidence from many tables and the lock is
what gives it one consistent snapshot against concurrent writers. The existing expiry
tests cover the unlocked path.

### 2.3 One Python process is CPU-bound; scale with worker processes

`/health/live` alone peaks at about 650 rps in one uvicorn process, so the event loop
plus request middleware sets the per-process ceiling. Four worker processes (`--workers 4`
via `WEB_CONCURRENCY`; each has its own pool and admission limit):

| Peak throughput, no errors | 1 process (baseline) | 4 workers |
| --- | --- | --- |
| health | 652 rps | 1,430 rps |
| status_poll | 119 rps | 319 rps (32 users, p95 157 ms) |
| result | 53 rps | 124 rps (8 users, p95 112 ms) |
| create_session | 100 rps | 207 rps (32 users, p95 288 ms) |
| mixed | 87 rps | 207 rps (8 users, p95 83 ms) |

At 128 users the 4-worker server answered every request: mixed 186 rps with p95 948 ms
and zero errors.

### 2.4 Documents: OCR is the system's capacity limit

Per-stage CPU cost (`artifacts/phase18-stages.json`, idle machine):

| Stage | p50 |
| --- | --- |
| Capture decode + quality gate (1600×1200 JPEG) | 158 ms |
| Tesseract full page, Khmer + English | 1,851 ms |
| Tesseract full page, English only | 823 ms |
| Face detection / embedding (YuNet / SFace) | 23 ms / 18 ms |
| QR decode | 13 ms |
| MRZ parse, PII seal+open, template seal+open, webhook signature | each under 0.3 ms |
| Encrypted capture write with fsync | 2.6 ms |

Encryption, signing and parsing cost nothing that matters. A passport spends about
5.4–6.2 s in processing end to end, because several OCR passes run per document.

- **Inline mode** (OCR in the API process after the response): upload intake was capped
  at about 1.1 uploads/s, because OCR competed with request handling.
- **Deferred mode** (`DOCUMENT_PROCESSING_MODE=deferred`, OCR in `scripts/process_documents.py`):
  upload intake rose to 16.7 uploads/s at 8 users, with p95 546 ms. That is **15×**.
- **The OCR tier is the bottleneck.** About 9–10 documents/min per serial worker, and at
  most about 24 documents/min with parallel workers on this shared laptop. The API
  accepted documents roughly 40× faster than OCR finished them. One upload test left about
  470 documents queued, and a passport behind that queue waited more than 180 s. Sessions
  expire after `SESSION_TTL_SECONDS` (default 900 s), so a long OCR backlog turns into
  expired sessions.

**Worker fixes.** The document worker processed in batches: `pool.map` waited for the
slowest document before fetching more. Several worker processes all tried the same oldest
session and queued on its row lock for the whole OCR. Now:

- `process_documents.py --parallel N --loop S` keeps N sessions in flight and refills
  each slot as soon as it frees;
- `pending_sessions(..., unclaimed=True)` lists only sessions no worker is processing
  (`FOR UPDATE SKIP LOCKED`);
- `DocumentProcessor.process(..., wait=False)` returns `BUSY` immediately if another
  worker holds the session;
- a session that fails (`ERROR`, `UNAVAILABLE`, `NO_ADAPTER`) waits 60 s before retry.

On the same machine a single worker with 8 threads went from 16 to 24 documents/min, and
3 processes × 3 threads from 12 to 20. Tesseract measured under one core per run
(`OMP_THREAD_LIMIT=1` made no difference), so the remaining ceiling came from the busy
host. Phase 19 must measure documents/min per vCPU on the target machines.

## 3. Capacity guidance for Phase 19

- **API.** Run `WEB_CONCURRENCY` ≈ vCPUs per instance. Database connections =
  instances × workers × (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW`), which must stay below Cloud
  SQL `max_connections` minus the document workers, the webhook worker and the
  administrative roles. Leave `MAX_CONCURRENT_REQUESTS=0` (automatic) unless a measurement
  shows otherwise. Cloud Run's per-instance `concurrency` should be at least workers × the
  admission limit, so requests queue in the cheap admission queue, not in front of the
  instance.
- **Documents.** Use `DOCUMENT_PROCESSING_MODE=deferred` in production
  (`config/production.env.example`). Run the OCR tier separately and scale it on
  **queue depth**: the count of sessions in `DOCUMENT_PROCESSING`, plus the age of the
  oldest. Alert before the oldest approaches `SESSION_TTL_SECONDS`. As a starting point,
  required worker slots ≈ documents per minute ÷ 9.
- **Rate limits are per process.** With W workers × I instances, a key's effective limit
  is W × I × its configured limit until the counter moves to Memorystore (Phase 19).

## 4. Files

```
scripts/load_test.py               + closed-loop load generator (stdlib only)
scripts/benchmark_stages.py        + per-stage CPU and live end-to-end pipeline benchmarks
src/kyc/core/admission.py          + per-process admission control (503 + Retry-After when saturated)
src/kyc/core/config.py             ~ MAX_CONCURRENT_REQUESTS, REQUEST_QUEUE_TIMEOUT_SECONDS
src/kyc/main.py                    ~ admission middleware; health reports phase 18
src/kyc/services/sessions.py       ~ get_session(lock=False) for status reads
src/kyc/api/routes.py              ~ GET /v1/kyc/{id} reads without the row lock
src/kyc/services/documents.py      ~ process(wait=False) → BUSY; pending_sessions(unclaimed=True) with SKIP LOCKED
scripts/process_documents.py       ~ --parallel N, --loop S, continuous claiming, retry back-off
Dockerfile, compose.yaml           ~ WEB_CONCURRENCY; long-running OCR worker service
.env.example, config/production.env.example  ~ capacity settings; production uses deferred processing
tests/test_performance.py          + admission control, queued reads, load-tool statistics
artifacts/phase18-load-baseline.json, phase18-load-tuned.json, phase18-stages.json, phase18-tests.txt
```

No database migration: the schema stays at `0012_phase17_finalize`.

### Running the tests

```sh
export KYC_LOAD_API_KEY=...   # provisioned key; never on the command line
python scripts/load_test.py --organization ORG --scenario mixed --concurrency 1,8,32,64 --duration 20 \
    --output artifacts/load.json
python scripts/benchmark_stages.py cpu
python scripts/benchmark_stages.py pipeline --organization ORG --sessions 8 --parallel 1,4
```

Load tests create real sessions in the target organization. Use a dedicated test
organization, and suspend it or purge its data afterwards.

## 5. Limits and open items

- **Not production numbers.** They come from a shared laptop with the client and the
  database on the same host. Phase 19 must re-run every scenario on the target sizes,
  including network latency to Cloud SQL, and record SLOs from those runs.
- **Duration.** No soak test longer than a few minutes, so memory growth, connection
  churn and audit-table growth over hours are unmeasured. Every status poll still writes a
  `SESSION_ACCESSED` audit row; at 1 poll/s per device that is up to 900 rows per session.
  Sampling or coalescing those rows is a privacy-review decision, not a performance tweak.
- **OCR transaction length.** `DocumentProcessor` still holds one transaction and the
  session row lock for the whole OCR run. Splitting it into read → OCR without a
  transaction → write (with a version check) would free a connection for each document
  in progress. That is the next structural optimization if the OCR tier needs more
  database connections.
- **Two transactions per request.** Authentication runs its own short transaction before
  the request's. Merging them would save one round trip and one commit per request.
- **Untested.** Face, liveness and NFC endpoints under load; webhook delivery
  throughput; the review dashboard. Their per-stage CPU costs are measured, but they were
  not part of the HTTP scenarios.
- Admission control and rate limits are per process; there is no global queue or quota.
