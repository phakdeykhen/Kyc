'use strict';
// Dependency-free flow checks with a simulated DOM/API/camera. This does not test
// physical cameras, CSS rendering, browser permissions or real face inference.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
class Element {
  constructor(id) { this.id = id; this.value = ''; this.hidden = false; this.disabled = false; this.checked = false; this.textContent = ''; this.children = []; this.listeners = {}; this.videoWidth = 640; this.videoHeight = 480; this.style = { setProperty() {} }; this.classList = { toggle() {} }; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  reportValidity() { return true; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  querySelector() { return submitButton; }
  play() { return Promise.resolve(); }
  getContext() { return { drawImage() {} }; }
  toBlob(callback) { callback(new Blob(['camera'], { type: 'image/jpeg' })); }
}
const html = fs.readFileSync('src/kyc/web/capture/index.html', 'utf8');
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
assert.equal(ids.length, new Set(ids).size, 'HTML IDs must be unique');
const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
const submitButton = new Element('create-session');
let queue = [], requests = [], cameraRequests = [], stopped = 0, timers = [];
const sessionId = '01234567-1234-4123-8123-123456789012';
const session = status => ({ session_id: sessionId, expected_document_type: 'KH_PASSPORT', status, expires_at: new Date(Date.now() + 600000).toISOString() });
const context = vm.createContext({
  document: { getElementById: id => { assert.ok(elements[id], 'Unknown element ' + id); return elements[id]; }, createElement: tag => new Element(tag) },
  window: { addEventListener(name, callback) { this[name] = callback; } },
  navigator: { mediaDevices: { async getUserMedia(options) { cameraRequests.push(options); return { getTracks: () => [{ stop() { stopped++; } }] }; } } },
  fetch: async (path, options) => { requests.push({ path, options }); assert.ok(queue.length, 'Unexpected request ' + path); const response = queue.shift(); return { ok: response.status < 400, status: response.status, json: async () => response.body }; },
  FormData, Blob, Float32Array, Date, setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout() {}, requestAnimationFrame() {},
});
vm.runInContext(fs.readFileSync('src/kyc/web/capture/capture.js', 'utf8'), context);
const run = source => vm.runInContext(source, context);
const enqueue = (body, status = 200) => queue.push({ body, status });
(async () => {
  elements['api-key'].value = 'a'.repeat(32);
  elements['org-id'].value = '01234567-1234-4123-8123-123456789013';
  elements['existing-session-id'].value = sessionId;
  enqueue(session('SELFIE_REQUIRED'));
  await run('resumeSession()');
  assert.equal(run('state.mode'), 'selfie');
  assert.equal(cameraRequests.at(-1).video.facingMode.ideal, 'user');
  assert.equal(elements['biometric-consent'].checked, false);
  assert.equal(elements['selfie-file'].disabled, true);
  const beforeNoConsent = requests.length;
  await run('submitSelfie(new Blob(["face"], {type: "image/jpeg"}))');
  assert.equal(requests.length, beforeNoConsent, 'Consent must be checked before upload');
  elements['biometric-consent'].checked = true;
  run('updateButtons()');
  assert.equal(elements['selfie-file'].disabled, false);
  enqueue({ capture_status: 'RECAPTURE', status: 'SELFIE_REQUIRED', instructions: ['CENTER_FACE'], attempts_remaining: 9, comparison: null });
  await run('submitSelfie(new Blob(["face"], {type: "image/jpeg"}))');
  assert.equal(run('state.mode'), 'selfie');
  assert.equal(elements.instructions.children[0].textContent, 'Center your face inside the oval.');
  assert.equal(requests.at(-1).options.body.get('biometric_consent'), 'true');
  assert.equal(elements['selfie-file'].disabled, false, 'Recapture stays available');
  enqueue({ capture_status: 'RECAPTURE', status: 'DOCUMENT_REQUIRED', instructions: ['RECAPTURE_DOCUMENT_WITH_CLEAR_PORTRAIT'], attempts_remaining: 8, comparison: null });
  enqueue(session('DOCUMENT_REQUIRED'));
  enqueue({ document_types: [{ type: 'KH_PASSPORT', required_sides: ['DATA_PAGE'] }] });
  await run('submitSelfie(new Blob(["face"], {type: "image/jpeg"}))');
  assert.equal(run('state.mode'), 'document');
  assert.equal(run('state.current'), 'DATA_PAGE');
  assert.equal(cameraRequests.at(-1).video.facingMode.ideal, 'environment');
  const quality = Object.fromEntries(['blur_score', 'glare_score', 'brightness_score', 'shadow_score', 'document_coverage', 'perspective_score', 'resolution_score', 'overall_quality'].map(key => [key, .9]));
  enqueue({ capture_status: 'ACCEPTED', side: 'DATA_PAGE', status: 'DOCUMENT_PROCESSING', instructions: [], quality, sides: { DATA_PAGE: 'ACCEPTED' }, attempts_remaining: 19 });
  enqueue(session('DOCUMENT_PROCESSING'));
  await run('submit(new Blob(["doc"], {type: "image/jpeg"}))');
  assert.equal(run('state.mode'), 'processing');
  assert.ok(timers.length, 'Processing schedules a refresh');
  enqueue(session('SELFIE_REQUIRED'));
  await run('refreshSession()');
  assert.equal(run('state.mode'), 'selfie');
  assert.equal(elements['biometric-consent'].checked, false, 'New face stage resets consent');
  elements['biometric-consent'].checked = true;
  run('updateButtons()');
  enqueue({ capture_status: 'ACCEPTED', status: 'LIVENESS_REQUIRED', instructions: [], attempts_remaining: 7, comparison: { score: .9, metric: 'COSINE_SIMILARITY', result: 'REVIEW', calibrated: false } });
  enqueue(session('LIVENESS_REQUIRED'));
  await run('submitSelfie(new Blob(["face"], {type: "image/jpeg"}))');
  assert.equal(run('state.mode'), 'liveness', 'Accepted selfie continues to liveness');
  assert.equal(cameraRequests.at(-1).video.facingMode.ideal, 'user');
  assert.equal(elements['selfie-file'].disabled, true);
  assert.equal(elements['liveness-start'].disabled, false);
  assert.equal(elements.scores.children.length, 0, 'No similarity percentage displayed');
  assert.match(elements.verdict.textContent, /comparison needs review/);
  // Liveness: random steps from the server, two raw frames per step, nonce echoed back.
  run('state.stepDelayMs = 0');
  const challenge = { challenge_id: '11111111-1111-4111-8111-111111111111', nonce: 'n'.repeat(43),
    steps: [{ index: 0, step: 'LOOK_STRAIGHT', instruction: 'Look straight at the camera.' },
            { index: 1, step: 'TURN_RIGHT', instruction: 'Slowly turn your head to your right.' },
            { index: 2, step: 'LOOK_DOWN', instruction: 'Tilt your head down slightly.' },
            { index: 3, step: 'TURN_LEFT', instruction: 'Slowly turn your head to your left.' }] };
  enqueue(challenge);
  enqueue({ status: 'LIVENESS_REQUIRED', result: 'REVIEW', retry_allowed: true, attempts_remaining: 3,
            instructions: ['FOLLOW_EACH_INSTRUCTION'], reason_codes: ['CHALLENGE_NOT_COMPLETED'] });
  await run('runLiveness()');
  const upload = requests.at(-1).options.body;
  assert.equal(upload.get('nonce'), challenge.nonce);
  assert.equal(upload.get('frame_steps'), '0,0,1,1,2,2,3,3');
  assert.equal(upload.getAll('frames').length, 8);
  assert.equal(run('state.mode'), 'liveness', 'Retry keeps the liveness step open');
  assert.match(elements.verdict.textContent, /try the movement check again/);
  enqueue(challenge);
  enqueue({ status: 'PROCESSING', result: 'REVIEW', retry_allowed: false, attempts_remaining: 2, instructions: [], score: 1 });
  await run('runLiveness()');
  assert.equal(run('state.mode'), 'done');
  assert.equal(run('state.stream'), null);
  assert.match(elements.verdict.textContent, /needs review/);
  assert.equal(elements.scores.children.length, 0, 'No liveness score displayed');
  assert.ok(stopped >= 4, 'Camera tracks stop at stage handoffs');
  enqueue(session('PROCESSING'));
  enqueue({ status: 'LIVENESS_REQUIRED', face_comparison: { result: 'REVIEW', score: .9 }, decision: null });
  await run('resumeSession()');
  assert.match(elements.verdict.textContent, /comparison needs review/, 'Resumed result preserves review');
  context.navigator.mediaDevices.getUserMedia = async () => { throw new Error('Camera blocked'); };
  enqueue(session('SELFIE_REQUIRED'));
  await run('resumeSession()');
  assert.equal(run('state.stream'), null);
  assert.equal(elements['biometric-consent'].checked, false);
  elements['biometric-consent'].checked = true;
  run('updateButtons()');
  assert.equal(elements['selfie-shoot'].disabled, true);
  assert.equal(elements['selfie-file'].disabled, false, 'Upload remains available without camera');
  enqueue({ detail: 'Face processing unavailable.', reason_code: 'FACE_MODELS_UNAVAILABLE' }, 503);
  await run('submitSelfie(new Blob(["face"], {type: "image/jpeg"}))');
  assert.equal(run('state.busy'), false);
  assert.equal(elements['selfie-file'].disabled, false, 'Failed submission unlocks controls');
  assert.match(elements.verdict.textContent, /unavailable/);
  assert.match(elements['selfie-hint'].textContent, /could not be completed/);
  assert.equal(queue.length, 0);
  context.window.pagehide();
  console.log('Capture UI smoke passed: consent, resume, recapture, document handoff/polling, cameras, review, liveness challenge/retry/finish, unavailable engine, stopped streams and no probability.');
})().catch(error => { console.error(error); process.exitCode = 1; });
