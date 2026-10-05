# Phases 8–9 — Face detection, quality and 1:1 comparison

The user authorized both stages on 5 October 2026 after the passport/MRZ work.
The shared workspace also contains international document and barcode adapters.
Validation evidence and final test counts belong to
[BUILD_PROGRESS.md](../BUILD_PROGRESS.md).

## Design and scope

The selfie stage uses local CPU inference: OpenCV YuNet `2023mar` finds faces and
five landmarks, and OpenCV SFace `2021dec` aligns crops and produces 128-dimensional,
L2-normalized embeddings. Both the document reference and the selfie use the same
model name, version and SHA-256 digest. Comparison is restricted to these two
templates for one claimed identity. There is no face index or 1:N search. The API
returns the cosine similarity metric, never a same-person percentage.

```text
accepted document + SELFIE_REQUIRED
  → explicit biometric consent
  → bounded image decoding
  → exactly one selfie face + capture quality
  → decrypt accepted FRONT/DATA_PAGE and locate one usable reference face
  → align both crops with the same SFace model
  → encrypt separate document/selfie templates
  → versioned 1:1 cosine comparison
  → LIVENESS_REQUIRED or PROCESSING; decision remains null
```

OpenCV documents the detector, aligned crop and recognizer APIs in its
[face detection/recognition tutorial](https://docs.opencv.org/4.13.0/d0/dd4/tutorial_dnn_face.html).
The pinned source bundles are [YuNet](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet)
and [SFace](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_recognition_sface).
The provisioner fixes an OpenCV Zoo commit, sizes and SHA-256 digests instead of
following the latest upstream weights. It keeps the YuNet MIT and SFace Apache-2.0
license notices with the installed bundle.

### Capture quality

The gate checks exactly one detected face, usable face dimensions, selfie position
and coverage, whether the face is cropped, exposure/clipping, Laplacian sharpness
and landmark geometry as a pose proxy. Reference portraits use document-specific
size rules and do not need to be centered in the document. Neither path modifies
identity features or creates a corrected face.

Usable capture returns `ACCEPTED`; poor quality returns `RECAPTURE` with instructions
such as `CENTER_FACE`, `MOVE_CLOSER`, `MOVE_BACK`, `HOLD_STILL`, `MORE_LIGHT`,
`REDUCE_LIGHT`, `EVEN_LIGHTING`, `FACE_CAMERA` and `ONE_FACE_ONLY`. No template is
created for a rejected capture. An unusable document reference triggers document
recapture, clears stale document evidence and returns `DOCUMENT_REQUIRED` with
`RECAPTURE_DOCUMENT_WITH_CLEAR_PORTRAIT`. Selfie attempt history remains available.

The quality policy is a development heuristic. Five predicted landmarks do not
establish that eyes are visible or that severe occlusion is absent. The engine
therefore reports `EYES_VISIBLE` and `SEVERE_OCCLUSION` as unverified checks and
preserves `FACE_EYE_VISIBILITY_UNVERIFIED` and `FACE_OCCLUSION_UNVERIFIED` review
reasons. `ACCEPTED` means usable for this embedding path, not a full biometric
quality PASS or proof of identity.

### Comparison policy

`FACE_MATCH_CALIBRATED=false` is the default and forces every comparison to `REVIEW`
with `UNCALIBRATED_FACE_POLICY`, regardless of similarity. The default pass boundary
0.363 comes from OpenCV's LFW benchmark example; the default fail boundary 0.20 is a
development setting. Neither is deployment calibration. OpenCV's tutorial lists
different benchmark thresholds across datasets, which supports keeping these
values out of identity-confidence claims.

Calibrated mode requires a distinct policy version and a nonempty calibration
reference. The operator must first validate representative, consented document and
selfie pairs, record false-match/false-nonmatch performance and approve appropriate
thresholds. A configuration flag or evidence-reference string does not perform that
validation. When enabled, the policy applies the pass boundary, fail boundary and
intermediate review band; the comparison result still does not set a risk decision.
Templates from different models or model digests are rejected instead of compared.

## Files

```text
src/kyc/biometrics/
├── types.py                     model, quality, embedding and policy contracts
├── opencv.py                    local pinned YuNet/SFace CPU inference
├── quality.py                   capture gate and unsupported-check metadata
└── embeddings.py                bounded payload format and same-model cosine comparison
src/kyc/storage/biometrics.py    independently keyed AES-256-GCM template encryption
src/kyc/services/biometrics.py   consent, attempts, reference and persistence lifecycle
src/kyc/services/retention.py    face image/template retention integration
src/kyc/api/{routes,schemas}.py  multipart selfie endpoint and safe result metadata
src/kyc/web/capture/             document processing handoff and consented selfie UI
migrations/versions/0004_phase8_9.py
scripts/download_face_models.py pinned weights, licenses and manifest
requests/phase8-9.postman.json   consent and face-stage API workflow
docs/architecture-phase8-9.md   this design
```

The migration extends existing tenant-scoped biometric and comparison tables and
records selfie capture/quality and explicit consent metadata. It does not replace
earlier document/MRZ migrations. Bootstrap refreshes the application's restricted
database-role grants. Templates are not accessible through an image-download or
public template API.

Selfie images use `Organization.capture_retention_hours`; templates and face-quality
evidence use `Organization.template_retention_hours` (both default to 24 hours).
Deleting an expired template also removes its dependent comparison. The existing
`scripts/purge_captures.py` job purges these rows and sweeps expired/orphaned image
ciphertext after the database commit. Schedule it regularly; retained database
backups need a separate production lifecycle.

## Provisioning and environment

Update dependencies, generate missing independent local keys, provision the pinned
models and run migration/bootstrap before starting the API:

```sh
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/configure_local.py
.venv/bin/python scripts/download_face_models.py --destination var/models
PYTHONPATH=src .venv/bin/python scripts/bootstrap_local.py
.venv/bin/python scripts/download_face_models.py --destination var/models --verify-only
PYTHONPATH=src .venv/bin/python scripts/purge_captures.py
```

Alembic revision is `0004_phase8_9`. Running Alembic alone does not refresh restricted
role grants. Model download is an explicit provisioning step; no request downloads
weights. Missing or corrupt models return HTTP 503 instead of a successful fallback.

| Variable | Default / purpose |
| --- | --- |
| `BIOMETRIC_ENCRYPTION_KEYS` | Third independent `version:base64(32 bytes)` keyring; never reuse capture or PII keys |
| `FACE_MODELS_DIR` | `var/models`, pinned local ONNX files and license notices |
| `MAX_SELFIE_BYTES`, `MAX_SELFIE_PIXELS` | 5 MiB / 12 million decoded pixels |
| `MAX_SELFIE_ATTEMPTS` | 10 attempts per session |
| `BIOMETRIC_CONSENT_POLICY_VERSION` | `BIOMETRIC-CONSENT-2026.10.1` |
| `FACE_MATCH_POLICY_VERSION` | `SFACE-COSINE-UNCALIBRATED-2026.10.1` |
| `FACE_MATCH_CALIBRATED` | `false`; forces REVIEW |
| `FACE_MATCH_PASS_THRESHOLD`, `FACE_MATCH_FAIL_THRESHOLD` | 0.363 / 0.20, uncalibrated development settings |
| `FACE_MATCH_CALIBRATION_REFERENCE` | Empty; calibrated mode requires evidence and a distinct version |

## API and capture client

Use the existing API-key and organization headers. Create a `DOCUMENT_FACE` or
`DOCUMENT_FACE_LIVENESS` session, capture its required document sides and poll until
`SELFIE_REQUIRED`. Submit a JPEG, PNG or WebP image with explicit consent:

```sh
curl --fail --silent --show-error "http://127.0.0.1:8000/v1/kyc/$KYC_SESSION_ID/selfie" \
  -H "X-API-Key: $DEVELOPMENT_API_KEY" \
  -H "X-Organization-ID: $DEVELOPMENT_ORGANIZATION_ID" \
  -F biometric_consent=true -F file=@selfie.jpg
```

The response contains `capture_status`, flat quality metadata including face count
and unverified checks, `reason_codes`, actionable `instructions`, attempts remaining
and `next_step`. `comparison` is null for recapture and otherwise supplies cosine
score, result and policy/model provenance. The result endpoint's nullable
`face_comparison` object exposes the same safe summary, alongside `checks.face_match`,
`checks.face_quality`, `checks.portrait_quality` and `checks.document_portrait`. It exposes safe
comparison metadata and review reasons; it never exposes vectors, encrypted
template bytes, raw landmarks or internal threshold values. `decision` remains
null, and no liveness check is asserted.

A `DOCUMENT_FACE` accepted selfie moves to `PROCESSING`; a
`DOCUMENT_FACE_LIVENESS` accepted selfie moves to `LIVENESS_REQUIRED`. Phase 10 has
not yet implemented the liveness endpoint. Rejected selfie quality keeps the
session at `SELFIE_REQUIRED`. An unusable reference portrait instead requires a
new document capture. Expired sessions, foreign-tenant sessions, exhausted attempts,
unsupported images and unavailable prerequisites fail without successful evidence.

The [Postman collection](../requests/phase8-9.postman.json) provides document setup,
consent-negative requests, selfie capture and safe-result assertions. Keep credentials
and selected personal-file paths local. A document request may return `RECAPTURE`;
select a new image before proceeding. Do not send a selfie until `SELFIE_REQUIRED`.

At `/capture/`, clients can create a session or continue an existing one with its
UUID. The document flow polls processing and then opens the front camera with an
oval guide. Consent is unchecked for each new/resumed selfie session and gates both
camera submission and file upload. Recapture instructions keep the capture controls
available; accepted submission stops the camera. The preview is mirrored while
the uploaded camera frame retains its original orientation. Local light/focus hints
are advisory and do not claim browser face detection or liveness.

## Validation

Run the full suite after provisioning models and applying migrations:

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
node --check src/kyc/web/capture/capture.js
node scripts/test_capture_client.cjs
```

Validation should cover the real YuNet/SFace path, quality recapture, finite normalized
embeddings, model/version binding, encrypted-template context isolation, consent,
state/attempt handling, tenant isolation, retention and public-data minimization.
Synthetic and upstream sample-image success establishes integration behavior; it
does not establish accuracy on the production population or calibrate thresholds.
See [build progress](../BUILD_PROGRESS.md) for the actual completed runs and limits.
The dependency-free client script simulates the DOM, API and camera lifecycle to
check consent, existing-session continuation, selfie/document recapture, processing
polls, safe comparison messaging and failure recovery. It does not validate a physical
camera, rendered CSS or browser permission behavior.

## Security and limits

- Face similarity does not prove liveness. A printed image, replay, screen photo or
  virtual-camera injection may match. Phase 10 implements liveness separately.
- Known Cambodia adapters crop their versioned portrait region from the geometry
  corrected accepted `FRONT` or `DATA_PAGE`; the generic MRZ passport fallback
  searches the whole corrected data page. These are printed-document references,
  not verified chip portraits. Multiple portraits, security ghost images or a poor
  print can force recapture. Portrait extraction has no authenticity verdict.
- Templates use AES-256-GCM with an independent versioned keyring and associated
  data bound to tenant, session, template/source and exact model provenance. Normal
  responses and audit metadata exclude vectors and decrypted face crops.
- Retention removes expired selfie artifacts and templates; backups and production
  key custody still require the later retention and KMS deployment controls.
- Explicit biometric consent is recorded at submission with a policy version. The
  development checkbox/API boolean is not a complete production consent notice,
  consent-revocation service or independently verified record of the person giving
  consent. Earlier document capture still has no consent gate.
- Eye visibility, severe occlusion, production-population calibration, fairness,
  load performance and deployment-level camera-injection protection remain
  unvalidated. Default comparison requires review and the final decision stays null.

Phases 8–9 satisfy face-stage integration within those limits. NFC, liveness and
the risk engine remain separate stages.
