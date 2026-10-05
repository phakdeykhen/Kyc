# Phase 2 — Document capture and quality pipeline

Status: implemented on 5 October 2026. Phase 3 (Cambodia National ID adapter) has not started.

## 1. Design

Phase 2 is the gate between the camera and every later engine. Its only job is
to decide **ACCEPTED** or **RECAPTURE** for each document side, so OCR, MRZ and
model calls are never spent on unusable images (spec §4). It never claims a
document is genuine.

```
client camera overlay ──► POST /v1/kyc/{session}/documents[/front|/back]
                               │
             body-size gate (middleware, before multipart parsing)
                               │
                tenant auth ─► session row locked FOR UPDATE
                               │  expired? → EXPIRED (committed), 409
                               │  CREATED  → START → DOCUMENT_REQUIRED
                               │  attempt limit → 429 (audited)
                               ▼
                safe decode (format allowlist, pixel cap, EXIF orientation,
                metadata dropped, animated/truncated/bomb files refused)
                               │
                HeuristicDocumentQualityEngine (deterministic, versioned policy)
                               │
             ┌─────────────────┴──────────────────┐
         RECAPTURE                             ACCEPTED
   check row + audit only;              AES-256-GCM sealed original bytes
   no image is stored                   + document_images row + check + audit
                                               │
                         all required sides accepted?
                                               │ yes
                         DOCUMENT_SUBMITTED → DOCUMENT_PROCESSING
                         (waits for the Phase 3+ document engine)
```

### Quality engine

`src/kyc/engines/capture_quality.py`, numpy + Pillow only.

| Check | Method | Reason code → instruction |
| --- | --- | --- |
| Boundaries | Colour distance from the frame-border median, Otsu-derived threshold, morphological closing, hole fill, run-length connected components | `DOCUMENT_NOT_DETECTED` → `USE_CONTRASTING_BACKGROUND` |
| Multiple documents | Second component ≥ 30 % of the largest | `MULTIPLE_DOCUMENTS` → `ONE_DOCUMENT_ONLY` |
| Cropping | Document region touches the frame edge | `DOCUMENT_CROPPED` → `MOVE_BACK` / `CENTER_DOCUMENT` |
| Too far / too close | Coverage of the frame | `DOCUMENT_TOO_FAR` → `MOVE_CLOSER`, `DOCUMENT_TOO_CLOSE` → `MOVE_BACK` |
| Perspective | Opposite-side length ratios × corner squareness of the fitted quadrilateral | `PERSPECTIVE_DISTORTED` → `ALIGN_DOCUMENT` |
| Shape | Aspect vs ID-1 (1.586) or TD3 data page (1.42), ±25 % | `DOCUMENT_SHAPE_UNEXPECTED` → `ALIGN_DOCUMENT` |
| Resolution | Document long side in source pixels | `RESOLUTION_TOO_LOW` → `USE_HIGHER_RESOLUTION` / `MOVE_CLOSER` |
| Blur | Variance of the Laplacian on the document region at a fixed scale; only judged under usable exposure | `IMAGE_BLURRY` → `HOLD_STILL` |
| Glare | Share of saturated, colourless pixels in the region | `GLARE_DETECTED` → `REDUCE_GLARE` |
| Brightness | Mean luminance of the region | `TOO_DARK` → `MORE_LIGHT`, `TOO_BRIGHT` → `LESS_LIGHT` |
| Shadow | Ratio of block-wise bright envelopes across a 4×6 grid | `SHADOW_DETECTED` → `AVOID_SHADOW` |
| Overall | Geometric mean of all scores | `OVERALL_QUALITY_LOW` → `RETAKE_PHOTO` |

Orientation is reported (`LANDSCAPE`/`PORTRAIT`, rotation hint, skew, normalized
corners) but does not cause a recapture. Correction belongs to the Phase 3 pipeline.

The service adds one cross-capture rule: the same image bytes cannot satisfy two
sides (`SAME_IMAGE_FOR_MULTIPLE_SIDES` → `CAPTURE_OTHER_SIDE`).

**Policy honesty.** `DOC-CAPTURE-HEURISTIC-2026.10.1` has `calibrated = False`.
Its thresholds were tuned on synthetic fixtures only. They must be calibrated on
consented field captures before production, and they are never returned to clients.
A trained detector can replace the engine behind the `DocumentQualityEngine`
protocol without touching the service.

### Required sides

`src/kyc/documents/requirements.py` holds defaults: cards need `FRONT` and
`BACK`, passports need `DATA_PAGE`. Phase 3+ adapters own `required_sides()`
and will replace these defaults; the core holds no country rules.

### Storage and retention

* `LocalEncryptedCaptureStore` seals each object with AES-256-GCM. The associated
  data is `capture/{org}/{session}/{object}`, so a ciphertext moved to another tenant,
  session or object fails authentication. Files are written atomically with 0600
  permissions inside 0700 directories. References must be canonical UUID paths, which
  rules out traversal.
* The keyring is `CAPTURE_ENCRYPTION_KEYS=version:key[,older:key]`. The first key
  encrypts; older keys only decrypt, which allows rotation.
* The original upload bytes are kept (encrypted) as evidence for later
  metadata/manipulation analysis (Phase 12). Decoded pixels are never written.
* Rejected and recaptured images are **not stored**. Only scores, reason codes and the
  SHA-256 are kept in `document_checks`.
* `document_images.delete_after` = now + `organizations.capture_retention_hours`;
  `identity_documents.delete_after` uses `pii_retention_days`.
  `scripts/purge_captures.py` deletes expired rows inside each tenant's RLS context.
  It then removes their ciphertext and sweeps orphaned objects older than 15 minutes:
  objects whose transaction rolled back, or images replaced by a recapture.

### State machine

No new states or events. Phase 2 drives the existing ones:
`CREATED --START--> DOCUMENT_REQUIRED --DOCUMENT_SUBMITTED--> DOCUMENT_PROCESSING`.
Nothing emits `DOCUMENT_ACCEPTED` yet, so sessions wait in `DOCUMENT_PROCESSING`
until the document engine exists. That is deliberate: no fake progress.

## 2. Directory tree (Phase 2 additions marked +, changes ~)

```text
src/kyc/
├── main.py                       ~ body-size gate, capture page + CSP, store/engine wiring
├── core/config.py                ~ capture keyring, storage dir, size/pixel/attempt limits
├── api/routes.py                 ~ 3 upload endpoints, result check, required sides
├── api/schemas.py                ~ CaptureResponse, CaptureQuality, CaptureGeometry, CaptureError
├── db/models.py                  ~ document_images key/media/policy columns, side uniqueness
├── documents/requirements.py     + required sides and aspect ratios per document type
├── engines/capture_quality.py    + decoder and heuristic quality engine
├── engines/contracts.py          ~ DocumentQualityEngine protocol
├── services/captures.py          + capture orchestration
├── services/retention.py         + expiry purge and orphan sweep
├── storage/captures.py           + AES-GCM capture store and keyring
└── web/capture/                  + development camera client (index.html, capture.css, capture.js)
migrations/versions/0002_phase2.py  +
scripts/purge_captures.py           +
scripts/bootstrap_local.py          ~ grants for capture tables
scripts/configure_local.py          ~ generates/appends the capture key
tests/images.py                     + synthetic, non-personal document fixtures
tests/test_capture_quality.py       +
tests/test_capture_store.py         +
tests/test_captures_api.py          +
requests/phase2.postman.json        +
artifacts/openapi-phase2.json, phase2-postgresql.sql, phase2-tests.txt  +
```

## 3. Migration `0002_phase2`

* `document_images`: add `key_version`, `media_type`, `quality_policy_version` (NOT NULL;
  temporary server defaults are dropped in the same migration because Phase 1 had no
  writer), add unique `(organization_id, session_id, document_id, side)`, and add index
  `(organization_id, delete_after)` for retention.
* `document_checks`: add index `(organization_id, session_id, check_type)` for attempt counting.
* RLS policies from 0001 already cover both tables. Downgrade reverses everything.

## 4. Security concerns

* **Upload abuse.** `Content-Length` is required and capped before multipart parsing
  (411/413). Files are capped by bytes and decoded pixels. Decompression bombs,
  animated images and truncated files are refused. Each session has an attempt limit,
  so it cannot be used as a free image-scoring oracle.
* **Metadata privacy.** EXIF orientation is applied, then GPS and device metadata are
  discarded from everything analysed. The stored evidence copy keeps the original bytes,
  encrypted, and is retention-limited.
* **Tenant isolation.** The session is loaded under the credential's organization with
  `FOR UPDATE`. Live PostgreSQL tests confirm RLS blocks cross-tenant reads and writes on
  `identity_documents` and `document_images`. AES-GCM associated data adds a second,
  cryptographic boundary.
* **Key management.** Local keys live in `.env` (0600). Production must use Secret Manager
  or KMS envelope encryption with CMEK on GCS (Phase 17/19). Never reuse the dev keyring.
* **Client trust.** The web client's live hints check exposure and focus only. The server
  repeats every check, and the client cannot assert quality.
* **Capture page.** It is served with a strict CSP (`script-src 'self'`, no inline code,
  `frame-ancestors 'none'`) and `Permissions-Policy: camera=(self)`. The API key is held
  in tab memory only. This page is a development client, not the production SDK
  (Phase 16), and it starts the camera as soon as a session is created.
* **Open gaps, owned by later phases.** No consent record is required before capture yet
  (Phase 15/17). Purge must be scheduled externally. GCS storage is not implemented
  (Phase 19). A future reviewer `REQUEST_RECAPTURE` (Phase 14) or classifier recapture
  (Phase 3) must clear accepted captures, or old sides would be reused immediately.
  Thresholds are uncalibrated.
