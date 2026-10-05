"use strict";
// Reviewer dashboard. Every value from the API is rendered with textContent, never as HTML.
// The token lives only in this object; nothing is written to storage.

const state = { org: null, token: null, me: null, current: null, imageUrls: [] };
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

async function api(path, options = {}) {
  try {
    const response = await fetch(path, { ...options, headers: {
      Authorization: `Bearer ${state.token}`, "X-Organization-ID": state.org,
      ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) } });
    if (options.raw) return { ok: response.ok, status: response.status, response };
    let body = null;
    try { body = await response.json(); } catch (error) { body = null; }
    return { ok: response.ok, status: response.status, body };
  } catch (error) {
    return { ok: false, status: 0, body: { detail: "Connection failed. Try again." } };
  }
}

function pill(value) {
  return el("span", { class: `pill ${value || ""}`, text: value || "—" });
}

function short(id) { return String(id).slice(0, 8); }

function since(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 2880) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} d`;
}

// Sign in ---------------------------------------------------------------------------
async function signIn(event) {
  event.preventDefault();
  state.org = $("org-id").value.trim();
  state.token = $("token").value.trim();
  $("token").value = "";
  const result = await api("/v1/review/me");
  if (!result.ok) {
    state.token = null;
    $("sign-in-error").hidden = false;
    $("sign-in-error").textContent = result.status === 401 ? "That token is not valid for this organization."
      : (result.body && result.body.detail) || "Sign-in failed.";
    return;
  }
  state.me = result.body;
  $("sign-in-error").hidden = true;
  $("sign-in").hidden = true;
  $("workspace").hidden = false;
  $("who").hidden = false;
  $("sign-out").hidden = false;
  $("who").textContent = `${state.me.display_name} · ${state.me.role.toLowerCase()}`;
  await loadQueue();
}

function signOut() {
  clearImages();
  Object.assign(state, { org: null, token: null, me: null, current: null });
  $("workspace").hidden = true;
  $("who").hidden = true;
  $("sign-out").hidden = true;
  $("sign-in").hidden = false;
  $("case-body").hidden = true;
  $("case-empty").hidden = false;
}

// Queue -----------------------------------------------------------------------------
async function loadQueue() {
  const result = await api("/v1/review/queue?limit=100");
  if (!result.ok) return signOutOnAuth(result);
  const { items, total } = result.body;
  $("queue-count").textContent = `(${total})`;
  $("queue-empty").hidden = items.length > 0;
  $("queue").replaceChildren(...items.map((item) => {
    const flags = item.high_signals.length ? `${item.high_signals.length} high signal${item.high_signals.length > 1 ? "s" : ""}`
      : item.medium_signals.length ? `${item.medium_signals.length} medium` : "no signals";
    const button = el("button", { type: "button", "data-id": item.session_id }, [
      el("span", { class: "line" }, [el("span", { class: "id", text: short(item.session_id) }),
                                     el("span", { text: `waiting ${since(item.in_review_since)}` })]),
      el("span", { class: "line" }, [el("span", { text: `${item.expected_document_type} · ${item.country}` }),
                                     el("span", { text: flags })]),
      el("span", { class: "line muted", text: item.reason_codes.slice(0, 3).join(", ") }),
    ]);
    if (state.current && state.current.session.session_id === item.session_id) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => openCase(item.session_id));
    return el("li", {}, button);
  }));
}

function signOutOnAuth(result) {
  if (result.status === 401) signOut();
}

// Case ------------------------------------------------------------------------------
function clearImages() {
  state.imageUrls.forEach((url) => URL.revokeObjectURL(url));
  state.imageUrls = [];
}

async function openCase(sessionId) {
  const result = await api(`/v1/review/${sessionId}`);
  if (!result.ok) {
    signOutOnAuth(result);
    $("case-empty").hidden = false;
    $("case-empty").textContent = (result.body && result.body.detail) || "The case could not be opened.";
    return;
  }
  state.current = result.body;
  document.querySelectorAll(".queue-list button").forEach((button) =>
    button.toggleAttribute("aria-current", button.dataset.id === sessionId));
  renderCase(result.body);
}

function renderCase(data) {
  clearImages();
  const { session, risk } = data;
  $("case-empty").hidden = true;
  $("case-body").hidden = false;
  $("case-title").textContent = `Case ${short(session.session_id)} · ${session.expected_document_type}`;
  $("case-meta").textContent = `${session.verification_level} · ${session.country} · customer ref ${session.user_id} · `
    + `${session.status === "MANUAL_REVIEW" ? "in review" : "last changed"} ${since(session.updated_at)} ago · `
    + `expires ${new Date(session.expires_at).toLocaleString()}`;
  $("case-decision").replaceWith(Object.assign(pill(session.status), { id: "case-decision" }));
  $("case-reasons").replaceChildren(...(risk ? risk.reason_codes : []).map((code) => el("code", { text: code })));

  renderImages(data);
  renderIdentity(data);
  $("checks").replaceChildren(...Object.entries(data.checks).sort().map(([name, value]) =>
    el("div", {}, [el("span", { text: name.replaceAll("_", " ") }), pill(value)])));
  $("signals").replaceChildren(...(data.fraud_signals.length ? data.fraud_signals.map((item) => el("li", {}, [
    el("span", { class: "top" }, [pill(item.severity), el("strong", { text: item.signal }),
                                  el("span", { class: "muted small", text: item.category.toLowerCase() })]),
    el("span", { class: "muted small", text: [item.fields.length ? `fields: ${item.fields.join(", ")}` : "",
      item.sources.length ? `sources: ${item.sources.join(" vs ")}` : "",
      Object.keys(item.details).length ? JSON.stringify(item.details) : ""].filter(Boolean).join(" · ") }),
  ])) : [el("li", { class: "muted", text: "No fraud signals." })]));
  renderEvidence(data);
  $("trace").replaceChildren(...(risk ? risk.trace : []).map((item) =>
    el("li", { text: `${item.outcome} · ${item.reason} (${item.rule})` })));
  $("history").replaceChildren(...(data.history.length ? data.history.map((item) => el("li", {}, [
    el("span", { class: "top" }, [pill(item.action), el("strong", { text: item.reason_code }),
                                  el("span", { class: "muted small", text: `${item.reviewer} · ${new Date(item.decided_at).toLocaleString()}` })]),
    item.note ? el("span", { text: item.note }) : null,
  ])) : [el("li", { class: "muted", text: "No earlier reviews." })]));
  renderDecisionForm(data);
}

async function renderImages(data) {
  const box = $("images");
  if (!data.permissions.includes("VIEW_IMAGES")) {
    box.replaceChildren(el("p", { class: "muted small", text: "Your role cannot view photos." }));
    return;
  }
  if (!data.images.length) {
    box.replaceChildren(el("p", { class: "muted small", text: "No photos are retained for this case (removed by retention, or never stored)." }));
    return;
  }
  const sessionId = data.session.session_id;
  box.replaceChildren();
  for (const image of data.images) {
    const result = await api(`/v1/review/${sessionId}/images/${image.image_id}`, { raw: true });
    if (!state.current || state.current.session.session_id !== sessionId) return;  // case changed meanwhile
    const caption = el("figcaption", { text: `${image.kind.replace("_", " ").toLowerCase()} · kept until ${new Date(image.available_until).toLocaleString()}` });
    if (!result.ok) { box.append(el("figure", {}, [caption, el("p", { class: "muted small", text: "Not available." })])); continue; }
    const url = URL.createObjectURL(await result.response.blob());
    state.imageUrls.push(url);
    box.append(el("figure", {}, [el("img", { src: url, alt: image.kind.replace("_", " ").toLowerCase() }), caption]));
  }
}

function renderIdentity(data) {
  const rows = [];
  if (data.document) {
    rows.push(["Document number", data.document.document_number || "—"]);
    rows.push(["Issuing country", data.document.issuing_country || "—"]);
    rows.push(["Classification confidence", data.document.classification_confidence == null ? "—"
      : `${Math.round(data.document.classification_confidence * 100)}%`]);
  }
  const canSee = data.permissions.includes("VIEW_IDENTITY");
  const body = $("identity");
  body.replaceChildren(...rows.map(([key, value]) => el("tr", {}, [el("th", { text: key }), el("td", { text: value })])));
  for (const field of data.fields) {
    const value = canSee ? (field.value ?? "—") : "hidden for your role";
    const meta = `${Math.round(field.confidence * 100)}% · ${field.source}${field.flags.length ? ` · ${field.flags.join(", ")}` : ""}`;
    body.append(el("tr", {}, [el("th", { text: field.name.replaceAll("_", " ") }),
      el("td", {}, [el("span", { class: canSee ? (field.name === "mrz" ? "mono" : "") : "hidden-value", text: value }),
                    el("div", { class: "muted small", text: meta })])]));
  }
  if (!rows.length && !data.fields.length) body.append(el("tr", {}, el("td", { class: "muted", text: "No extracted document data." })));
}

function section(title, pairs) {
  const items = pairs.filter(([, value]) => value !== undefined && value !== null && value !== "");
  return el("section", {}, [el("h4", { text: title }),
    ...items.map(([key, value]) => el("div", { text: `${key}: ${typeof value === "object" ? JSON.stringify(value) : value}` }))]);
}

function renderEvidence(data) {
  const parts = [];
  if (data.mrz) parts.push(section("MRZ", [["format", data.mrz.format], ["valid", data.mrz.valid],
    ["check digits", data.mrz.check_digits], ["consistency", data.mrz.field_consistency]]));
  data.barcodes.forEach((item) => parts.push(section(`Barcode (${item.symbology})`, [["format valid", item.format_valid],
    ["signature", item.signature_present ? (item.signature_valid ? "valid" : "INVALID") : "none"], ["fields", item.fields]])));
  if (data.nfc) parts.push(section("Passport chip", [["status", data.nfc.status], ["passive authentication", data.nfc.passive_authentication],
    ["active authentication", data.nfc.active_authentication], ["consistency", data.nfc.document_consistency],
    ["reasons", data.nfc.reason_codes.join(", ")]]));
  data.face_comparisons.forEach((item) => parts.push(section(`Face vs ${item.reference.toLowerCase().replace("_", " ")}`, [
    ["score", item.score], ["result", item.result], ["calibrated", item.calibrated], ["policy", item.policy_version]])));
  if (data.liveness) parts.push(section("Liveness", [["result", data.liveness.result], ["score", data.liveness.score],
    ["attack type", data.liveness.attack_type], ["calibrated", data.liveness.calibrated], ["reasons", data.liveness.reason_codes.join(", ")]]));
  $("evidence").replaceChildren(...(parts.length ? parts : [el("p", { class: "muted small", text: "No further evidence recorded." })]));
}

// Decision --------------------------------------------------------------------------
function renderDecisionForm(data) {
  const canDecide = Object.keys(data.decision_options).length > 0;
  const open = data.session.status === "MANUAL_REVIEW";
  $("decision").hidden = !(canDecide && open);
  $("read-only").hidden = canDecide || !open;
  $("decision").reset();
  $("reason").disabled = true;
  $("reason").replaceChildren(el("option", { value: "", text: "Choose an action first" }));
  $("decision-error").hidden = true;
  updateSubmit();
}

function chooseAction() {
  const action = document.querySelector("input[name=action]:checked");
  if (!action || !state.current) return;
  const codes = state.current.decision_options[action.value] || [];
  $("reason").replaceChildren(el("option", { value: "", text: "Choose a reason" }),
    ...codes.map((code) => el("option", { value: code, text: code.replaceAll("_", " ").toLowerCase() })));
  $("reason").disabled = false;
  updateSubmit();
}

function updateSubmit() {
  const action = document.querySelector("input[name=action]:checked");
  $("submit-decision").disabled = !action || !$("reason").value || $("note").value.trim().length < 5;
}

async function submitDecision(event) {
  event.preventDefault();
  const action = document.querySelector("input[name=action]:checked").value;
  $("submit-decision").disabled = true;
  const result = await api(`/v1/review/${state.current.session.session_id}/decision`, { method: "POST", body: JSON.stringify({
    action, reason_code: $("reason").value, note: $("note").value.trim(), expected_version: state.current.session.version }) });
  if (!result.ok) {
    signOutOnAuth(result);
    const body = result.body || {};
    const message = body.reason_code === "APPROVAL_BLOCKED"
      ? `Cannot approve: ${body.blockers.join(", ")}. Request recapture or reject instead.`
      : body.reason_code === "CASE_CHANGED" ? "The case changed since you opened it. It has been reloaded; review it again."
      : body.detail || "The decision was not saved.";
    // Reload first (it resets the form), then show why the decision was not saved.
    if (body.reason_code === "CASE_CHANGED" || body.reason_code === "CASE_NOT_IN_REVIEW") await openCase(state.current.session.session_id);
    $("decision-error").hidden = false;
    $("decision-error").textContent = message;
    updateSubmit();
    return;
  }
  await openCase(result.body.session_id);
  await loadQueue();
}

$("sign-in-form").addEventListener("submit", signIn);
$("sign-out").addEventListener("click", signOut);
$("refresh").addEventListener("click", loadQueue);
$("actions").addEventListener("change", chooseAction);
$("reason").addEventListener("change", updateSubmit);
$("note").addEventListener("input", updateSubmit);
$("decision").addEventListener("submit", submitDecision);
window.addEventListener("pagehide", clearImages);
