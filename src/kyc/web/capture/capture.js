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
};
const SCORE_LABELS = {
  blur_score: "Sharpness", glare_score: "No glare", brightness_score: "Exposure", shadow_score: "Even light",
  document_coverage: "Coverage", perspective_score: "Alignment", resolution_score: "Resolution", overall_quality: "Overall",
};
const state = { apiKey: "", orgId: "", session: null, sides: [], current: null, stream: null, busy: false };
const $ = (id) => document.getElementById(id);

function headers() {
  return { "X-API-Key": state.apiKey, "X-Organization-ID": state.orgId };
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
  let body = null;
  try { body = await response.json(); } catch (error) { body = null; }
  return { ok: response.ok, status: response.status, body };
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
  state.apiKey = $("api-key").value.trim();
  state.orgId = $("org-id").value.trim();
  const documentType = $("doc-type").value;
  const created = await api("/v1/kyc/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: $("user-ref").value.trim(), country: $("country").value.trim().toUpperCase(),
                           expected_document_type: documentType, verification_level: "DOCUMENT_FACE_LIVENESS" }),
  });
  if (!created.ok) return showError(created);
  state.session = created.body;
  const types = await api("/v1/document-types");
  const entry = types.ok && types.body.document_types.find((item) => item.type === documentType);
  state.sides = entry ? entry.required_sides : ["FRONT", "BACK"];
  state.current = state.sides[0];
  $("frame").style.setProperty("--doc-ratio", state.sides[0] === "DATA_PAGE" ? "1.42" : "1.586");
  $("session-info").hidden = false;
  $("session-info").textContent = `Session ${state.session.session_id} · expires ${new Date(state.session.expires_at).toLocaleTimeString()}`;
  $("capture").hidden = false;
  renderSides(null);
  startCamera();
}

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    $("hint").textContent = "No camera available here. Use “Upload a photo”.";
    return;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1440 } },
    });
    $("video").srcObject = state.stream;
    await $("video").play();
    $("shoot").disabled = false;
    requestAnimationFrame(liveHints);
  } catch (error) {
    $("hint").textContent = "Camera permission was not granted. Use “Upload a photo”.";
  }
}

// Cheap on-device pre-check: exposure and sharpness inside the frame, about four times a second.
const probe = document.createElement("canvas");
let lastProbe = 0;
function liveHints(time) {
  if (!state.stream || !state.current) return;
  if (time - lastProbe > 250) {
    lastProbe = time;
    const video = $("video");
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
    let hint = "Light and focus OK. Fit the document in the frame and take the photo.";
    if (mean < 60) hint = TEXT.MORE_LIGHT;
    else if (mean > 225) hint = TEXT.LESS_LIGHT;
    else if (sharpness < 40) hint = TEXT.HOLD_STILL;
    $("hint").textContent = hint;
    $("frame").classList.toggle("good", hint.startsWith("Light and focus OK"));
  }
  requestAnimationFrame(liveHints);
}

function grabFrame() {
  const video = $("video");
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth; canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
}

async function submit(blob) {
  if (state.busy || !state.current || !blob) return;
  state.busy = true;
  $("shoot").disabled = true;
  $("hint").textContent = "Checking quality…";
  const form = new FormData();
  form.append("side", state.current);
  form.append("file", blob, "capture.jpg");
  const result = await api(`/v1/kyc/${state.session.session_id}/documents`, { method: "POST", body: form });
  state.busy = false;
  $("shoot").disabled = !state.stream;
  if (!result.ok) return showError(result);
  showResult(result.body);
}

function showResult(body) {
  $("result").hidden = false;
  const accepted = body.capture_status === "ACCEPTED";
  $("verdict").className = `verdict ${accepted ? "ok" : "retry"}`;
  $("verdict").textContent = accepted ? `${body.side.replace("_", " ")} accepted` : `Please retake the ${body.side.replace("_", " ")}`;
  $("instructions").replaceChildren(...body.instructions.map((code) => {
    const item = document.createElement("li");
    item.textContent = TEXT[code] || code;
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
    $("shoot").disabled = true;
    if (state.stream) state.stream.getTracks().forEach((track) => track.stop());
    state.stream = null;
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
}

$("session-form").addEventListener("submit", startSession);
$("shoot").addEventListener("click", async () => submit(await grabFrame()));
$("file").addEventListener("change", (event) => {
  const [file] = event.target.files;
  event.target.value = "";
  if (file) submit(file);
});
