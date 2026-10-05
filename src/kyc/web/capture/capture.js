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
  REDUCE_LIGHT: "Move out of direct bright light.",
  EVEN_LIGHTING: "Use even light across your face and avoid strong shadows.",
};
const SCORE_LABELS = {
  blur_score: "Sharpness", glare_score: "No glare", brightness_score: "Exposure", shadow_score: "Even light",
  document_coverage: "Coverage", perspective_score: "Alignment", resolution_score: "Resolution", overall_quality: "Overall",
};
const state = { apiKey: "", orgId: "", session: null, sides: [], current: null, stream: null, busy: false,
  mode: "setup", cameraVersion: 0, pollTimer: null, polling: false };
const $ = (id) => document.getElementById(id);
const cameraElement = (id) => $(state.mode === "selfie" ? `selfie-${id}` : id);

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
  $("shoot").disabled = state.busy || state.mode !== "document" || !state.current || !state.stream;
  $("file").disabled = state.busy || state.mode !== "document" || !state.current;
  const maySubmitSelfie = !state.busy && state.mode === "selfie" && $("biometric-consent").checked;
  $("selfie-shoot").disabled = !maySubmitSelfie || !state.stream;
  $("selfie-file").disabled = !maySubmitSelfie;
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
  $("biometric-consent").checked = false;
  $("result").hidden = true;
  setMode("setup");
}

function sessionInfo() {
  $("session-info").hidden = false;
  $("session-info").textContent = `Session ${state.session.session_id} · expires ${new Date(state.session.expires_at).toLocaleTimeString()}`;
}

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
  } else if (status === "DOCUMENT_PROCESSING") {
    stopCamera();
    setMode("processing");
    $("processing-info").textContent = "Please wait while your document is processed.";
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
    $("verdict").className = "verdict retry";
    $("verdict").textContent = status === "EXPIRED" ? "Session expired"
      : result.body.face_comparison ? comparisonVerdict(result.body.face_comparison) : "Capture submitted";
    $("instructions").replaceChildren();
    $("scores").replaceChildren();
    $("next").textContent = "Additional verification or review is still required before an identity decision.";
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
  const isSelfie = state.mode === "selfie";
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
    let hint = state.mode === "selfie" ? "Light and focus OK. Center your face in the oval and look at the camera." : "Light and focus OK. Fit the document in the frame and take the photo.";
    if (mean < 60) hint = TEXT.MORE_LIGHT;
    else if (mean > 225) hint = TEXT.LESS_LIGHT;
    else if (sharpness < 40) hint = TEXT.HOLD_STILL;
    cameraElement("hint").textContent = hint;
    cameraElement("frame").classList.toggle("good", hint.startsWith("Light and focus OK"));
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

async function submit(blob) {
  if (state.busy || state.mode !== "document" || !state.current || !blob) return;
  state.busy = true;
  updateButtons();
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
    setMode("done");
  }
}

function showResult(body) {
  $("result").hidden = false;
  const accepted = body.capture_status === "ACCEPTED";
  $("verdict").className = `verdict ${accepted ? "ok" : "retry"}`;
  $("verdict").textContent = accepted ? `${body.side.replace("_", " ")} accepted` : `Please retake the ${body.side.replace("_", " ")}`;
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
    : `All sides accepted. Session status: ${body.status}. The document engine takes over from here.`;
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
  if (state.mode === "selfie" || state.mode === "document") {
    cameraElement("hint").textContent = "Submission could not be completed. Check the message below and try again.";
  }
}

$("session-form").addEventListener("submit", startSession);
$("resume-session").addEventListener("click", resumeSession);
$("refresh-session").addEventListener("click", refreshSession);
$("biometric-consent").addEventListener("change", updateButtons);
$("shoot").addEventListener("click", async () => submit(await grabFrame()));
$("selfie-shoot").addEventListener("click", async () => submitSelfie(await grabFrame()));
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
