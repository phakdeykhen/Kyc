"use strict";

const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  history: '<path d="M3 11a9 9 0 1 1 2.7 7.3"/><path d="M3 4v7h7M12 7v5l3 2"/>',
  monitor: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 13-2l2 2M4 17l2 2a8 8 0 0 0 13-2"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  shield: '<path d="M12 3 20 6v6c0 5-8 9-8 9S4 17 4 12V6z"/><path d="m8 12 3 3 5-6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert: '<path d="m12 3 10 18H2zM12 9v5M12 17h.01"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h5"/>',
  upload: '<path d="M12 16V3m-4 4 4-4 4 4M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  download: '<path d="M12 3v13m-4-4 4 4 4-4M4 17v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  note: '<path d="M4 3h16v14l-5 4H4zM15 17h5M8 8h8M8 12h6"/>',
};

const STATUS = { under_review: "Under review", needs_information: "Needs information", approved: "Approved", rejected: "Rejected" };
const ACTION = { created: "Case created", document_uploaded: "Document uploaded", document_verified: "Document checked", verification_removed: "Check removed", note_added: "Review note added", approved: "Case approved", rejected: "Case rejected", needs_information: "Information requested", under_review: "Sent to review queue" };
const state = { cases: [], activity: [], filter: "all", risk: "all", search: "", view: "queue", detail: null, busy: false };
let toastTimer;
let detailRequest = 0;
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ICONS.file}</svg>`;
const cap = (value) => value ? value[0].toUpperCase() + value.slice(1) : "";
const initials = (name) => name.trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
const badge = (value, label) => `<span class="badge ${esc(value)}">${esc(label || STATUS[value] || cap(value))}</span>`;
const date = (value) => new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const fullDate = (value) => new Date(value).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const relative = (value) => {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
};
const activityIcon = (action) => ({ approved: "check", rejected: "close", needs_information: "alert", document_verified: "shield", document_uploaded: "upload", note_added: "note", created: "plus" }[action] || "clock");

async function request(path, body) {
  const response = await fetch(path, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "The request could not be completed.");
  return payload;
}

function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4500);
}

function errorBox(selector, message) {
  const element = $(selector);
  element.textContent = message;
  element.hidden = !message;
}

async function loadWorkspace() {
  try {
    const [cases, activity] = await Promise.all([request("/api/cases"), request("/api/activity")]);
    state.cases = cases.cases;
    state.activity = activity.activity;
    errorBox("#load-error", "");
    renderWorkspace();
  } catch (error) {
    errorBox("#load-error", `${error.message} Use Refresh to try again.`);
    if (!state.cases.length) {
      $("#stats").innerHTML = '<div class="loading-panel">The workspace is unavailable.</div>';
      $("#results-count").textContent = "Could not load cases";
    }
  }
}

function renderWorkspace() {
  const open = state.cases.filter((item) => !["approved", "rejected"].includes(item.status));
  const summaries = [
    { label: "Under review", number: state.cases.filter((c) => c.status === "under_review").length, caption: "Ready for a document check", icon: "clock", tone: "", filter: "under_review" },
    { label: "Approved", number: state.cases.filter((c) => c.status === "approved").length, caption: "Both documents checked", icon: "shield", tone: "green", filter: "approved" },
    { label: "Needs information", number: state.cases.filter((c) => c.status === "needs_information").length, caption: "Waiting for customer details", icon: "file", tone: "amber", filter: "needs_information" },
    { label: "High risk", number: open.filter((c) => c.risk_level === "high").length, caption: "Open cases to review closely", icon: "alert", tone: "red", filter: "high" },
  ];
  $("#stats").innerHTML = summaries.map((stat) => `<button class="stat" data-summary="${stat.filter}" aria-label="Show ${esc(stat.label.toLowerCase())} cases"><span><span class="stat-label">${stat.label}</span><span class="stat-number">${stat.number}</span><span class="stat-description">${stat.caption}</span></span><span class="stat-icon ${stat.tone}">${icon(stat.icon)}</span></button>`).join("");
  $("#nav-count").textContent = open.length;
  $("#case-count").textContent = state.cases.length;
  renderCases();
  renderActivity();
}

function renderCases() {
  const query = state.search.trim().toLowerCase();
  const filtered = state.cases.filter((item) => {
    const statusMatch = state.filter === "all" || (state.filter === "open" ? !["approved", "rejected"].includes(item.status) : item.status === state.filter);
    return statusMatch && (state.risk === "all" || item.risk_level === state.risk) && (!query || [item.full_name, item.email, item.reference, item.country].some((v) => v.toLowerCase().includes(query)));
  });
  $("#cases-body").innerHTML = filtered.map((item) => `<tr>
    <td><button class="customer-button" data-case="${item.id}" aria-label="Review ${esc(item.full_name)}"><span class="avatar tone-${item.id % 4}">${esc(initials(item.full_name))}</span><span><span class="customer-name">${esc(item.full_name)}</span><span class="customer-meta">${esc(item.reference)} · ${esc(item.country)}</span></span></button></td>
    <td>${badge(item.risk_level)}</td>
    <td><span class="document-progress"><span>${item.verified_count}/2 checked</span><progress max="2" value="${item.verified_count}" aria-label="${item.verified_count} of 2 documents checked"></progress></span></td>
    <td>${badge(item.status)}</td><td class="received"><time datetime="${esc(item.created_at)}" title="${esc(fullDate(item.created_at))}">${esc(date(item.created_at))}</time></td>
  </tr>`).join("");
  $("#empty-state").hidden = filtered.length !== 0;
  $("#results-count").textContent = `Showing ${filtered.length} of ${state.cases.length} cases`;
  document.querySelectorAll("[data-filter]").forEach((button) => {
    const active = button.dataset.filter === state.filter;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  $("#risk-filter").value = state.risk;
}

function renderActivity() {
  $("#recent-activity").innerHTML = state.activity.slice(0, 4).map((item) => `<div class="recent-item"><span class="activity-dot ${esc(item.action)}">${icon(activityIcon(item.action))}</span><div><button class="text-button recent-name" data-case="${item.case_id}">${esc(item.full_name)}</button><span class="recent-action">${esc(ACTION[item.action] || item.action)}</span><time class="recent-time" datetime="${esc(item.created_at)}">${esc(relative(item.created_at))}</time></div></div>`).join("") || '<div class="recent-item secondary-text">No activity yet.</div>';
  $("#activity-list").innerHTML = state.activity.map((item) => `<article class="activity-row"><span class="activity-dot ${esc(item.action)}">${icon(activityIcon(item.action))}</span><div class="activity-row-content"><button class="text-button" data-case="${item.case_id}">${esc(item.full_name)}</button> <span class="secondary-text">· ${esc(ACTION[item.action] || item.action)}</span><p>${esc(item.detail)}</p><div class="activity-row-meta">${esc(item.reference)} · ${esc(item.actor)} · ${esc(fullDate(item.created_at))}</div></div><time datetime="${esc(item.created_at)}">${esc(relative(item.created_at))}</time></article>`).join("") || '<div class="empty-state"><h3>No review history yet</h3><p>Create a customer case to begin.</p></div>';
}

function changeView(view) {
  state.view = view;
  const isQueue = view === "queue";
  $("#queue-view").hidden = !isQueue;
  $("#activity-view").hidden = isQueue;
  $("#page-title").textContent = isQueue ? "Customer verification" : "Activity log";
  $("#page-subtitle").textContent = isQueue ? "Review documents and resolve customer cases." : "Follow document checks, notes, and review decisions.";
  $("#breadcrumb-title").textContent = isQueue ? "Customer verification" : "Activity log";
  document.querySelectorAll(".nav-item").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

async function openCase(id) {
  const requestNumber = ++detailRequest;
  $("#detail-content").innerHTML = `<div class="dialog-heading"><h2 id="detail-title">Loading case…</h2><button class="icon-button" data-close="detail-dialog" aria-label="Close case">${icon("close")}</button></div><div class="dialog-body"><p>Retrieving customer details.</p></div>`;
  if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
  try {
    const detail = await request(`/api/cases/${id}`);
    if (requestNumber !== detailRequest || !$("#detail-dialog").open) return;
    state.detail = detail;
    renderDetail();
  } catch (error) {
    if (requestNumber !== detailRequest || !$("#detail-dialog").open) return;
    $("#detail-content").innerHTML = `<div class="dialog-heading"><h2 id="detail-title">Case unavailable</h2><button class="icon-button" data-close="detail-dialog" aria-label="Close case">${icon("close")}</button></div><div class="dialog-body"><p>${esc(error.message)}</p><button class="button secondary" data-case="${Number(id)}">Try again</button></div>`;
  }
}

function renderDocument(kind, title, closed) {
  const doc = state.detail.documents.find((entry) => entry.kind === kind);
  const disabled = closed || state.busy;
  return `<section class="document-card" aria-label="${title}"><div class="document-card-head"><span class="document-title">${icon("file")}${title}</span><span class="document-status ${doc?.verified ? "checked" : ""}">${doc?.verified ? "Checked" : doc ? "To check" : "Missing"}</span></div>
    ${doc ? `<span class="file-name">${esc(doc.original_name)}</span><div class="file-meta">${(doc.size / 1024).toFixed(1)} KB · ${doc.is_sample ? "Fictional sample PDF" : esc(date(doc.uploaded_at))}</div><a class="file-download" href="${esc(doc.download_url)}" download>${icon("download")}Download document</a>` : '<p class="document-empty">No document uploaded.</p>'}
    <div class="document-controls"><label class="upload-button ${disabled ? "disabled" : ""}">${icon("upload")}${doc ? "Replace file" : "Upload file"}<input class="visually-hidden" type="file" data-upload="${kind}" accept=".pdf,.png,.jpg,.jpeg" aria-label="Upload ${title.toLowerCase()}" ${disabled ? "disabled" : ""}></label>
    <label class="verification-checkbox"><input type="checkbox" data-verify="${kind}" ${doc?.verified ? "checked" : ""} ${!doc || disabled ? "disabled" : ""}>Document checked</label></div></section>`;
}

function renderDetail(draft = "") {
  const c = state.detail;
  if (!c) return;
  const closed = ["approved", "rejected"].includes(c.status);
  const allChecked = c.documents.length === 2 && c.documents.every((doc) => doc.verified);
  $("#detail-content").innerHTML = `<div class="dialog-heading"><div><div class="detail-person"><span class="avatar tone-${c.id % 4}">${esc(initials(c.full_name))}</span><div><h2 id="detail-title">${esc(c.full_name)}</h2><span class="customer-meta">${esc(c.reference)} · Created ${esc(fullDate(c.created_at))}</span></div></div><div class="detail-tags">${badge(c.status)}${badge(c.risk_level, `${cap(c.risk_level)} risk`)}</div></div><button class="icon-button" data-close="detail-dialog" aria-label="Close case">${icon("close")}</button></div>
    <div id="detail-error" class="form-error detail-error" role="alert" hidden></div><div class="detail-grid"><div class="detail-main"><h3>Customer details</h3><dl class="profile-grid"><div><dt>Email address</dt><dd>${esc(c.email)}</dd></div><div><dt>Country</dt><dd>${esc(c.country)}</dd></div><div><dt>Customer type</dt><dd>${esc(cap(c.customer_type))}</dd></div><div><dt>Phone number</dt><dd>${esc(c.phone || "Not provided")}</dd></div><div class="full-width"><dt>Account purpose</dt><dd>${esc(c.purpose || "Not provided")}</dd></div></dl>
    <div class="section-heading"><h3>Required documents</h3><span>PDF, PNG, JPG · up to 5 MB</span></div>${renderDocument("identity", "Identity document", closed)}${renderDocument("address", "Proof of address", closed)}
    <label class="review-label" for="review-note">Review note<textarea id="review-note" class="review-note" rows="3" maxlength="2000" placeholder="Record your findings or explain the next step." ${state.busy ? "disabled" : ""}>${esc(draft)}</textarea></label><div class="note-actions"><span>Required for rejection, information requests, and reopening.</span><button class="button secondary small" id="save-note" ${state.busy ? "disabled" : ""}>Save note</button></div></div>
    <aside class="detail-history"><h3>Case history</h3>${c.activity.map((a) => `<div class="history-entry"><strong>${esc(ACTION[a.action] || a.action)}</strong><p>${esc(a.detail)}</p><time datetime="${esc(a.created_at)}">${esc(fullDate(a.created_at))}</time></div>`).join("")}</aside></div>
    <div class="dialog-footer review-footer"><span class="review-footer-hint">${state.busy ? "Saving changes…" : closed ? "Case closed. Reopen it to review documents again." : allChecked ? "Both documents checked. Ready for your decision." : "Upload and check both documents to approve."}</span><div class="review-actions">${closed ? `<button class="button primary" data-decision="under_review" ${state.busy ? "disabled" : ""}>Reopen case</button>` : `<button class="button danger" data-decision="rejected" ${state.busy ? "disabled" : ""}>Reject</button><button class="button warning" data-decision="needs_information" ${state.busy || c.status === "needs_information" ? "disabled" : ""}>Request information</button><button class="button success" data-decision="approved" ${!allChecked || state.busy ? "disabled" : ""}>${icon("check")}Approve case</button>`}</div></div>`;
}

async function updateCase(path, body, message, clearDraft = false) {
  if (state.busy) return;
  const draft = $("#review-note")?.value || "";
  const active = document.activeElement;
  const focusAttribute = active?.dataset?.verify ? ["data-verify", active.dataset.verify] : active?.id ? ["id", active.id] : null;
  state.busy = true;
  renderDetail(draft);
  try {
    state.detail = await request(path, body);
    state.busy = false;
    renderDetail(clearDraft ? "" : draft);
    await loadWorkspace();
    toast(message);
  } catch (error) {
    state.busy = false;
    renderDetail(draft);
    errorBox("#detail-error", error.message);
  } finally {
    state.busy = false;
    if (focusAttribute) $("#detail-content").querySelector(`[${focusAttribute[0]}="${focusAttribute[1]}"]`)?.focus();
  }
}

function fileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("The file could not be read. Choose it again."));
    reader.readAsDataURL(file);
  });
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target || target.disabled) return;
  if (target.dataset.close) {
    if (state.busy) return;
    document.getElementById(target.dataset.close).close();
  } else if (target.dataset.view) changeView(target.dataset.view);
  else if (target.dataset.case) await openCase(Number(target.dataset.case));
  else if (target.dataset.filter) { state.filter = target.dataset.filter; renderCases(); }
  else if (target.dataset.summary) {
    state.search = ""; $("#search").value = "";
    state.filter = target.dataset.summary === "high" ? "open" : target.dataset.summary;
    state.risk = target.dataset.summary === "high" ? "high" : "all";
    renderCases();
  } else if (target.id === "add-customer") {
    $("#create-form").reset(); errorBox("#create-error", ""); $("#create-dialog").showModal();
  } else if (["refresh", "refresh-activity"].includes(target.id)) {
    target.disabled = true; await loadWorkspace(); target.disabled = false;
  } else if (target.id === "clear-filters") {
    state.filter = "all"; state.risk = "all"; state.search = ""; $("#search").value = ""; renderCases();
  } else if (target.id === "save-note" && state.detail) {
    const note = $("#review-note").value.trim();
    if (!note) { errorBox("#detail-error", "Write a note before saving."); $("#review-note").focus(); return; }
    await updateCase(`/api/cases/${state.detail.id}/notes`, { note }, "Review note saved.", true);
  } else if (target.dataset.decision && state.detail) {
    const status = target.dataset.decision;
    const note = $("#review-note").value.trim();
    if (["rejected", "needs_information", "under_review"].includes(status) && !note) {
      errorBox("#detail-error", "Add a review note explaining this decision."); $("#review-note").focus(); return;
    }
    await updateCase(`/api/cases/${state.detail.id}/review`, { status, note }, `${STATUS[status]}. Decision saved.`, true);
  }
});

document.addEventListener("change", async (event) => {
  const target = event.target;
  if (target.id === "risk-filter") { state.risk = target.value; renderCases(); }
  else if (target.dataset.verify && state.detail) {
    await updateCase(`/api/cases/${state.detail.id}/documents/${target.dataset.verify}/verify`, { verified: target.checked }, target.checked ? "Document marked as checked." : "Document check removed.");
  } else if (target.dataset.upload && state.detail) {
    const file = target.files?.[0];
    if (!file) return;
    if (!file.size || file.size > 5 * 1024 * 1024) { errorBox("#detail-error", "Choose a non-empty PDF, PNG, or JPEG no larger than 5 MB."); target.value = ""; return; }
    const caseId = state.detail.id;
    const kind = target.dataset.upload;
    try {
      const content = await fileAsBase64(file);
      await updateCase(`/api/cases/${caseId}/documents/${kind}`, { filename: file.name, content_base64: content }, "Document uploaded. Check it before approving.");
    } catch (error) { errorBox("#detail-error", error.message); target.value = ""; }
  }
});

$("#search").addEventListener("input", (event) => { state.search = event.target.value; renderCases(); });
$("#create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#create-submit");
  if (button.disabled) return;
  const body = Object.fromEntries(new FormData(event.target));
  button.disabled = true; button.textContent = "Creating…";
  errorBox("#create-error", "");
  try {
    const created = await request("/api/cases", body);
    $("#create-dialog").close();
    state.filter = "all"; state.risk = "all"; state.search = ""; $("#search").value = "";
    changeView("queue"); await loadWorkspace();
    toast("Customer case created."); await openCase(created.id);
  } catch (error) { errorBox("#create-error", error.message); }
  finally { button.disabled = false; button.textContent = "Create case"; }
});
$("#detail-dialog").addEventListener("cancel", (event) => { if (state.busy) event.preventDefault(); });
$("#detail-dialog").addEventListener("close", () => { ++detailRequest; state.detail = null; });
$("#today").textContent = new Date().toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
document.querySelectorAll("[data-icon]").forEach((element) => { element.innerHTML = icon(element.dataset.icon); });
loadWorkspace();
