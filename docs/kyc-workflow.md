# ID KYC workflow

How one identity verification runs from start to finish: who calls what, which
status the session is in, what the server checks, and how a human reviewer
finishes the cases the risk engine cannot settle.

## 1. Actors and credentials

| Actor | Credential | Can do |
|---|---|---|
| Client backend (your app's server) | API key `kyc_…` + `X-Organization-ID` | Create sessions, issue client tokens, read results, call `/verify`, erase data. Scopes: `sessions:write`, `sessions:read`, `results:identity`, `data:erase`, `keys:manage`, `webhooks:manage` |
| User's device (browser / mobile app) | Session client token (scope `session:capture`) | Consent, upload document / selfie / liveness / NFC, and poll status, for **one session only** |
| Reviewer | Reviewer token `rvw_…` + `X-Organization-ID` | Work the manual review queue at `/review/` (roles `REVIEWER`, `AUDITOR`) |
| Risk engine / workers | Internal | Process documents, assess sessions, expire sessions, deliver webhooks |

An API key is never shipped to a device, and it cannot reach `/v1/review/*`. A reviewer token
cannot reach the client API, so an integrating application can never approve its own sessions.

## 2. Verification levels

Chosen when the session is created (`verification_level`, default `DOCUMENT_FACE_LIVENESS`).
Each level adds steps and required checks:

| Level | Steps after the document | Extra required checks |
|---|---|---|
| `DOCUMENT_ONLY` | — | — |
| `DOCUMENT_FACE` | selfie | `portrait_quality`, `face_quality`, `face_match` |
| `DOCUMENT_FACE_LIVENESS` | selfie → liveness | + `liveness` |
| `DOCUMENT_FACE_LIVENESS_NFC` | selfie → liveness → NFC chip | + `nfc` |

Every level requires `document_quality`, `document_classification`, `document_data`,
`expiry`, `cross_check` and `fraud` (`src/kyc/risk/policy.py`).

## 3. Session status flow

```
CREATED
  │ first document upload
  ▼
DOCUMENT_REQUIRED ◄──────────────┬──────────────┬─────────────────────┐
  │ all sides uploaded           │ recapture    │ portrait unusable   │ reviewer
  ▼                              │              │                     │ REQUEST_RECAPTURE
DOCUMENT_PROCESSING ─────────────┘              │                     │
  │ document accepted                           │                     │
  ├─ DOCUMENT_ONLY ───────────────────────┐     │                     │
  ▼                                       │     │                     │
SELFIE_REQUIRED ──────────────────────────┼─────┘                     │
  │ selfie accepted                       │                           │
  ├─ DOCUMENT_FACE ───────────────────────┤                           │
  ▼                                       │                           │
LIVENESS_REQUIRED                         │                           │
  │ liveness accepted                     │                           │
  ├─ DOCUMENT_FACE_LIVENESS ──────────────┤                           │
  ▼                                       │                           │
NFC_REQUIRED                              │                           │
  │ chip accepted                         │                           │
  ▼                                       ▼                           │
PROCESSING ── risk engine ──┬─ PASS ──► VERIFIED                      │
                            ├─ FAIL ──► REJECTED                      │
                            └─ REVIEW ► MANUAL_REVIEW ── APPROVE ──► VERIFIED
                                              ├──────── REJECT ───► REJECTED
                                              └──────── REQUEST_RECAPTURE ─┘

Any non-terminal status except MANUAL_REVIEW ── session TTL passes ──► EXPIRED
```

`VERIFIED`, `REJECTED` and `EXPIRED` are terminal: start a new session instead.
Transitions are driven only by server events (`src/kyc/domain/state_machine.py`);
a client can never set a status. Reaching `VERIFIED` re-checks that every piece of
evidence the level requires is present.

## 4. Step by step

### Step 1: Create the session (client backend)

```http
POST /v1/kyc/sessions
X-API-Key: kyc_…
X-Organization-ID: <org>
Idempotency-Key: <unique per attempt>

{"user_id": "u-123", "country": "KH",
 "expected_document_type": "KH_NATIONAL_ID",
 "verification_level": "DOCUMENT_FACE_LIVENESS"}
```

- Status `CREATED`. The session expires after `SESSION_TTL_SECONDS` (default 900 s).
- `KH_*` document types require `country: "KH"`. `GET /v1/document-types` lists the types and the sides each one needs.
- Retrying with the same `Idempotency-Key` returns the same session (`200`, `Idempotent-Replayed: true`).

### Step 2: Hand the session to the device (client backend)

```http
POST /v1/kyc/{session_id}/client-token
```

Returns a `client_token` scoped to this session. Issuing a new one revokes the old one.
Pass it to the browser or app. The built-in capture page is at `/capture/`.

### Step 3: Consent (device)

```http
POST /v1/kyc/{session_id}/consent
```

Records consent to document processing. It is required before any document upload when
`REQUIRE_DOCUMENT_CONSENT` is on (always in production), otherwise the upload returns `422 DOCUMENT_CONSENT_REQUIRED`.

### Step 4: Upload the document (device)

```http
POST /v1/kyc/{session_id}/documents        (form: side, file)
POST /v1/kyc/{session_id}/documents/front  (file)
POST /v1/kyc/{session_id}/documents/back   (file)
```

| Document | Sides |
|---|---|
| `KH_NATIONAL_ID`, `KH_NSSF`, `NATIONAL_ID`, `RESIDENCE_CARD`, `DRIVING_LICENSE` | `FRONT` + `BACK` |
| `KH_PASSPORT`, `PASSPORT` | `DATA_PAGE` |

1. The first upload moves `CREATED → DOCUMENT_REQUIRED`.
2. Each image passes a capture quality gate (size, pixels, blur, glare, framing). A rejected
   photo returns its reason and `attempts_remaining` (limit `MAX_CAPTURE_ATTEMPTS`, default 20).
3. When every side is in, the session moves to `DOCUMENT_PROCESSING` and the document is read.
   This happens inline after the response, or by a worker when `DOCUMENT_PROCESSING_MODE=deferred`.
4. Document processing: classification, OCR, MRZ (check digits and field consistency),
   barcode, issuing country, field validation and expiry. Fields are stored encrypted.
   See [Document reading (OCR)](#document-reading-ocr) below.
   - **Accepted** → `kyc.document.accepted`, then `SELFIE_REQUIRED` (or `PROCESSING` for `DOCUMENT_ONLY`).
   - **Recapture** (wrong or unrecognized document, unreadable critical fields) → captures are
     cleared and the session goes back to `DOCUMENT_REQUIRED` (`kyc.recapture.required`).
   - **Not processed** (no adapter for the type, OCR engine, capture storage or PII key not
     configured, or an internal error) → the session stays in `DOCUMENT_PROCESSING` and the
     reason is audited; `scripts/process_documents.py` retries it.

The device polls `GET /v1/kyc/{session_id}` to see the current status.

#### Document reading (OCR)

`src/kyc/services/documents.py` runs these steps for each required side, then for the document:

1. **Prepare** (`documents/preprocess.py`): locate the card, warp it to an upright rectangle of the
   expected shape (ID-1 card or TD3 passport page), then grayscale, autocontrast and light sharpening.
   Geometry and contrast only: the document's pixels are never edited.
2. **Read text**: Tesseract 5 (`OCR_LANGUAGES`, default `khm,eng`; images go over stdin, never to disk).
   Cambodian cards then re-read every number and date with only Khmer digits allowed
   (`documents/refine.py`). If the two reads disagree the value keeps `OCR_PASSES_DISAGREED`, which
   lowers its confidence.
3. **Read the MRZ** (`mrz/reader.py`): the MRZ band is read four ways (ICAO alphabet only,
   unconstrained, enlarged, column layout). The parser keeps the combination of lines whose check
   digits validate. Repairs for common misreads (lost or misread `<` filler, `O` read for `0` in a
   TD1 document number, one stray character in the data line) are used only when no reading as printed
   validates, and only if the field check digits then agree (`MRZ_CHAR_CORRECTED`). The name line has
   no check digit, so the reading most passes agree on wins.
4. **Classify** each side against the claimed type (`documents/adapters/`). Sides uploaded the wrong
   way round are swapped (`SIDES_SWAPPED`). A different or unrecognized document → recapture.
5. **Extract** fields from the printed labels. The visual zone is canonical. The MRZ fills only
   fields the visual zone lacks: a number or date needs its own valid check digit, and a name or sex
   needs the MRZ's field check digits to be valid (`FROM_MRZ`).
6. **Validate**: required fields, number format, date consistency, expiry, OCR confidence (below 0.80
   → `REVIEW`), digit-script consistency, MRZ check digits, MRZ against visual fields, barcode, and
   issuing country against the session country.
   - A missing critical field (KH National ID: number, Khmer name, date of birth) → recapture.
     On a KH National ID whose MRZ check digits validate, an unreadable Khmer name gives `REVIEW`
     instead, so a genuine card does not loop through recaptures.
   - Everything uncertain is flagged and carried into the risk engine as `REVIEW`, never guessed.

Raw OCR text and field values are personal data: they are stored encrypted and never written to
checks, logs or audit records.

### Step 5: Selfie (device, all levels except `DOCUMENT_ONLY`)

```http
POST /v1/kyc/{session_id}/selfie   (form: file, biometric_consent=true)
```

- Needs explicit biometric consent in the same request.
- The face is checked for quality and compared **only** with this session's document portrait.
- Accepted → `LIVENESS_REQUIRED` (or `PROCESSING` for `DOCUMENT_FACE`).
- If the document portrait itself is unusable → back to `DOCUMENT_REQUIRED`.
- Limit: `MAX_SELFIE_ATTEMPTS` (default 10).

### Step 6: Liveness (device, `…_LIVENESS` levels)

```http
POST /v1/kyc/{session_id}/liveness/challenge
POST /v1/kyc/{session_id}/liveness   (form: challenge_id, nonce, frame_steps, frames[])
```

1. The server issues a single-use, randomly ordered head-movement challenge
   (expires after `LIVENESS_CHALLENGE_TTL_SECONDS`, default 120 s).
2. The device records 4–12 raw (unmirrored) frames and says which step each frame belongs to.
3. Frames are assessed in memory and never stored. They must match the selfie face.
- Accepted → `NFC_REQUIRED` (NFC level) or `PROCESSING`.
- Limit: `MAX_LIVENESS_ATTEMPTS` (default 5).

### Step 7: NFC chip (device, `DOCUMENT_FACE_LIVENESS_NFC` only)

```http
POST /v1/kyc/{session_id}/nfc/challenge
POST /v1/kyc/{session_id}/nfc   (form: read_status, access_protocol, challenge_id, aa_signature, sod, dg1, dg2, dg15)
```

- The server verifies Passive Authentication (SOD against the CSCA trust list) and Active
  Authentication (the chip signs the 8-byte challenge). Raw chip data is not stored.
- The device can report `NOT_SUPPORTED`, `NOT_AVAILABLE` or `FAILED` instead of `READ`.
  The risk engine then decides what that means for the case.
- Limit: `MAX_NFC_ATTEMPTS` (default 3).

### Step 8: Automatic decision (server)

When the session reaches `PROCESSING`, the assessor runs straight after that request commits.
The client can also trigger it, or read the decision already made, with `POST /v1/kyc/{session_id}/verify`.

1. **Fraud analysis**: duplicate or reused documents and selfies, velocity, cross-checks
   between MRZ, OCR, barcode and chip, metadata and tamper signals (`src/kyc/fraud/`).
2. **Risk engine**: deterministic evidence plus policy gives `PASS`, `REVIEW` or `FAIL`, with reason
   codes and a trace of every rule that fired (`src/kyc/risk/engine.py`).
   - `FAIL` beats `REVIEW` beats `PASS`.
   - `PASS` needs every required check to be `PASS` (or an allowed `NOT_APPLICABLE`) and no rule fired.
   - A missing or unavailable required check gives `REVIEW`, never `PASS`.

| Decision | Status | Webhook |
|---|---|---|
| `PASS` | `VERIFIED` | `kyc.verified` |
| `REVIEW` | `MANUAL_REVIEW` | `kyc.review.required` |
| `FAIL` | `REJECTED` | `kyc.rejected` |

### Step 9: Manual review (reviewer)

Reviewers sign in at `/review/` with the organization ID and their `rvw_…` token
(create one with `scripts/create_reviewer.py create "Name" --role REVIEWER`).

1. **Queue**: `GET /v1/review/queue` lists `MANUAL_REVIEW` cases, oldest first by default. Cases in review
   never expire, so a reviewer can take as long as needed.
   **All sessions**: `GET /v1/review/sessions?status=&user_id=` lists every session of the organization
   (newest change first, with per-status counts). The dashboard shows it in the *All sessions* tab.
2. **Case**: `GET /v1/review/{session_id}` shows checks, fraud signals, the risk trace and history.
   What the reviewer sees depends on their role:
   - `VIEW_CASE` (REVIEWER, AUDITOR): checks, signals, trace, masked document number
   - `VIEW_IDENTITY` (REVIEWER): unmasked identity fields and reviewer notes
   - `VIEW_IMAGES` (REVIEWER): document photos and selfie, while retention allows
   Every case view and image view is audited.
3. **Decision**: `POST /v1/review/{session_id}/decision` (`DECIDE` permission, REVIEWER only):

| Action | Reason codes | Result |
|---|---|---|
| `APPROVE` | `DOCUMENT_CONFIRMED_GENUINE`, `FACE_CONFIRMED_SAME_PERSON`, `OCR_ERROR_CONFIRMED`, `DATA_DIFFERENCE_EXPLAINED`, `DUPLICATE_USE_EXPLAINED` | `VERIFIED` |
| `REJECT` | `DOCUMENT_SUSPECTED_FORGED`, `FACE_DIFFERENT_PERSON`, `PRESENTATION_ATTACK_SUSPECTED`, `DOCUMENT_EXPIRED`, `IDENTITY_MISUSE_SUSPECTED`, `POLICY_NOT_MET` | `REJECTED` |
| `REQUEST_RECAPTURE` | `DOCUMENT_UNREADABLE`, `WRONG_DOCUMENT`, `GLARE_OR_BLUR`, `SELFIE_UNUSABLE`, `EVIDENCE_INCOMPLETE` | identity evidence cleared, back to `DOCUMENT_REQUIRED` with a fresh session lifetime |

Rules:
- Every decision needs a reason code from the fixed list and a note (5–1000 characters, stored encrypted).
- `expected_version` must equal the case version the reviewer opened. If the case changed, the
  server returns `409 CASE_CHANGED`, so two reviewers can never decide on different evidence.
- **Approval is blocked** (`409 APPROVAL_BLOCKED`) when a required check is missing, `UNAVAILABLE`
  or `FAIL`, or when there is a high-severity tamper signal. Those cases must be rejected or recaptured.

### Step 10: Result (client backend)

```http
GET /v1/kyc/{session_id}/result
```

Returns the status, the decision, reason codes and the checks. Identity fields are masked
unless the API key holds `results:identity`. Subscribe to webhooks (`/v1/webhooks`) rather
than polling. Event types:

`kyc.document.accepted`, `kyc.selfie.required`, `kyc.recapture.required`, `kyc.processing`,
`kyc.review.required`, `kyc.verified`, `kyc.rejected`, `kyc.expired`.

## 5. Expiry, erasure and retention

- **Expiry**: any request to a session past its TTL moves it to `EXPIRED` (`kyc.expired`). A session in
  `MANUAL_REVIEW` does not expire; the device's client token keeps working so it can poll for the outcome.
- **Erasure**: `POST /v1/kyc/{session_id}/erase` (`data:erase`) deletes personal and biometric
  data now and closes an unfinished session. The status, reason codes and audit trail are kept.
- **Retention**: `scripts/purge_captures.py` (run hourly) deletes expired captures and orphaned
  ciphertext.

## 6. Default limits

| Setting | Default |
|---|---|
| `SESSION_TTL_SECONDS` | 900 |
| `MAX_CAPTURE_ATTEMPTS` | 20 |
| `MAX_SELFIE_ATTEMPTS` | 10 |
| `MAX_LIVENESS_ATTEMPTS` | 5 |
| `LIVENESS_CHALLENGE_TTL_SECONDS` | 120 |
| `MAX_NFC_ATTEMPTS` | 3 |
| `NFC_CHALLENGE_TTL_SECONDS` | 120 |
| `REVIEWER_TOKEN_MAX_DAYS` | 90 |
