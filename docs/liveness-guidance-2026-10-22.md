# Face scan guidance and recognition review — 2026-10-22

The React applicant scan, shared server guidance/assessment, and safe wording/calibration in the built-in capture client were updated in the existing React/TypeScript, Tailwind, FastAPI and OpenCV stack. No new face model or liveness provider was introduced.

## Causes reproduced

- The old arrow was a bare white chevron positioned **inside** the camera at a 13% inset. Its pulse changed opacity, allowing faces, clothing and backgrounds to obscure it.
- Guidance compared movement to the **last** neutral capture, while final assessment used the **median** neutral pose. With synthetic neutral captures at −2°/+2° pitch, a comfortable +12° downward movement passed against the median and failed against the former last-frame reference. Raw-frame pitch/yaw signs were correct. Lowering the whole face in the image does not change the affine-relative pitch measurement.
- Five-second nudges asked people to look at the floor/ceiling or over a shoulder. These instructions could drive a person beyond usable tracking instead of clarifying a small movement.
- Holds used consecutive responses without checking decoded-frame identity, result age or time between captures. Failed requests reset the hold silently, and detection, hold and confirmation were not distinct UI states.
- Movement guidance accepted predicted landmarks without the existing crop, illumination, distance and usability checks. Final movement assessment now applies the same visibility rules; neutral capture also retains its frontal quality gate.
- The help card appeared as a variable-height list. Body scrolling was locked at every width, but the scan became a relative panel on tablets/desktops, which could make overflowing content unreachable.
- Each JPEG capture allocated another canvas. Native video was already independent of server inference; moving inference into a browser worker would not address this implementation.

## Resulting behavior

- White 32px SVG arrows sit in opaque `#1D4ED8` badges, responsively 48–56px, in a reserved gutter beside the face circle. Only the glyph moves by 3px; the badge never moves or fades. Holds stop arrow motion; reduced motion disables animation and transitions. White/blue contrast is 6.70:1; white/green confirmation is 5.02:1. Active status text, confirmation and warnings use explicit high-contrast colors on the slate scan background.
- One main instruction and one short status describe the current action. Chin prompts say “Lower your chin slightly.” and “Lift your chin slightly.” Muted-by-default spoken main instructions can be enabled with the sound control. Only the camera pixels are mirrored; capture frames and direction cues are not mirrored.
- Progress separates recognized movement, fresh hold checks, confirmed challenges, return to center, submitting and the server result. Active feedback is blue, confirmation has a green check, and corrections have a warning symbol plus text. A `REVIEW` result explicitly says identity is not yet verified.
- Neutral calibration uses the same shared median in guidance and final assessment. Guidance has advisory entering/leaving margins and smoothed visual progress. `HOLDING` maintains calm coaching but **never** counts as evidence. Corrections for wrong direction require two observations to avoid rapidly conflicting messages.
- A hold counts only strict server `DONE`/`CENTERED` results on distinct, usable frames received within 2,000ms of presentation/capture, with no capture gap above 2,000ms. Tracking loss, paused/hidden camera, missing frames, stale responses and network errors reset the hold and its stored evidence array. Final assessment still re-detects every submitted image.
- A native video element remains mounted across all phases. Frame callbacks update refs rather than React state. Captures reuse one canvas; duplicate or stale decoded frames are skipped, capture and server checks are serial, and no backlog is queued.
- Fullscreen scan regions scroll at every breakpoint. Status, hold and help reserve space. Controls have 44px minimum targets, dialog semantics, initial focus, keyboard focus containment and focus restoration. Camera, lighting, distance and tracking recovery messages are specific.

## Calibration bandwidth

Sending both neutral images on every guidance request would fix the median mismatch but increase upload size by approximately 50%. The final implementation sends neutral images when establishing calibration, then reuses an opaque advisory calibration token.

The existing biometric AES-GCM cipher encrypts the median pose with associated data bound to the organization, session, challenge, issued/current policy versions and advisory purpose. Each request still validates session scope, nonce, expiry and single use before opening the token. Invalid tokens cause a full-reference retry; environments without a biometric cipher retain full-reference guidance. Tokens and poses are never persisted by this flow. Final submission always uses raw evidence and never trusts a token, animation or elapsed timer.

The default version is `ACTIVE-GEOMETRY-2026.10.22`. Deployments explicitly overriding `LIVENESS_POLICY_VERSION` should update that label when deploying the stricter movement visibility policy. Movement thresholds, required consecutive evidence frames, random server-selected sequence, challenge expiry, nonce/replay checks, identity continuity and uncalibrated-review behavior remain enforced.

## Measurement and automated validation

Exact measurements, environment, sample counts and test outcomes are recorded in:

- [Native server profile](../artifacts/liveness-guidance-server-profile.json), produced by [benchmark_liveness_guidance.py](../scripts/benchmark_liveness_guidance.py).
- [Consolidated browser results](../artifacts/liveness-improvements/browser-validation-all.json), [browser profile and responsive scenarios](../artifacts/liveness-improvements/browser-profile.json), [camera recovery](../artifacts/liveness-improvements/browser-recovery-validation.json), and [fresh evidence/help/fallback scenarios](../artifacts/liveness-improvements/browser-evidence-validation.json), produced by [profile_liveness_browser.cjs](../scripts/profile_liveness_browser.cjs): all 13 cases passed.
- [Full-app browser regressions](../artifacts/liveness-improvements/browser-app-regressions.json), produced by [test_liveness_browser.cjs](../scripts/test_liveness_browser.cjs): all 11 existing scenarios passed (complete/retry, capture submitted for review, exhausted attempts, camera denial/interruption, positioning/challenge cancellation, stalled response bodies, offline expiry and session-poll recovery). Total browser coverage: 24 passing scenarios.
- [Full Python test log](../artifacts/liveness-guidance-full-tests.txt): 526 tests passed, four optional checks skipped. [Separate native liveness run](../artifacts/liveness-guidance-native-tests.txt): 58 tests passed with the vendored public fixture, no skips.
- [Feedback state tests](../scripts/test_liveness_feedback.cjs) and [built-in capture smoke tests](../scripts/test_capture_client.cjs).

The browser harness compares a pinned pre-change Git revision with the current scan, using Chromium's synthetic camera and mocked provider responses. It measures draw/encode time, canvas allocation, native preview/rAF cadence, DOM mutations, request concurrency, latency and bytes. Four directions are exercised in the browser; the real server continues to select its own three distinct challenge movements. CPU throttling and small viewports approximate slower devices but do not establish performance on physical devices.

Passed cases: baseline/current desktop (1280×800), mobile (390×844, four-times CPU throttle), tablet (768×1024), reduced motion, reserved help (320×568), denied camera access, stopped camera, frozen preview, a stale 2.2-second positive response, tracking loss, calibration-token fallback, and disabling the camera while a positive response is in flight. Stale/lost-tracking images are hashed and asserted absent from final submitted evidence. The in-flight interruption resets the hold and never submits. [Small-phone help screenshot](../artifacts/liveness-improvements/help-320.png) was captured after scrolling the help into view; the header controls remain visible. [Phone downward step](../artifacts/liveness-improvements/current-390-LOOK_DOWN.png) shows the separate arrow region.

Measured local results (100 warm server samples; desktop browser uses a synthetic camera and mocked 50ms provider latency):

| Measurement | Before | After |
| --- | ---: | ---: |
| Server guidance p50 / p95 | 12.97 / 17.81ms | 6.41 / 7.08ms with calibration token |
| Subsequent guidance upload | 76,042B | 38,619B (49.21% lower) |
| Calibration setup | One neutral reference | Both neutral references; 19.89ms p50 and 113,976B |
| Canvas allocations / 24 captures | 25 | 2, including the separate idle exposure probe |
| Browser draw p50 / p95 | 5.2 / 5.9ms | 4.5 / 5.4ms |
| Browser JPEG encode p50 / p95 | 7.1 / 10.0ms | 4.1 / 7.9ms |
| Browser rAF interval p95 | 16.8ms | 16.7ms |
| Mocked complete-run uploaded bytes | 250,565B | 196,960B |
| DOM mutation records | 979 | 1,289, including added phase/hold feedback |
| Maximum concurrent requests | 1 | 1 |

Calibration setup repeats when accepted challenge neutral frames replace positioning frames; later requests reuse the new token. The final raw-evidence submission is included in browser upload totals. Added explicit feedback increases DOM updates, so these results do not support a claim of fewer total UI mutations. Four-times CPU throttling retained a 16.8ms p95 rAF interval and recorded a startup long task; it is not a physical low-end phone measurement.

The native benchmark uses the vendored public face fixture with the installed YuNet model at 640×449 JPEG85. It measures local decode/detection/geometry/quality/token work, excluding HTTP, database and network time. The static fixture is never submitted for identity approval. Model detection did not move to the browser; no browser inference worker was needed.

Verification commands:

```sh
cd Frontend
npm run type-check
npm run build
npx eslint src/pages/verify/components/LivenessStage.tsx src/pages/verify/components/FaceScanRing.tsx src/lib/useCamera.ts src/lib/livenessFeedback.ts src/api/client.ts src/api/types.ts
cd ..
node scripts/test_liveness_feedback.cjs
node scripts/test_capture_client.cjs
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -t .
```

All 31 feedback state checks and the built-in capture smoke pass, along with TypeScript, targeted ESLint, and the production build. The full suite needs localhost networking for webhook receiver tests. Optional native-fixture/database checks may skip when their fixtures or database URL are absent. The Vite production build also reports the existing application bundle size warning; this change does not split unrelated application routes.

## Physical-device validation still required

| Scenario | Automated coverage | Remaining validation |
| --- | --- | --- |
| All four directions and mirrored preview | Projected 3D head tests, translation invariance, raw direction signs and browser arrow/label/transform assertions | Confirm anatomical direction and comfortable small chin movements with live users |
| Bright, dim and uneven lighting | Synthetic quality/recovery cases, overexposure and crop rejection | Real cameras, natural light and mixed indoor light |
| Different skin tones, dark clothing and backgrounds | Opaque badge does not depend on underlying video color | Representative users, detector/quality fairness and contrast over real video |
| Glasses and partial occlusion | Invalid landmark/visibility checks; existing unverified eye/occlusion limitations retained | Clear and reflective glasses, tracking while eyes remain visible |
| Phones/tablets/desktops and slower devices | Browser viewport, reduced-motion, help overflow and CPU-throttling scenarios | iOS Safari, Android Chrome and lower-powered hardware; permission and background/resume behavior |
| Interrupted camera/network/tracking | Freshness, duplicate, stale, lost-face, permission/interruption and calibration fallback tests | Real permission revocation, OS interruptions and constrained mobile networks |
| Anti-spoofing and identity result | Random sequence, nonce, TTL, replay, identity continuity, token tamper/binding and final evidence tests | Existing provider calibration and presentation-attack evaluation remain necessary; five landmarks do not establish eye visibility or strong spoof resistance |

Synthetic ±12° chin tests demonstrate the repaired calibration boundary. They do not establish that every real user can complete the movement comfortably; that requires the live-user/device matrix above.
