"use strict";

// Development capture client. Live hints are advisory only; the API's quality gate decides.
const TEXT = {
  MOVE_CLOSER: "Move closer so the document fills the frame.",
  MOVE_BACK: "Move back so all four corners are visible.",
  CENTER_DOCUMENT: "Center the document inside the frame.",
  HOLD_STILL: "Hold still, the photo is blurry.",
  REDUCE_GLARE: "Tilt slightly or move away from direct light to remove glare.",
  MORE_LIGHT: "Find more light.",
  LESS_LIGHT: "Too bright. Move out of direct light.",
  AVOID_SHADOW: "Remove the shadow across the document.",
  ALIGN_DOCUMENT: "Hold the phone parallel to the document.",
  ONE_DOCUMENT_ONLY: "Show only one document.",
  USE_CONTRASTING_BACKGROUND: "Place the document on a plain, darker surface.",
  USE_HIGHER_RESOLUTION: "Use a higher-resolution camera or photo.",
  CAPTURE_OTHER_SIDE: "That image was already used for another side. Turn the document over.",
  RETAKE_PHOTO: "Please take the photo again.",
  CENTER_FACE: "Center your face inside the oval.",
  REMOVE_OCCLUSION: "Remove anything covering your face and keep both eyes visible.",
  FACE_CAMERA: "Look straight at the camera with your head upright.",
  ONE_FACE_ONLY: "Only your face should be visible in the photo.",
  SHOW_FACE: "Make sure your whole face is visible in the photo.",
  RETAKE_SELFIE: "Please take another selfie.",
  RECAPTURE_DOCUMENT: "Retake your document photo so its portrait is clear and unobstructed.",
  RECAPTURE_DOCUMENT_WITH_CLEAR_PORTRAIT: "Retake your document photo so its portrait is clear and unobstructed.",
  REDUCE_LIGHT: "Move out of direct bright light.",
  EVEN_LIGHTING: "Use even light across your face and avoid strong shadows.",
  FOLLOW_EACH_INSTRUCTION: "Follow each instruction as it appears: move your head clearly, then hold still.",
  ONLY_YOU_IN_FRAME: "Make sure only your face is in view.",
  LOOK_STRAIGHT: "Start by looking straight at the camera.",
};
const SCORE_LABELS = {
  blur_score: "Sharpness", glare_score: "No glare", brightness_score: "Exposure", shadow_score: "Even light",
  document_coverage: "Coverage", perspective_score: "Alignment", resolution_score: "Resolution", overall_quality: "Overall",
};
const state = { apiKey: "", orgId: "", session: null, sides: [], current: null, stream: null, busy: false,
  documentConsentRecorded: false, mode: "setup", cameraVersion: 0, pollTimer: null, polling: false, live: null,
  // Guided liveness timing: one server check per interval (≈90 a minute, inside the client-token rate limit),
  // a stronger hint after nudgeAfterMs without progress, and a help card after helpAfterMs.
  guideIntervalMs: 650, nudgeAfterMs: 5000, helpAfterMs: 15000 };
const $ = (id) => document.getElementById(id);
const CAMERA_PREFIX = { selfie: "selfie-", liveness: "liveness-" };
const cameraElement = (id) => $(`${CAMERA_PREFIX[state.mode] || ""}${id}`);
const frontCamera = () => state.mode === "selfie" || state.mode === "liveness";
const wait = (ms) => (ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve());

function headers() {
  return { "X-API-Key": state.apiKey, "X-Organization-ID": state.orgId };
}

async function api(path, options = {}) {
  try {
    const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
    let body = null;
    try { body = await response.json(); } catch (error) { body = null; }
    return { ok: response.ok, status: response.status, body };
  } catch (error) {
    return { ok: false, status: 0, body: { detail: "Connection failed. Check your connection and try again." } };
  }
}

function updateButtons() {
  const mayCaptureDocument = !state.busy && state.mode === "document" && !!state.current && $("document-consent").checked;
  $("shoot").disabled = !mayCaptureDocument || !state.stream;
  $("file").disabled = !mayCaptureDocument;
  const maySubmitSelfie = !state.busy && state.mode === "selfie" && $("biometric-consent").checked;
  $("selfie-shoot").disabled = !maySubmitSelfie || !state.stream;
  $("selfie-file").disabled = !maySubmitSelfie;
  $("liveness-start").disabled = state.busy || state.mode !== "liveness" || !state.stream;
  $("liveness-start").hidden = !!state.live;
  $("liveness-cancel").hidden = !state.live;
  $("resume-session").disabled = state.busy || state.polling;
  $("session-form").querySelector('button[type="submit"]').disabled = state.busy || state.polling;
  $("refresh-session").disabled = state.polling || state.busy;
}

function stopCamera() {
  state.cameraVersion++;
  if (state.stream) state.stream.getTracks().forEach((track) => track.stop());
  state.stream = null;
  $("video").srcObject = null;
  $("selfie-video").srcObject = null;
  $("liveness-video").srcObject = null;
  updateButtons();
}

function stopPolling() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

function setMode(mode) {
  state.mode = mode;
  $("capture").hidden = mode !== "document";
  $("selfie").hidden = mode !== "selfie";
  $("liveness").hidden = mode !== "liveness";
  $("processing").hidden = mode !== "processing";
  updateButtons();
}

function readCredentials() {
  if (!$("api-key").reportValidity() || !$("org-id").reportValidity()) return false;
  state.apiKey = $("api-key").value.trim();
  state.orgId = $("org-id").value.trim();
  return true;
}

function resetSession() {
  stopPolling();
  stopCamera();
  state.current = null;
  state.session = null;
  state.documentConsentRecorded = false;
  $("document-consent").checked = false;
  $("biometric-consent").checked = false;
  $("result").hidden = true;
  setMode("setup");
}

function sessionInfo() {
  $("session-info").hidden = false;
  $("session-info").textContent = `Session ${state.session.session_id} · expires ${new Date(state.session.expires_at).toLocaleTimeString()}`;
}

// Final outcomes for the person. Reason codes stay with the integrating service, never on this page.
const OUTCOMES = {
  VERIFIED: { css: "ok", text: "Identity verified", next: "You are done. You can close this page." },
  MANUAL_REVIEW: { css: "retry", text: "Your details are being reviewed",
    next: "A member of staff will check your verification. You can close this page." },
  REJECTED: { css: "retry", text: "We could not verify your identity",
    next: "Contact the service that sent you here if you think this is a mistake." },
};

function comparisonVerdict(comparison) {
  if (comparison && comparison.result === "PASS") return "Selfie accepted · face comparison passed";
  if (comparison && comparison.result === "FAIL") return "Selfie accepted · face comparison did not pass";
  return "Selfie accepted · comparison needs review";
}

function renderSides(progress) {
  const list = $("sides");
  list.replaceChildren(...state.sides.map((side) => {
    const item = document.createElement("li");
    item.textContent = side.replace("_", " ");
    if (progress && progress[side] === "ACCEPTED") item.className = "done";
    else if (side === state.current) item.className = "current";
    return item;
  }));
  $("side-label").textContent = state.current ? state.current.replace("_", " ") : "complete";
}

async function startSession(event) {
  event.preventDefault();
  if (state.busy || state.polling || !readCredentials()) return;
  resetSession();
  state.busy = true;
  updateButtons();
  const documentType = $("doc-type").value;
  const created = await api("/v1/kyc/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: $("user-ref").value.trim(), country: $("country").value.trim().toUpperCase(),
                           expected_document_type: documentType, verification_level: "DOCUMENT_FACE_LIVENESS" }),
  });
  state.busy = false;
  updateButtons();
  if (!created.ok) return showError(created);
  state.session = created.body;
  sessionInfo();
  await prepareDocument(documentType);
}

async function prepareDocument(documentType = state.session.expected_document_type) {
  const session = state.session;
  const types = await api("/v1/document-types");
  if (session !== state.session) return;
  const entry = types.ok && types.body.document_types.find((item) => item.type === documentType);
  state.sides = entry ? entry.required_sides : (documentType.includes("PASSPORT") ? ["DATA_PAGE"] : ["FRONT", "BACK"]);
  state.current = state.sides[0];
  $("frame").style.setProperty("--doc-ratio", state.sides[0] === "DATA_PAGE" ? "1.42" : "1.586");
  setMode("document");
  renderSides(null);
  await startCamera();
}

async function resumeSession() {
  if (state.busy || state.polling || !readCredentials()) return;
  const id = $("existing-session-id").value.trim();
  if (!id || !$("existing-session-id").reportValidity()) {
    return showError({ status: 0, body: { detail: "Enter the existing session ID to continue." } });
  }
  resetSession();
  state.busy = true;
  updateButtons();
  const result = await api(`/v1/kyc/${encodeURIComponent(id)}`);
  state.busy = false;
  updateButtons();
  if (!result.ok) return showError(result);
  state.session = result.body;
  sessionInfo();
  await followSession();
}

async function followSession() {
  const session = state.session;
  const status = state.session.status;
  if (status === "SELFIE_REQUIRED") {
    stopPolling();
    if (state.mode !== "selfie") {
      stopCamera();
      state.current = "SELFIE";
      $("biometric-consent").checked = false;
      setMode("selfie");
      $("next").textContent = "Your document is ready. Continue with your selfie.";
      await startCamera();
    }
  } else if (status === "LIVENESS_REQUIRED") {
    stopPolling();
    if (state.mode !== "liveness") {
      stopCamera();
      state.current = "LIVENESS";
      setMode("liveness");
      $("liveness-step").textContent = "";
      $("next").textContent = "Selfie done. Next, a short movement check shows that you are present.";
      await startCamera();
    }
  } else if (status === "NFC_REQUIRED") {
    // Browsers cannot reach passport chips (Web NFC is NDEF-only); the mobile SDK performs this step.
    stopPolling();
    stopCamera();
    state.current = null;
    setMode("processing");
    $("processing-info").textContent = "Next, read your passport chip with the mobile app. Hold the passport against the back of your phone when the app asks.";
    $("next").textContent = "Chip reading needs the mobile app. Use Check progress after the app has finished.";
  } else if (status === "DOCUMENT_PROCESSING") {
    stopCamera();
    setMode("processing");
    $("processing-info").textContent = "Please wait while your document is processed.";
    state.pollTimer = setTimeout(refreshSession, 2000);
  } else if (status === "PROCESSING") {
    // The decision is made seconds after the last step; keep checking until it arrives.
    stopCamera();
    state.current = null;
    setMode("processing");
    $("processing-info").textContent = "Checking your details. This usually takes a few seconds.";
    state.pollTimer = setTimeout(refreshSession, 2000);
  } else if (status === "CREATED" || status === "DOCUMENT_REQUIRED") {
    stopPolling();
    await prepareDocument();
  } else {
    stopPolling();
    stopCamera();
    state.current = null;
    setMode("done");
    const result = await api(`/v1/kyc/${state.session.session_id}/result`);
    if (session !== state.session) return;
    if (!result.ok) return showError(result);
    $("result").hidden = false;
    const outcome = OUTCOMES[status];
    $("verdict").className = `verdict ${outcome ? outcome.css : "retry"}`;
    $("verdict").textContent = status === "EXPIRED" ? "Session expired" : outcome ? outcome.text
      : result.body.face_comparison ? comparisonVerdict(result.body.face_comparison) : "Capture submitted";
    $("instructions").replaceChildren();
    $("scores").replaceChildren();
    $("next").textContent = outcome ? outcome.next : "Additional verification or review is still required before an identity decision.";
  }
}

async function refreshSession() {
  stopPolling();
  if (state.polling || !state.session) return;
  if (Date.parse(state.session.expires_at) <= Date.now()) {
    stopCamera();
    setMode("done");
    return showError({ status: 410, body: { detail: "This session has expired. Create a new session to continue." } });
  }
  state.polling = true;
  updateButtons();
  const result = await api(`/v1/kyc/${state.session.session_id}`);
  state.polling = false;
  updateButtons();
  if (!result.ok) {
    $("processing-info").textContent = "Progress could not be checked. Use Check progress to try again.";
    return showError(result);
  }
  state.session = result.body;
  sessionInfo();
  await followSession();
}

async function startCamera() {
  stopCamera();
  const version = state.cameraVersion;
  const isSelfie = frontCamera();
  const video = cameraElement("video"), hint = cameraElement("hint");
  hint.textContent = "Starting camera…";
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    hint.textContent = isSelfie ? "No camera available here. Consent, then upload a selfie." : "No camera available here. Use Upload a photo.";
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false, video: { facingMode: { ideal: isSelfie ? "user" : "environment" }, width: { ideal: 1920 }, height: { ideal: 1440 } },
    });
    if (version !== state.cameraVersion) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    state.stream = stream;
    video.srcObject = stream;
    await video.play();
    updateButtons();
    requestAnimationFrame((time) => liveHints(time, version));
  } catch (error) {
    if (version !== state.cameraVersion) return;
    stopCamera();
    hint.textContent = isSelfie ? "Camera unavailable. Consent, then upload a selfie." : "Camera unavailable. Use Upload a photo.";
  }
}

// Cheap on-device pre-check: exposure and sharpness inside the frame, about four times a second.
const probe = document.createElement("canvas");
let lastProbe = 0;
function liveHints(time, version) {
  if (!state.stream || !state.current || version !== state.cameraVersion) return;
  const video = cameraElement("video");
  if (time - lastProbe > 250 && video.videoWidth && video.videoHeight) {
    lastProbe = time;
    const width = 240, height = Math.round(240 * (video.videoHeight / video.videoWidth || 0.75));
    probe.width = width; probe.height = height;
    const context = probe.getContext("2d", { willReadFrequently: true });
    context.drawImage(video, 0, 0, width, height);
    const x0 = Math.round(width * 0.15), y0 = Math.round(height * 0.2);
    const data = context.getImageData(x0, y0, width - 2 * x0, height - 2 * y0);
    const w = data.width, h = data.height, lum = new Float32Array(w * h);
    let sum = 0;
    for (let i = 0; i < w * h; i++) {
      const v = 0.299 * data.data[i * 4] + 0.587 * data.data[i * 4 + 1] + 0.114 * data.data[i * 4 + 2];
      lum[i] = v; sum += v;
    }
    const mean = sum / (w * h);
    let lapSum = 0, lapSq = 0, n = 0;
    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        const i = y * w + x;
        const l = lum[i - 1] + lum[i + 1] + lum[i - w] + lum[i + w] - 4 * lum[i];
        lapSum += l; lapSq += l * l; n++;
      }
    }
    const sharpness = lapSq / n - (lapSum / n) ** 2;
    // Exposure and focus only; whether a document is in frame is decided server-side.
    let hint = state.mode === "liveness" ? "Light and focus OK. Press Start, then follow each instruction."
      : state.mode === "selfie" ? "Light and focus OK. Center your face in the oval and look at the camera." : "Light and focus OK. Fit the document in the frame and take the photo.";
    if (mean < 60) hint = TEXT.MORE_LIGHT;
    else if (mean > 225) hint = TEXT.LESS_LIGHT;
    else if (sharpness < 40) hint = TEXT.HOLD_STILL;
    if (!state.live) {  // while a guided check runs, its own feedback owns the hint
      cameraElement("hint").textContent = hint;
      cameraElement("frame").classList.toggle("good", hint.startsWith("Light and focus OK"));
    }
  }
  requestAnimationFrame((nextTime) => liveHints(nextTime, version));
}

function grabFrame() {
  const video = cameraElement("video");
  if (!video.videoWidth || !video.videoHeight) return Promise.resolve(null);
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth; canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
}

async function recordDocumentConsent() {
  // Phase 17: consent to document processing is recorded once per session, before the first upload.
  if (state.documentConsentRecorded) return true;
  const result = await api(`/v1/kyc/${state.session.session_id}/consent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "DOCUMENT_PROCESSING", granted: true }),
  });
  if (!result.ok) {
    showError(result);
    return false;
  }
  state.documentConsentRecorded = true;
  return true;
}

async function submit(blob) {
  if (state.busy || state.mode !== "document" || !state.current || !blob) return;
  if (!$("document-consent").checked) {
    return showError({ status: 0, body: { detail: "Consent to document processing is required before uploading." } });
  }
  state.busy = true;
  updateButtons();
  if (!(await recordDocumentConsent())) {
    state.busy = false;
    return updateButtons();
  }
  $("hint").textContent = "Checking quality…";
  const form = new FormData();
  form.append("side", state.current);
  form.append("file", blob, "capture.jpg");
  const result = await api(`/v1/kyc/${state.session.session_id}/documents`, { method: "POST", body: form });
  state.busy = false;
  updateButtons();
  if (!result.ok) return showError(result);
  showResult(result.body);
  if (!state.current) await refreshSession();
}

async function submitSelfie(blob) {
  if (state.busy || state.mode !== "selfie" || !blob) return;
  if (!$("biometric-consent").checked) {
    return showError({ status: 0, body: { detail: "Consent to biometric processing is required before submitting a selfie." } });
  }
  state.busy = true;
  updateButtons();
  $("selfie-hint").textContent = "Checking your selfie…";
  const form = new FormData();
  form.append("biometric_consent", "true");
  form.append("file", blob, "selfie.jpg");
  const result = await api(`/v1/kyc/${state.session.session_id}/selfie`, { method: "POST", body: form });
  state.busy = false;
  updateButtons();
  if (!result.ok) return showError(result);
  const body = result.body, accepted = body.capture_status === "ACCEPTED";
  $("result").hidden = false;
  const comparisonResult = body.comparison && body.comparison.result;
  $("verdict").className = `verdict ${accepted && comparisonResult === "PASS" ? "ok" : "retry"}`;
  $("verdict").textContent = accepted ? comparisonVerdict(body.comparison) : "Please retake your selfie";
  $("instructions").replaceChildren(...body.instructions.map((code) => {
    const item = document.createElement("li");
    if (code === "MOVE_CLOSER") item.textContent = "Move closer so your face is clearly visible.";
    else if (code === "MOVE_BACK") item.textContent = "Move back so your whole face fits inside the oval.";
    else item.textContent = TEXT[code] || "Please take another clear photo with your whole face visible.";
    return item;
  }));
  $("scores").replaceChildren();
  $("selfie-hint").textContent = accepted ? "Selfie submitted." : "Adjust your position or lighting and try again.";
  $("next").textContent = accepted ? "Face comparison submitted. Additional verification or review is still required."
    : `${body.attempts_remaining} attempts left. Consent remains selected for your next photo.`;
  if (body.status === "DOCUMENT_REQUIRED") {
    $("verdict").textContent = "Please retake your document photo";
    $("next").textContent = "The portrait on your document could not be used. Please provide a clearer document photo.";
    stopCamera();
    await refreshSession();
  }
  if (accepted) {
    stopCamera();
    state.current = null;
    if (body.status === "LIVENESS_REQUIRED") {
      // Keep the comparison verdict visible; refreshing moves the page on to the liveness step.
      await refreshSession();
    } else {
      setMode("done");
    }
  }
}

// Guided active liveness. The server picks a random sequence; for each step the page asks the server,
// a frame at a time, whether the person has done the movement (same geometry as the final check), and
// only moves on once two frames in a row show it. Raw (unmirrored) frames go back with each step index.
const STEP_TEXT = {
  LOOK_STRAIGHT: { say: "Look straight at the camera", cue: "" },
  TURN_LEFT: { say: "Turn your head to your left", cue: "left",
    more: "Turn further, as if looking over your left shoulder.", wrong: "That's the other way. Turn to your left." },
  TURN_RIGHT: { say: "Turn your head to your right", cue: "right",
    more: "Turn further, as if looking over your right shoulder.", wrong: "That's the other way. Turn to your right." },
  LOOK_UP: { say: "Tilt your head up", cue: "up",
    more: "Lift your chin higher, as if looking at the ceiling.", wrong: "That's down. Tilt your head up instead." },
  LOOK_DOWN: { say: "Tilt your head down", cue: "down",
    more: "Lower your chin further, as if looking at the floor.", wrong: "That's up. Tilt your head down instead." },
};
const FACE_TEXT = {
  NO_FACE: "We can't see your face. Keep it inside the oval.",
  MULTIPLE_FACES: "Only your face should be in view.",
  UNCLEAR: "Hold still with your whole face in the oval and good light.",
};
const HOLD_FRAMES = 2;

class LivenessStop extends Error {
  constructor(reason, result) { super(reason); this.reason = reason; this.result = result; }
}

function grabSmallFrame(maxWidth = 640) {
  const video = cameraElement("video");
  if (!video.videoWidth || !video.videoHeight) return Promise.resolve(null);
  const scale = Math.min(1, maxWidth / video.videoWidth);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}

function renderDots(current, done) {
  const steps = state.live.issued.steps;
  $("liveness-dots").hidden = false;
  $("liveness-dots").replaceChildren(...steps.map((step, index) => {
    const dot = document.createElement("li");
    dot.className = index < done ? "done" : index === current ? "current" : "";
    dot.textContent = STEP_TEXT[step.step] ? STEP_TEXT[step.step].say : step.instruction;
    return dot;
  }));
}

function showStep(index, kind) {
  const steps = state.live.issued.steps;
  const text = STEP_TEXT[steps[index].step] || { say: steps[index].instruction, cue: "" };
  $("liveness-step").textContent = kind === "center" ? "Turn your head back to the center" : text.say;
  $("liveness-cue").hidden = kind !== "move" || !text.cue;
  $("liveness-cue").className = `cue ${kind === "move" ? text.cue : ""}`;
  $("liveness-coach").textContent = kind === "move" ? `Step ${index} of ${steps.length - 1} · keep going until the dot turns green`
    : kind === "baseline" ? "Hold still for a moment." : "";
  coach(kind === "move" ? "Move slowly." : "Keep your face inside the oval.", 0);
}

function coach(text, progress, good = false) {
  $("liveness-hint").textContent = text;
  $("liveness-frame").classList.toggle("good", good);
  $("liveness-meter").hidden = progress === null;
  if (progress !== null) $("liveness-meter-fill").style.width = `${Math.round(progress * 100)}%`;
}

async function guideCall(index, blob) {
  const live = state.live, form = new FormData();
  form.append("challenge_id", live.issued.challenge_id);
  form.append("nonce", live.issued.nonce);
  form.append("step", String(index));
  form.append("frame", blob, "frame.jpg");
  if (live.baseline) form.append("baseline", live.baseline, "baseline.jpg");
  return api(`/v1/kyc/${state.session.session_id}/liveness/guide`, { method: "POST", body: form });
}

function askForHelp(index, kind) {
  const step = state.live.issued.steps[index].step;
  const tips = kind === "move" ? [STEP_TEXT[step].more, "Move slowly and keep your whole face inside the oval.",
    "Keep the phone still at eye level and move only your head.", "Make sure your face is evenly lit."]
    : ["Hold the phone at eye level, about an arm's length away.", "Keep your whole face inside the oval.",
       "Find even light and remove anything covering your face."];
  $("liveness-help-tips").replaceChildren(...tips.map((tip) => {
    const item = document.createElement("li");
    item.textContent = tip;
    return item;
  }));
  $("liveness-help").hidden = false;
  coach("Paused.", null);
  return new Promise((resolve, reject) => { state.live.help = { resolve, reject }; });
}

function closeHelp(action) {
  const live = state.live;
  $("liveness-help").hidden = true;
  if (!live || !live.help) return;
  const help = live.help;
  live.help = null;
  if (action === "retry") help.resolve();
  else help.reject(new LivenessStop(action));
}

// Repeats until HOLD_FRAMES consecutive frames satisfy the step; returns those frames.
// kind: "baseline" (clear frontal face), "move" (the step's movement) or "center" (back to the baseline).
async function followStep(index, kind) {
  const live = state.live;
  const text = STEP_TEXT[live.issued.steps[index].step] || {};
  showStep(index, kind);
  let held = [], started = Date.now();
  for (;;) {
    if (state.mode !== "liveness" && !live.stopped) live.stopped = "CANCELLED";  // the page moved on
    if (live.stopped) throw new LivenessStop(live.stopped);
    const tick = Date.now();
    const blob = await grabSmallFrame();
    const result = blob ? await guideCall(index, blob) : null;
    if (live.stopped) throw new LivenessStop(live.stopped);
    let satisfied = false;
    if (result && !result.ok) {
      if (result.status === 409 || result.status === 404) throw new LivenessStop("CHALLENGE_CLOSED", result);
      if (result.status !== 0 && result.status !== 429 && result.status < 500) throw new LivenessStop("ERROR", result);
      coach("The connection is slow. Keep still…", null);
    } else if (result) {
      const body = result.body;
      if (body.face !== "OK") {
        held = [];
        coach(FACE_TEXT[body.face] || FACE_TEXT.UNCLEAR, 0);
      } else {
        satisfied = kind === "baseline" || body.state === (kind === "center" ? "CENTERED" : "DONE");
        const elapsed = Date.now() - started;
        const hint = satisfied ? (held.length + 1 >= HOLD_FRAMES ? "Done!" : "Hold it there…")
          : body.state === "WRONG_DIRECTION" ? text.wrong
          : kind === "center" ? "Look straight at the camera again."
          : elapsed > state.nudgeAfterMs ? text.more : "Keep going, slowly.";
        coach(hint, kind === "move" ? (satisfied ? 1 : body.progress) : (satisfied ? 1 : 0), satisfied);
      }
    }
    if (satisfied) {
      held.push(blob);
      if (held.length >= HOLD_FRAMES) return held;
    } else {
      held = [];
      if (Date.now() - started > state.helpAfterMs) {
        await askForHelp(index, kind);  // resolves on "Try this step again"; rejects on start over / cancel
        showStep(index, kind);
        started = Date.now();
        continue;
      }
    }
    await wait(state.guideIntervalMs - (Date.now() - tick));
  }
}

function stopLiveness(reason) {
  if (!state.live) return;
  state.live.stopped = reason;
  closeHelp(reason);
}

function endLiveness() {
  state.live = null;
  state.busy = false;
  $("liveness-help").hidden = true;
  $("liveness-cue").hidden = true;
  $("liveness-meter").hidden = true;
  $("liveness-dots").hidden = true;
  $("liveness-coach").textContent = "";
  updateButtons();
}

async function runLiveness() {
  if (state.busy || state.mode !== "liveness" || !state.stream) return;
  state.busy = true;
  $("result").hidden = true;
  $("liveness-tips").hidden = true;
  updateButtons();
  const id = state.session.session_id;
  const issued = await api(`/v1/kyc/${id}/liveness/challenge`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (!issued.ok) {
    endLiveness();
    $("liveness-tips").hidden = false;
    return showError(issued);
  }
  state.live = { issued: issued.body, baseline: null, stopped: null, help: null };
  updateButtons();
  const frames = [], steps = [];
  try {
    renderDots(0, 0);
    const baseline = await followStep(0, "baseline");
    state.live.baseline = baseline[0];
    baseline.forEach((blob) => { frames.push(blob); steps.push(0); });
    const sequence = issued.body.steps;
    for (let index = 1; index < sequence.length; index++) {
      renderDots(index, index);
      const done = await followStep(index, "move");
      done.forEach((blob) => { frames.push(blob); steps.push(index); });
      renderDots(index + 1, index + 1);
      if (index < sequence.length - 1) await followStep(0, "center");
    }
  } catch (stop) {
    const reason = stop instanceof LivenessStop ? stop.reason : "ERROR";
    endLiveness();
    $("liveness-tips").hidden = false;
    $("liveness-step").textContent = reason === "CANCELLED" ? "" : "Press I'm ready to try again.";
    if (reason === "RESTART") return runLiveness();
    if (reason === "CHALLENGE_CLOSED") {
      $("result").hidden = false;
      $("verdict").className = "verdict retry";
      $("verdict").textContent = "Time ran out for this check";
      $("instructions").replaceChildren();
      $("scores").replaceChildren();
      $("next").textContent = `Press I'm ready to start a new check. ${issued.body.attempts_remaining} attempts left.`;
    } else if (reason === "ERROR") {
      showError(stop.result || { status: 0, body: { detail: "Something went wrong. Try again." } });
    }
    return;
  }
  $("liveness-step").textContent = "Checking…";
  coach("All steps done. Checking…", null, true);
  const form = new FormData();
  form.append("challenge_id", issued.body.challenge_id);
  form.append("nonce", issued.body.nonce);
  form.append("frame_steps", steps.join(","));
  frames.forEach((blob, index) => form.append("frames", blob, `frame-${index}.jpg`));
  const result = await api(`/v1/kyc/${id}/liveness`, { method: "POST", body: form });
  endLiveness();
  if (!result.ok) {
    $("liveness-tips").hidden = false;
    return showError(result);
  }
  showLiveness(result.body);
}

function showLiveness(body) {
  $("result").hidden = false;
  const retry = body.retry_allowed && body.status === "LIVENESS_REQUIRED";
  $("verdict").className = `verdict ${body.result === "PASS" ? "ok" : "retry"}`;
  $("verdict").textContent = retry ? "Please try the movement check again"
    : body.result === "FAIL" ? "The movement check did not pass"
    : body.result === "PASS" ? "Movement check passed" : "Movement check recorded · needs review";
  $("instructions").replaceChildren(...(body.instructions || []).map((code) => {
    const item = document.createElement("li");
    item.textContent = TEXT[code] || "Follow each instruction on screen.";
    return item;
  }));
  $("scores").replaceChildren();
  $("liveness-step").textContent = retry ? "Press I'm ready to try again." : "";
  $("liveness-tips").hidden = !retry;
  if (retry) {
    $("next").textContent = `${body.attempts_remaining} attempts left.`;
  } else {
    stopCamera();
    state.current = null;
    setMode("done");
    $("next").textContent = "Your capture is complete. We are checking your details.";
    if (body.status === "PROCESSING") state.pollTimer = setTimeout(refreshSession, 2000);
  }
}

function showResult(body) {
  $("result").hidden = false;
  const accepted = body.capture_status === "ACCEPTED";
  $("verdict").className = `verdict ${accepted ? "ok" : "retry"}`;
  $("verdict").textContent = accepted ? `${body.side.replace("_", " ")} photo accepted` : `Please retake the ${body.side.replace("_", " ")}`;
  $("instructions").replaceChildren(...body.instructions.map((code) => {
    const item = document.createElement("li");
    item.textContent = TEXT[code] || "Please take another clear document photo.";
    return item;
  }));
  $("scores").replaceChildren(...Object.entries(SCORE_LABELS).flatMap(([key, label]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const value = document.createElement("dd");
    const meter = document.createElement("span");
    meter.className = "meter";
    const fill = document.createElement("i");
    fill.style.width = `${Math.round(body.quality[key] * 100)}%`;
    meter.append(fill);
    const number = document.createElement("span");
    number.textContent = body.quality[key].toFixed(2);
    value.append(meter, number);
    return [term, value];
  }));
  const remaining = state.sides.filter((side) => body.sides[side] !== "ACCEPTED");
  state.current = remaining[0] || null;
  renderSides(body.sides);
  $("next").textContent = state.current
    ? `Next: capture the ${state.current.replace("_", " ")}. ${body.attempts_remaining} attempts left.`
    : "Photo quality passed for all sides. We are now checking your document details. Identity verification is still in progress.";
  if (!state.current) {
    stopCamera();
    $("hint").textContent = "Capture complete.";
  }
}

function showError(result) {
  $("result").hidden = false;
  $("verdict").className = "verdict retry";
  const detail = result.body && result.body.detail;
  $("verdict").textContent = typeof detail === "string" ? detail : `Request failed (${result.status}).`;
  $("instructions").replaceChildren();
  $("scores").replaceChildren();
  $("next").textContent = "";
  if (state.mode === "selfie" || state.mode === "document" || state.mode === "liveness") {
    cameraElement("hint").textContent = "Submission could not be completed. Check the message below and try again.";
  }
}

$("session-form").addEventListener("submit", startSession);

// Development convenience: a link ending in #key=…&org=… pre-fills the form (e.g. from a QR code on
// the operator's screen). The fragment is never sent to any server, and it is removed from the
// address bar and this history entry at once.
(function prefillFromFragment() {
  if (typeof location === "undefined" || !location.hash || location.hash.length < 2) return;
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.get("key")) $("api-key").value = params.get("key");
  if (params.get("org")) $("org-id").value = params.get("org");
  if (typeof history !== "undefined" && history.replaceState) history.replaceState(null, "", location.pathname);
})();
$("resume-session").addEventListener("click", resumeSession);
$("refresh-session").addEventListener("click", refreshSession);
$("biometric-consent").addEventListener("change", updateButtons);
$("document-consent").addEventListener("change", updateButtons);
$("shoot").addEventListener("click", async () => submit(await grabFrame()));
$("selfie-shoot").addEventListener("click", async () => submitSelfie(await grabFrame()));
$("liveness-start").addEventListener("click", runLiveness);
$("liveness-retry-step").addEventListener("click", () => closeHelp("retry"));
$("liveness-restart").addEventListener("click", () => stopLiveness("RESTART"));
$("liveness-cancel").addEventListener("click", () => stopLiveness("CANCELLED"));
$("file").addEventListener("change", (event) => {
  const [file] = event.target.files;
  event.target.value = "";
  if (file) submit(file);
});
$("selfie-file").addEventListener("change", (event) => {
  const [file] = event.target.files;
  event.target.value = "";
  if (file) submitSelfie(file);
});
window.addEventListener("pagehide", () => { stopPolling(); stopCamera(); });
