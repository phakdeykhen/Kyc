# Phase 14 — Authorized manual review dashboard

Status: implemented on 6 October 2026.

## 1. Design (spec §20, §24)

Cases the risk engine sends to `MANUAL_REVIEW` are worked by people in a dashboard at
`/review/`, backed by the reviewer API. A reviewer sees only what their role allows,
every view and decision is audited, and a human can resolve uncertainty but cannot
approve away missing evidence or tamper proof.

```
risk engine REVIEW → MANUAL_REVIEW → queue (longest waiting first)
  reviewer opens case (audited) → photos (audited, retention-bound) → decision
  APPROVE            → REVIEW_APPROVED  → VERIFIED   (approval guard + state-machine evidence guard)
  REJECT             → REVIEW_REJECTED  → REJECTED
  REQUEST_RECAPTURE  → RECAPTURE_REQUIRED → DOCUMENT_REQUIRED (stale photos/fields/templates cleared)
```

### Separate reviewer identity

- Reviewers are rows in `reviewers` (tenant-scoped, forced RLS). Each has a name, a role
  and a bearer token `rvw_…` with 256 random bits; only its SHA-256 is stored.
- Accounts are created and deactivated with `scripts/create_reviewer.py`, using the
  migrator connection. The API role can only read reviewers. Tokens are shown once.
- Review endpoints accept only `Authorization: Bearer rvw_…` plus `X-Organization-ID`.
  The client application's API key is refused there, and reviewer tokens are refused by
  the client API, so **an integrating app can never approve its own sessions**.
- A token is looked up under the organization's RLS context, so it works only in its own
  organization. Another tenant's case returns 404.

### Roles and what each sees

| Permission | REVIEWER | AUDITOR |
| --- | --- | --- |
| Queue, checks, fraud signals (with details), risk trace, MRZ/barcode/chip/face/liveness evidence, history (reason codes) | ✓ | ✓ |
| Unmasked identity fields and document number; reviewers' notes | ✓ | — (masked / hidden) |
| Document photos and selfie, while retention allows | ✓ | — (403) |
| Approve, reject, request recapture | ✓ | — (403) |

Liveness frames and raw chip data were never stored, so there is nothing to show for
them. Photos past `delete_after` are neither listed nor served.

### Decisions

`POST /v1/review/{session}/decision` takes `action`, `reason_code`, `note` and
`expected_version`:

- **Reason codes** are a fixed list per action (for example `DOCUMENT_CONFIRMED_GENUINE`,
  `IDENTITY_MISUSE_SUSPECTED`, `GLARE_OR_BLUR`). The required **note** (5–1000 characters)
  is encrypted with the PII keyring, bound to tenant, session and review.
- **Optimistic version:** the case version the reviewer opened must still be current,
  otherwise 409 `CASE_CHANGED`. Two reviewers can never decide on different evidence.
- **Approval guard:** APPROVE is refused with 409 `APPROVAL_BLOCKED` (and the list of
  blockers) if any check the level requires is missing, unavailable or FAIL, or if there
  is a HIGH tamper signal. Those cases need recapture or rejection. The state machine's
  evidence guard then applies as for an automatic PASS.
- **Recapture** clears photos, extracted fields, MRZ results, selfie and face templates
  (the same routine as a reference recapture) and keeps checks, signals, assessments,
  reviews and audit history. The person redoes the flow from the document step.
- The client result gains `review: {action, reason_code, decided_at}`. Reviewer identity
  and notes stay inside the review system. The engine's `decision` is kept unchanged as history.

### Audit

`REVIEW_CASE_VIEWED` (records whether identity was shown), `REVIEW_IMAGE_VIEWED`,
`REVIEW_APPROVAL_BLOCKED`, `REVIEW_DECISION` (action, reason, case version seen) and the
state transition. Each entry has actor `reviewer:<id>` and the reviewer's role.
`manual_reviews` is append-only for the API role (SELECT, INSERT).

### Dashboard (`/review/`)

A static page served under a strict CSP: scripts and styles only from `'self'`, images
from `'self'` and `blob:`, no framing, no inline code. The token is held in memory only
and never stored, so reloading signs the reviewer out. All API values are rendered with
`textContent`, never as HTML. Photos are fetched with the token and shown through
revocable blob URLs. The layout is a queue beside the case, collapsing to one column on
narrow screens, with light and dark themes.

## 2. Files

```
src/kyc/review/access.py          roles, permissions, reason codes, token hashing, reviewer auth
src/kyc/services/review.py        queue, case view, images, decision + approval guard
src/kyc/api/review_routes.py      GET /me, /queue, /{session}, /{session}/images/{id}; POST /{session}/decision
src/kyc/web/review/               index.html, review.js, review.css
src/kyc/services/biometrics.py    ~ clear_identity_evidence() shared with reviewer recapture
src/kyc/services/results.py       ~ review outcome in the client result
src/kyc/db/models.py              + Reviewer; ManualReview.session_version, risk_assessment_id
migrations/versions/0008_phase14.py   + reviewers (forced RLS), manual_reviews columns and index
scripts/create_reviewer.py        + create / deactivate reviewers
tests/test_review.py              + 11 tests
```

## 3. Validation (6 October 2026)

- **344 tests: 344 passed, 0 skipped, 0 failures** with live PostgreSQL 18.6 (RLS on
  `reviewers` and `manual_reviews`; 22 forced-RLS tables) and native face models
  ([artifacts/phase14-tests.txt](../artifacts/phase14-tests.txt)).
- Live run ([artifacts/phase14-live-e2e.json](../artifacts/phase14-live-e2e.json)), with
  reviewers made by the real provisioning script and cases made by the real pipeline:
  - Approval of a case without liveness evidence was blocked (`EVIDENCE_MISSING_LIVENESS`).
    Recapture reopened it, and the redone document went through real OCR back to
    `SELFIE_REQUIRED`.
  - The reviewer saw identity and the photo. The auditor saw masked data and got 403 on the photo.
  - A stale version was refused (409 `CASE_CHANGED`).
  - Approval → VERIFIED. The client result shows the outcome; the note did not leak and
    is stored encrypted.
  - `kyc_app` could not rewrite a review.
- In Chrome, a reviewer signed in, opened a real-selfie case, rejected it with
  `IDENTITY_MISUSE_SUSPECTED`, and saw the history and the emptied queue
  ([screenshot](../artifacts/phase14-dashboard.jpg)). A text-overflow bug found this way
  was fixed.

## 4. Limits

- No four-eyes approval or reviewer assignment/claiming. Optimistic versions prevent
  conflicting decisions, but not duplicated effort.
- Tokens have no expiry or rotation schedule yet, and there is no SSO/MFA. Proper
  credential management is Phase 15/17.
- Access-denied attempts (403) are not audited; successful views and decisions are.
- The dashboard has no search or filters beyond the oldest-first queue.
- Webhooks for review outcomes are Phase 16.
