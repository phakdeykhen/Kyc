"use strict";
// Exercise the actual TypeScript tracker without adding a browser/test dependency.
// These are synthetic server answers, not face inference or identity verification.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require(path.resolve(__dirname, "../Frontend/node_modules/typescript"));

const filename = path.resolve(__dirname, "../Frontend/src/lib/livenessFeedback.ts");
const { outputText } = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  fileName: filename,
});
const moduleObject = { exports: {} };
vm.runInNewContext(outputText, { module: moduleObject, exports: moduleObject.exports }, { filename });
const { LivenessFeedback, MAX_GUIDE_AGE_MS, MAX_HOLD_GAP_MS } = moduleObject.exports;
let checks = 0;
const check = (name, run) => {
  try { run(); checks++; }
  catch (error) { error.message = `${name}: ${error.message}`; throw error; }
};

const guide = (state, options = {}) => ({
  step: "LOOK_DOWN", face: "OK", state, progress: state === "DONE" ? 1 : 0, ...options,
});
const sample = (tracker, state, { id = 1, at = 1000, now = at + 20, usable = true, ...body } = {}) =>
  tracker.update(guide(state, body), { frameId: id, capturedAt: at }, now, usable);

for (const direction of ["TURN_RIGHT", "TURN_LEFT", "LOOK_DOWN", "LOOK_UP"]) {
  check(`${direction} progresses from detection through strict hold and confirmation`, () => {
    const tracker = new LivenessFeedback(3, true);
    const moving = sample(tracker, "KEEP_GOING", { step: direction, progress: .5 });
    assert.equal(moving.phase, "active");
    assert.equal(moving.accepted, false);
    assert.equal(moving.complete, false);
    assert.equal(tracker.progress, .225);
    for (let count = 1; count <= 3; count++) {
      const result = sample(tracker, "DONE", { step: direction, id: count + 1, at: 1000 + count * 650 });
      assert.equal(result.fresh, true);
      assert.equal(result.accepted, true);
      assert.equal(tracker.held, count);
      assert.equal(result.complete, count === 3);
      assert.equal(result.phase, count === 3 ? "confirmed" : "hold");
    }
  });
  check(`${direction} cannot complete with CENTERED or visual progress alone`, () => {
    const tracker = new LivenessFeedback(2, true);
    for (let index = 1; index <= 10; index++) {
      const result = sample(tracker, index % 2 ? "KEEP_GOING" : "CENTERED", {
        step: direction, id: index, at: index * 650, progress: 1,
      });
      assert.equal(result.accepted, false);
      assert.equal(result.complete, false);
      assert.equal(tracker.held, 0);
    }
  });
}

check("centering accepts only consecutive CENTERED answers", () => {
  const tracker = new LivenessFeedback(2, false);
  assert.equal(sample(tracker, "DONE").accepted, false);
  assert.equal(sample(tracker, "CENTERED", { id: 2, at: 1650 }).phase, "hold");
  assert.equal(sample(tracker, "CENTERED", { id: 3, at: 2300 }).complete, true);
});

check("an interrupted server-positive sequence must start its hold again", () => {
  const tracker = new LivenessFeedback(2, true);
  assert.equal(sample(tracker, "DONE").complete, false);
  assert.equal(sample(tracker, "KEEP_GOING", { id: 2, at: 1650, progress: .9 }).complete, false);
  assert.equal(tracker.held, 0);
  assert.equal(sample(tracker, "DONE", { id: 3, at: 2300 }).complete, false);
  assert.equal(sample(tracker, "DONE", { id: 4, at: 2950 }).complete, true);
});

for (const repeatedId of [1, 0]) {
  check(`duplicate or older frame ${repeatedId} cannot count toward a hold`, () => {
    const tracker = new LivenessFeedback(2, true);
    sample(tracker, "DONE");
    const repeated = sample(tracker, "DONE", { id: repeatedId, at: 1650 });
    assert.equal(repeated.fresh, false);
    assert.equal(repeated.accepted, false);
    assert.equal(repeated.complete, false);
    assert.equal(repeated.phase, "correction");
    assert.equal(tracker.held, 0);
    assert.equal(sample(tracker, "DONE", { id: 2, at: 2300 }).complete, false);
  });
}

check("results older than 2000ms are discarded and reset the hold", () => {
  assert.equal(MAX_GUIDE_AGE_MS, 2000);
  const tracker = new LivenessFeedback(2, true);
  sample(tracker, "DONE");
  const stale = sample(tracker, "DONE", { id: 2, at: 1650, now: 1650 + MAX_GUIDE_AGE_MS + 1 });
  assert.equal(stale.fresh, false);
  assert.equal(stale.complete, false);
  assert.equal(tracker.held, 0);
  assert.equal(tracker.progress, 0);
  assert.equal(sample(tracker, "DONE", { id: 3, at: 4300 }).complete, false);
});

check("a result exactly at the freshness boundary is usable", () => {
  const tracker = new LivenessFeedback(2, true);
  assert.equal(sample(tracker, "DONE", { now: 1000 + MAX_GUIDE_AGE_MS }).accepted, true);
});

check("a future capture timestamp cannot create evidence", () => {
  const tracker = new LivenessFeedback(2, true);
  const future = sample(tracker, "DONE", { at: 1000, now: 999 });
  assert.equal(future.fresh, false);
  assert.equal(future.complete, false);
  assert.equal(tracker.held, 0);
});

check("a capture gap longer than 2000ms starts a new hold", () => {
  assert.equal(MAX_HOLD_GAP_MS, 2000);
  const tracker = new LivenessFeedback(2, true);
  sample(tracker, "DONE");
  const afterGap = sample(tracker, "DONE", { id: 2, at: 1000 + MAX_HOLD_GAP_MS + 1 });
  assert.equal(afterGap.fresh, true);
  assert.equal(afterGap.accepted, true);
  assert.equal(afterGap.complete, false);
  assert.equal(tracker.held, 1);
  assert.equal(sample(tracker, "DONE", { id: 3, at: 3651 }).complete, true);
});

check("a capture gap exactly at the boundary remains consecutive", () => {
  const tracker = new LivenessFeedback(2, true);
  sample(tracker, "DONE");
  assert.equal(sample(tracker, "DONE", { id: 2, at: 1000 + MAX_HOLD_GAP_MS }).complete, true);
});

for (const face of ["NO_FACE", "MULTIPLE_FACES", "UNCLEAR"]) {
  check(`${face} invalidates progress and consecutive evidence`, () => {
    const tracker = new LivenessFeedback(2, true);
    sample(tracker, "DONE");
    const lost = sample(tracker, "DONE", { id: 2, at: 1650, face });
    assert.equal(lost.accepted, false);
    assert.equal(lost.complete, false);
    assert.equal(lost.phase, "correction");
    assert.equal(tracker.held, 0);
    assert.equal(tracker.holding, false);
    assert.equal(tracker.progress, 0);
    assert.equal(sample(tracker, "DONE", { id: 3, at: 2300 }).complete, false);
  });
}

check("a hidden, interrupted or otherwise unusable camera sample never counts", () => {
  const tracker = new LivenessFeedback(2, true);
  sample(tracker, "DONE");
  const hidden = sample(tracker, "DONE", { id: 2, at: 1650, usable: false });
  assert.equal(hidden.fresh, false);
  assert.equal(hidden.accepted, false);
  assert.equal(hidden.complete, false);
  assert.equal(tracker.held, 0);
  assert.equal(tracker.holding, false);
  assert.equal(sample(tracker, "DONE", { id: 3, at: 2300 }).complete, false);
});

check("advisory HOLDING never starts or completes a strict hold", () => {
  const tracker = new LivenessFeedback(2, true);
  for (let index = 1; index <= 8; index++) {
    const advisory = sample(tracker, "HOLDING", { id: index, at: index * 650, progress: 1 });
    assert.equal(advisory.accepted, false);
    assert.equal(advisory.complete, false);
    assert.equal(advisory.phase, "active");
    assert.equal(tracker.held, 0);
  }
});

check("HOLDING smooths feedback after detection but resets consecutive evidence", () => {
  const tracker = new LivenessFeedback(2, true);
  sample(tracker, "DONE");
  const advisory = sample(tracker, "HOLDING", { id: 2, at: 1650, progress: .8 });
  assert.equal(advisory.phase, "hold");
  assert.equal(advisory.accepted, false);
  assert.equal(advisory.complete, false);
  assert.equal(tracker.held, 0);
  assert.equal(tracker.holding, true);
  assert.equal(sample(tracker, "DONE", { id: 3, at: 2300 }).complete, false);
  assert.equal(sample(tracker, "DONE", { id: 4, at: 2950 }).complete, true);
});

check("wrong-direction feedback requires two consecutive answers and recovers promptly", () => {
  const tracker = new LivenessFeedback(2, true);
  const first = sample(tracker, "WRONG_DIRECTION");
  assert.equal(first.wrong, false);
  assert.equal(first.phase, "active");
  const second = sample(tracker, "WRONG_DIRECTION", { id: 2, at: 1650 });
  assert.equal(second.wrong, true);
  assert.equal(second.phase, "correction");
  const recovered = sample(tracker, "KEEP_GOING", { id: 3, at: 2300, progress: .5 });
  assert.equal(recovered.wrong, false);
  assert.equal(recovered.phase, "active");
  assert.equal(sample(tracker, "WRONG_DIRECTION", { id: 4, at: 2950 }).wrong, false);
});

check("an invalid frame also resets wrong-direction debounce", () => {
  const tracker = new LivenessFeedback(2, true);
  sample(tracker, "WRONG_DIRECTION");
  sample(tracker, "WRONG_DIRECTION", { id: 2, at: 1650, usable: false });
  assert.equal(sample(tracker, "WRONG_DIRECTION", { id: 3, at: 2300 }).wrong, false);
});

for (const progress of [NaN, Infinity, -Infinity, -10, 10]) {
  check(`invalid or out-of-range progress ${String(progress)} stays bounded and cannot approve`, () => {
    const tracker = new LivenessFeedback(2, true);
    const result = sample(tracker, "KEEP_GOING", { progress });
    assert.ok(Number.isFinite(tracker.progress));
    assert.ok(tracker.progress >= 0 && tracker.progress <= 1);
    assert.equal(tracker.progress, progress === 10 ? .45 : 0);
    assert.equal(result.complete, false);
    assert.equal(tracker.held, 0);
  });
}

check("an explicit reset cannot make an old frame acceptable again", () => {
  const tracker = new LivenessFeedback(2, true);
  sample(tracker, "DONE");
  tracker.reset();
  const replay = sample(tracker, "DONE");
  assert.equal(replay.fresh, false);
  assert.equal(tracker.held, 0);
});

console.log(`Liveness feedback passed ${checks} behavioral checks: four directions, strict fresh holds, replay/stale/gap rejection, tracking/visibility loss, advisory hysteresis, debounced corrections and bounded progress.`);
