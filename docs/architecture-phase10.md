# Phase 10 — Liveness / anti-spoof

Status: implemented on 5 October 2026; uncalibrated flat-geometry rejection corrected
after a phone-test report on 6 October 2026.

## 1. Design (spec §14)

Liveness is a separate check from face similarity. It asks whether a real, present
person is in front of the camera, not whether two faces match. Phase 10 implements
**active liveness** on the face models that are already installed (YuNet landmarks,
SFace templates). No trained presentation-attack model is assumed.

```
LIVENESS_REQUIRED
  → POST /v1/kyc/{id}/liveness/challenge   random sequence + 256-bit nonce, 120 s, single use
  → client shows each instruction, captures 2–3 raw frames per step
  → POST /v1/kyc/{id}/liveness             challenge_id, nonce, frame_steps, 8–12 frames
  → geometry · replay · identity continuity (frames assessed in memory, never stored)
  → retry (same state, new challenge)  or  final PASS/REVIEW/FAIL → LIVENESS_ACCEPTED → PROCESSING/NFC_REQUIRED
```

### Unpredictable challenges

`LOOK_STRAIGHT` followed by three distinct moves from TURN_LEFT/TURN_RIGHT/LOOK_UP/LOOK_DOWN,
in random order (24 sequences), drawn with `secrets.SystemRandom`. Only the nonce's
SHA-256 is stored. A challenge is refused if its nonce is wrong, if it has been used
for an assessment, or if it has expired. Model/reference/storage unavailability
leaves the challenge open for retry within its original TTL; it does not extend that TTL.
Issuing a new challenge voids older unused ones. Blinking is not used.

### The 3D geometry test

The nose is expressed in the affine frame of the eyes and mouth:
`nose = m + a·(le − re) + b·(mouth − m)`. Affine maps preserve (a, b) exactly, so a
**flat** face (printed photo, phone or monitor) keeps them constant however it is moved,
rotated, tilted or zoomed. A real head is not flat. Turning left/right changes `a`, and
looking up/down changes `b`; the centred frame keeps the two nearly independent.

| Measured with a 3D head model | a | b | face shape (aspect) |
| --- | --- | --- | --- |
| real turn ±20° | ±0.164 | 0.00 | −6 % |
| real tilt ±15° | 0.00 | ±0.11 | +6 % / +1 % |
| flat photo tilted 35° (close, perspective) | +0.032 | 0.00 | −18 % |
| flat photo tilted 45° at 30 cm | +0.053 | −0.006 | −29 % |

* Frames must remain in step order, and a movement needs at least two consecutive
  qualifying frames. A step is completed when its frames move the right coordinate by at least the policy
  movement (a ~10° turn) in the right direction. Frames must be raw, unmirrored camera frames.
* A **flat-geometry finding** is a frame whose eye/mouth triangle deformed strongly
  while the nose's relative coordinates barely changed. Expression, perspective and
  landmark estimation error can produce the same measurements on a live head. Under
  the uncalibrated policy, an incomplete challenge with this finding returns REVIEW,
  `attack_type: POSSIBLE_PRINTED_OR_SCREEN_PHOTO`, and requires manual review.
  It cannot automatically verify. A calibrated policy retains the configured FAIL
  behavior, which requires validation on genuine users and attack media.
* The same image submitted for every step is FAIL, `STATIC_REPLAY`.
* **Identity continuity.** Every frame is embedded and compared with the session's
  unexpired encrypted selfie template. A frame below the face-match pass boundary gives REVIEW,
  `POSSIBLE_FACE_SWAP`.
* **Final face comparison.** For a completed challenge with sufficient continuity,
  select the best accepted frontal baseline frame by its weakest quality measurement,
  then the sum of its measurements. The React client captures these frames after
  issuance. Store only its encrypted embedding and quality/provenance, and compare
  it with the current unexpired document portrait. The original consented selfie
  remains the continuity reference. Raw liveness frames are discarded.
* A missing baseline, more than one face, or uncompleted moves are **retryable**: the
  session stays in `LIVENESS_REQUIRED`, and attempts are limited (`MAX_LIVENESS_ATTEMPTS`).

### Results and honesty

* `ACTIVE-GEOMETRY-2026.10.5` is **uncalibrated**. A completed challenge returns REVIEW
  (`UNCALIBRATED_LIVENESS_POLICY`) until the policy is validated on presentation-attack
  data and `LIVENESS_CALIBRATED=true`. Uncertain flat geometry also requires REVIEW;
  byte-identical replay still returns FAIL.
* Thresholds never leave the server. Responses carry the result, a completion score,
  the attack type, per-step completion and an attack **coverage** map:

| Attack | Coverage |
| --- | --- |
| Printed photo, photo on a screen | partial: uncalibrated geometry heuristic |
| Video replay, device replay | partial: random challenge + single-use nonce |
| Virtual camera / stream injection | partial: nonce + 120 s TTL; no device attestation |
| 3D mask | not supported |
| AI-generated / deep-fake media | not supported |

* `PASSIVE_LIVENESS` remains a contract: no passive presentation-attack model is installed.
* The `decision` stays null. A FAIL is evidence for the risk engine (Phase 13).

## 2. Files

```text
src/kyc/liveness/geometry.py      + affine nose coordinates and face-shape measures
src/kyc/liveness/challenge.py     + random single-use challenges, nonce hashing
src/kyc/liveness/active.py        + assessment policy, outcomes, coverage map
src/kyc/services/liveness.py      + challenge issuance, frame submission, evidence, state change
src/kyc/api/routes.py, main.py, core/config.py  ~ endpoints, body limit, settings, policy wiring
src/kyc/services/results.py       ~ `liveness` check and LIVENESS_* review flags
src/kyc/db/models.py              ~ LivenessChallenge; liveness_checks.evidence_metadata
src/kyc/web/capture/*             ~ liveness step (front camera, instructions, 2 frames per step)
migrations/versions/0005_phase10.py   + liveness_challenges (RLS) and evidence column
tests/test_liveness.py            + geometry, challenges, assessor, API, real-YuNet photo attack
scripts/test_capture_client.cjs   ~ simulated liveness challenge, retry and finish
requests/phase10.postman.json     + challenge and frame requests
```

## 3. Migration and environment

* `0005_phase10` creates `liveness_challenges` (forced tenant RLS) and adds
  `liveness_checks.evidence_metadata`. Rerun bootstrap for the new grants.
* New settings: `LIVENESS_CHALLENGE_TTL_SECONDS` (120), `MAX_LIVENESS_ATTEMPTS` (5),
  `MAX_LIVENESS_BYTES` (16 MiB), `LIVENESS_POLICY_VERSION`, `LIVENESS_CALIBRATED` (false).

## 4. Security concerns and limits

* **Frames are biometric data.** They are held only in request memory and never written
  to storage. Evidence keeps outcomes, measurements and the nonce hash; the best
  accepted frontal frame can supply an encrypted template with the existing retention deadline.
* **Injection.** The server cannot attest the camera. A virtual camera driven in real
  time by an attacker who reproduces 3D motion (e.g. a deep-fake puppet) is not
  defeated. Mitigating that needs device attestation or a certified passive model.
* **Calibration.** Movement and flat-face boundaries were derived from a geometric head
  model and synthetic tests only. They must be measured on real users and attack media
  (ISO/IEC 30107-3 style) before any PASS is allowed.
* **Positive-path evidence.** A genuine moving head was not available. Live completion
  is verified with projected 3D landmarks. With real YuNet, a real photo moved and
  tilted in front of the camera completed 0 of 3 moves and was never accepted.

### Phone-test correction (6 October 2026)

The reported Khmer ID session passed current document photo quality and expiry checks.
Its only hard rejection reason was `LIVENESS_FAILED`, produced by the uncalibrated
`FLAT_FACE_PRESENTATION` heuristic. OCR/MRZ discrepancies and uncalibrated face comparison
were review findings. Since liveness frames are not retained, the original sequence
cannot be replayed or independently classified as a presentation attack.

A regression using a projected live 3D head and modest mouth/nose landmark displacement
reproduces the previous false rejection. Version `.2` preserves the suspicious finding
and incomplete challenge as review evidence. Existing decisions remain unchanged;
new sessions use the corrected policy. The capture page now says "photo accepted"
and explains that document details and identity verification remain pending.

This is a correction to how uncertain evidence is handled, not empirical validation of
liveness accuracy. Measure genuine-user rejection and attack acceptance on real capture
data before enabling calibrated automatic decisions. [NIST's PAD evaluation](https://www.nist.gov/publications/face-analysis-technology-evaluation-fate-part-10-performance-passive-software-based)
provides a reference for evaluating software presentation-attack detectors.
