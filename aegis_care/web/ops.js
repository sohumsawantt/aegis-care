/* AEGIS-Care Operations Console.
 * Vanilla JS single-page app. Session token is held in sessionStorage so a
 * closed tab ends the session, which is the behaviour clinical software wants.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const el = (t, c, h) => { const n = document.createElement(t); if (c) n.className = c;
  if (h !== undefined) n.innerHTML = h; return n; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const TOKEN_KEY = "aegis.ops.token";
const A = {
  token: sessionStorage.getItem(TOKEN_KEY) || null,
  me: null, meta: null, page: "dashboard", params: {}, counts: {},
};

/* ---------------- api ---------------- */
async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (A.token) headers.Authorization = `Bearer ${A.token}`;
  const res = await fetch(path, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) { signOutLocal(); throw new Error("Your session ended. Please sign in again."); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch (e) { /* keep */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

function toast(message, kind = "") {
  const t = el("div", `toast ${kind}`, esc(message));
  $("toast").appendChild(t);
  setTimeout(() => t.remove(), 5200);
}

/* ---------------- formatting ---------------- */
const fmtTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined,
    { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
};
const ago = (iso) => {
  if (!iso) return "";
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const h = Math.floor(mins / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
};
const sevPill = (s) => `<span class="pill sev-${esc(s)}">${esc(s)}</span>`;
const statusPill = (s) => `<span class="st st-${esc(s)}">${esc(String(s).replace(/_/g, " "))}</span>`;
const msChip = (s) => `<span class="ms ms-${esc(s)}">${esc(s)}</span>`;
const can = (p) => Boolean(A.me?.permissions?.includes(p));
const initials = (name) => String(name || "?").replace(/[^A-Za-z ]/g, "")
  .split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";

/* ================================================================== */
/* SIGN IN                                                             */
/* ================================================================== */
async function loadMeta() {
  A.meta = await api("/api/ops/meta");
  const box = $("accounts");
  if (!A.meta.demo_accounts.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<h4>Sandbox accounts</h4>
    <p>This instance runs on synthetic records. Pick a role to sign in.</p>`;
  A.meta.demo_accounts.forEach((acct) => {
    const b = el("button", "account-btn",
      `<span class="av">${esc(initials(acct.display_name))}</span>
       <span style="min-width:0">
         <b>${esc(acct.display_name)}</b>
         <span>${esc(acct.role.replace(/_/g, " "))}${acct.unit ? " · " + esc(acct.unit) : ""}</span>
       </span>`);
    b.type = "button";
    b.addEventListener("click", () => {
      $("username").value = acct.username;
      $("password").value = acct.password;
      $("signin-form").requestSubmit();
    });
    box.appendChild(b);
  });
}

$("signin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("signin-btn");
  btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Signing in…';
  $("signin-error").innerHTML = "";
  try {
    const out = await api("/api/ops/session", {
      method: "POST",
      body: { username: $("username").value.trim(), password: $("password").value },
    });
    A.token = out.token; A.me = out.operator;
    sessionStorage.setItem(TOKEN_KEY, A.token);
    await enterApp();
  } catch (err) {
    $("signin-error").innerHTML =
      `<div class="banner crit mb"><div class="ic">!</div><div>${esc(err.message)}</div></div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Sign in";
  }
});

function signOutLocal() {
  A.token = null; A.me = null;
  sessionStorage.removeItem(TOKEN_KEY);
  $("shell").classList.remove("on");
  $("signin").style.display = "flex";
}

$("signout").addEventListener("click", async () => {
  try { await api("/api/ops/session", { method: "DELETE" }); } catch (e) { /* ignore */ }
  signOutLocal();
});

/* ================================================================== */
/* NAV                                                                 */
/* ================================================================== */
const PAGES = [
  { sec: "Work" },
  { id: "dashboard", label: "Dashboard", ico: "◧", perm: "view_worklist" },
  { id: "worklist", label: "Incidents", ico: "☰", perm: "view_worklist", count: "open" },
  { id: "review", label: "Review queue", ico: "⚖", perm: "review_quarantine", count: "review" },
  { id: "report", label: "Report an issue", ico: "＋", perm: "report_incident" },
  { sec: "Records" },
  { id: "patients", label: "Patient memory", ico: "◍", perm: "view_patient_memory" },
  { id: "audit", label: "Audit trail", ico: "▤", perm: "view_audit" },
  { sec: "Account" },
  { id: "access", label: "Your access", ico: "⚿", perm: null },
];

function renderNav() {
  const nav = $("nav");
  nav.innerHTML = "";
  let pending = null;
  PAGES.forEach((p) => {
    if (p.sec) { pending = p.sec; return; }
    if (p.perm && !can(p.perm)) return;
    if (pending) { nav.appendChild(el("div", "nav-sec", esc(pending))); pending = null; }
    const item = el("div", "navitem" + (A.page === p.id ? " active" : ""));
    const n = p.count === "open" ? A.counts.open : p.count === "review" ? A.counts.review : null;
    item.innerHTML = `<span class="ico">${p.ico}</span><span>${esc(p.label)}</span>` +
      (n ? `<span class="count${p.count === "review" && n ? " alert" : ""}">${n}</span>` : "");
    item.addEventListener("click", () => go(p.id));
    nav.appendChild(item);
  });
}

function go(page, params = {}) {
  A.page = page; A.params = params;
  renderNav();
  const titles = {
    dashboard: "Dashboard", worklist: "Incidents", report: "Report an issue",
    review: "Review queue", patients: "Patient memory", audit: "Audit trail",
    access: "Your access", spread: "Contamination spread",
    incident: params.id || "Incident", patient: params.id || "Patient",
  };
  $("page-title").textContent = titles[page] || page;
  $("page-crumb").textContent = "";
  $("topbar-actions").innerHTML = "";
  $("content").innerHTML = '<div class="empty"><span class="spin dark"></span> Loading…</div>';
  const render = {
    dashboard: pageDashboard, worklist: pageWorklist, report: pageReport,
    review: pageReview, patients: pagePatients, audit: pageAudit,
    incident: pageIncident, patient: pagePatient, access: pageAccess,
    spread: pageSpread,
  }[page];
  (render ? render(params) : Promise.resolve()).catch((e) => {
    $("content").innerHTML =
      `<div class="banner crit"><div class="ic">!</div><div>${esc(e.message)}</div></div>`;
  });
}

async function refreshCounts() {
  try {
    const d = await api("/api/ops/dashboard");
    A.counts.open = d.open_incidents;
    A.counts.review = d.awaiting_review;
    renderNav();
  } catch (e) { /* non-fatal */ }
}

async function enterApp() {
  $("signin").style.display = "none";
  $("shell").classList.add("on");
  $("who-av").textContent = initials(A.me.display_name);
  $("who-name").textContent = A.me.display_name;
  $("who-role").textContent = `${A.me.role_label}${A.me.unit ? " · " + A.me.unit : ""}`;
  const env = A.meta.environment;
  const badge = $("env-badge");
  badge.textContent = env.label;
  badge.classList.toggle("live", env.live_fhir);
  await refreshCounts();
  go(can("view_worklist") ? "dashboard" : "report");
}

/* ================================================================== */
/* DASHBOARD                                                           */
/* ================================================================== */
async function pageDashboard() {
  const d = await api("/api/ops/dashboard");
  A.counts.open = d.open_incidents; A.counts.review = d.awaiting_review; renderNav();
  const c = $("content"); c.innerHTML = "";

  if (can("confirm_seed") && !A.meta.environment.live_fhir) {
    const btn = el("button", "btn sec sm", "Run a drill");
    btn.addEventListener("click", drillDialog);
    $("topbar-actions").appendChild(btn);
  }

  if (d.active_exposures > 0) {
    c.appendChild(el("div", "banner crit mb",
      `<div class="ic">!</div><div><b>${d.active_exposures} active restricted-information
       exposure(s).</b> Protected content is sitting in memory belonging to a role that is
       not authorised to hold it. These should be recovered immediately.</div>`));
  }

  const tiles = el("div", "grid g4");
  const sev = d.by_severity || {};
  [
    ["Open incidents", d.open_incidents, "awaiting resolution", d.open_incidents ? "info" : "okv"],
    ["Critical", sev.critical || 0, "wrong patient or disclosure", (sev.critical ? "crit" : "")],
    ["Awaiting review", d.awaiting_review, "quarantined artifacts", d.awaiting_review ? "warnv" : ""],
    ["Active exposures", d.active_exposures, "restricted data in memory",
      d.active_exposures ? "crit" : "okv"],
  ].forEach(([k, v, s, kind]) => tiles.appendChild(el("div", `stat ${kind}`,
    `<div class="k">${esc(k)}</div><div class="v">${v}</div><div class="s">${esc(s)}</div>`)));
  c.appendChild(tiles);

  const grid = el("div", "grid g2 mt");

  const recent = el("div", "card");
  recent.innerHTML = `<header><h3>Open incidents</h3><div class="grow"></div></header>`;
  const body = el("div", "body tight");
  if (!d.recent.length) {
    body.appendChild(emptyWorklistGuide());
  } else {
    body.appendChild(incidentTable(d.recent));
  }
  recent.appendChild(body);
  grid.appendChild(recent);

  const mem = el("div", "card");
  mem.innerHTML = "<header><h3>Agent memory by role</h3></header>";
  const mb = el("div", "body");
  Object.entries(d.memory_totals).forEach(([role, stats]) => {
    const total = stats.total || 0;
    mb.appendChild(el("div", "row", `
      <div style="flex:1"><b>${esc(role.replace(/_/g, " "))}</b>
        <div class="small dim">${total} version(s) ·
          ${stats.active || 0} active · ${stats.repaired || 0} repaired ·
          ${stats.quarantined || 0} quarantined · ${stats.tombstoned || 0} tombstoned</div>
      </div>`));
    mb.appendChild(el("div", "mb"));
  });
  mem.appendChild(mb);
  grid.appendChild(mem);
  c.appendChild(grid);
}

/** An empty queue is the normal state. Say what to do next rather than
 *  showing a blank panel, which reads as a broken screen. */
function emptyWorklistGuide() {
  const wrap = el("div", "empty");
  wrap.innerHTML = `<div class="big">✓</div>
    <div class="et">No open incidents</div>
    <div class="es">Nothing has been reported against agent memory.</div>`;
  const row = el("div", "row", "");
  row.style.cssText = "justify-content:center;margin-top:18px";

  if (can("report_incident")) {
    const b = el("button", "btn sec", "Report an issue");
    b.addEventListener("click", () => go("report"));
    row.appendChild(b);
  }
  if (can("confirm_seed") && !A.meta.environment.live_fhir) {
    const b = el("button", "btn", "Run a drill");
    b.addEventListener("click", drillDialog);
    row.appendChild(b);
  }
  wrap.appendChild(row);

  if (!A.meta.environment.live_fhir) {
    const hint = el("div", "small dim");
    hint.style.cssText = "margin-top:16px;max-width:460px;margin-inline:auto;line-height:1.6";
    hint.innerHTML = can("confirm_seed")
      ? `This sandbox has no real contamination yet. <b>Run a drill</b> to plant a genuine
         poisoning event — it uses the real agent write path and must be found by the real
         recovery loop — then report it and work it through.`
      : `This sandbox has no real contamination yet. A safety officer can plant a training
         drill from their dashboard.`;
    wrap.appendChild(hint);
  }
  return wrap;
}

/* ================================================================== */
/* WORKLIST                                                            */
/* ================================================================== */
function incidentTable(rows) {
  const wrap = el("div", "tablewrap");
  const t = el("table");
  t.innerHTML = `<thead><tr>
    <th>Incident</th><th>Severity</th><th>Status</th><th>Patient</th>
    <th>Reported</th><th>Assigned</th></tr></thead>`;
  const tb = el("tbody");
  rows.forEach((r) => {
    const tr = el("tr", "clickable");
    tr.innerHTML = `
      <td><b>${esc(r.incident_id)}</b><div class="small dim">${esc(r.title)}</div></td>
      <td>${sevPill(r.severity)}</td>
      <td>${statusPill(r.status)}
        ${r.quarantined ? `<div class="small dim">${r.quarantined} awaiting review</div>` : ""}</td>
      <td>${r.patient_display ? esc(r.patient_display) : '<span class="dim">—</span>'}
        <div class="small dim mono">${esc(r.patient_id || "")}</div></td>
      <td class="nowrap">${esc(ago(r.reported_at))}<div class="small dim">${esc(r.reported_by)}</div></td>
      <td>${r.assigned_to ? esc(r.assigned_to) : '<span class="dim">unassigned</span>'}</td>`;
    tr.addEventListener("click", () => go("incident", { id: r.incident_id }));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  wrap.appendChild(t);
  return wrap;
}

async function pageWorklist() {
  const openOnly = A.params.openOnly !== false;
  const d = await api(`/api/ops/incidents?open_only=${openOnly}`);
  const c = $("content"); c.innerHTML = "";

  const toggle = el("button", "btn sec sm", openOnly ? "Show all incidents" : "Show open only");
  toggle.addEventListener("click", () => go("worklist", { openOnly: !openOnly }));
  $("topbar-actions").appendChild(toggle);

  const card = el("div", "card");
  card.innerHTML = `<header><h3>${openOnly ? "Open" : "All"} incidents</h3>
    <div class="grow"></div><span class="small dim">${d.incidents.length} shown</span></header>`;
  const body = el("div", "body tight");
  if (!d.incidents.length) {
    body.appendChild(emptyWorklistGuide());
  } else {
    body.appendChild(incidentTable(d.incidents));
  }
  card.appendChild(body);
  c.appendChild(card);
}

/* ================================================================== */
/* REPORT                                                              */
/* ================================================================== */
async function pageReport() {
  const c = $("content"); c.innerHTML = "";
  const card = el("div", "card");
  card.style.maxWidth = "760px";
  card.innerHTML = `<header><h3>Report a memory safety issue</h3></header>`;
  const body = el("div", "body");

  body.appendChild(el("div", "banner info mb",
    `<div class="ic">i</div><div>Use this when the assistant referred to the wrong person,
     showed content from another chart, revealed restricted information, or kept repeating
     something that has since been corrected. You do not need to know which memory is at
     fault — describe what you saw.</div>`));

  const kinds = A.meta.issue_kinds.map((k) =>
    `<option value="${esc(k.kind)}">${esc(k.label)}</option>`).join("");
  body.appendChild(el("div", "field", `
    <label for="r-kind">What did you observe?</label>
    <select id="r-kind">${kinds}</select>
    <div class="help" id="r-kind-help"></div>`));

  body.appendChild(el("div", "field", `
    <label for="r-title">Short title</label>
    <input id="r-title" placeholder="e.g. Handover lists another patient's vitals">`));

  body.appendChild(el("div", "field", `
    <label for="r-patient">Patient (optional but strongly recommended)</label>
    <div class="searchbox"><input id="r-patient-q" placeholder="Search by name or MRN"></div>
    <input type="hidden" id="r-patient">
    <div class="help" id="r-patient-sel">No patient selected.</div>`));

  body.appendChild(el("div", "field", `
    <label for="r-where">Where did you see it?</label>
    <input id="r-where" placeholder="e.g. Ward 4B evening handover, 19:40">`));

  body.appendChild(el("div", "field", `
    <label for="r-desc">What happened?</label>
    <textarea id="r-desc" placeholder="Describe what the assistant showed and why it looked wrong."></textarea>`));

  const submit = el("button", "btn", "Submit report");
  body.appendChild(submit);
  card.appendChild(body);
  c.appendChild(card);

  const kindHelp = () => {
    const k = A.meta.issue_kinds.find((x) => x.kind === $("r-kind").value);
    $("r-kind-help").textContent = k ? `${k.help} Default severity: ${k.default_severity}.` : "";
  };
  $("r-kind").addEventListener("change", kindHelp); kindHelp();
  wirePatientSearch("r-patient-q", (p) => {
    $("r-patient").value = p.patient_id;
    $("r-patient-sel").innerHTML =
      `Selected: <b>${esc(p.display)}</b> (MRN ${esc(p.mrn)})`;
  });

  submit.addEventListener("click", async () => {
    submit.disabled = true; submit.innerHTML = '<span class="spin"></span>Submitting…';
    try {
      const inc = await api("/api/ops/incidents", {
        method: "POST",
        body: {
          title: $("r-title").value, issue_kind: $("r-kind").value,
          description: $("r-desc").value, patient_id: $("r-patient").value || null,
          observed_in: $("r-where").value,
        },
      });
      toast(`Incident ${inc.incident_id} raised.`, "ok");
      await refreshCounts();
      go("incident", { id: inc.incident_id });
    } catch (e) {
      toast(e.message, "err");
      submit.disabled = false; submit.textContent = "Submit report";
    }
  });
}

function wirePatientSearch(inputId, onPick) {
  const input = $(inputId);
  let box = null;
  const close = () => { if (box) { box.remove(); box = null; } };
  input.addEventListener("input", async () => {
    close();
    const q = input.value.trim();
    if (q.length < 2) return;
    try {
      const d = await api(`/api/ops/patients/search?q=${encodeURIComponent(q)}`);
      if (!d.results.length) return;
      box = el("div", "results");
      d.results.forEach((p) => {
        const row = el("div", null,
          `<b>${esc(p.display)}</b> <span class="dim mono">${esc(p.mrn)}</span>`);
        row.addEventListener("click", () => { onPick(p); input.value = p.display; close(); });
        box.appendChild(row);
      });
      input.parentElement.appendChild(box);
    } catch (e) { /* ignore */ }
  });
  document.addEventListener("click", (e) => {
    if (box && !input.parentElement.contains(e.target)) close();
  });
}

/* ================================================================== */
/* INCIDENT WORKSPACE                                                  */
/* ================================================================== */
const STEP_ORDER = ["reported", "triaged", "confirmed", "recovering",
                    "review_required", "recovered", "closed"];

async function pageIncident({ id }) {
  const inc = await api(`/api/ops/incidents/${encodeURIComponent(id)}`);
  const c = $("content"); c.innerHTML = "";
  $("page-title").textContent = inc.incident_id;
  $("page-crumb").textContent = inc.title;

  if (can("export_evidence")) {
    const b = el("button", "btn sec sm", "Export evidence");
    b.addEventListener("click", () => exportEvidence(inc.incident_id));
    $("topbar-actions").appendChild(b);
  }

  // --- progress steps -------------------------------------------------
  const steps = el("div", "steps");
  const currentIdx = STEP_ORDER.indexOf(inc.status);
  STEP_ORDER.forEach((s, i) => {
    const cls = inc.status === "dismissed" ? "" :
      i < currentIdx ? "done" : i === currentIdx ? "current" : "";
    steps.appendChild(el("div", `step ${cls}`,
      `<div class="n">Step ${i + 1}</div><div class="t">${esc(s.replace(/_/g, " "))}</div>`));
  });
  c.appendChild(steps);

  if (inc.severity === "critical" && inc.is_open) {
    c.appendChild(el("div", "banner crit mb",
      `<div class="ic">!</div><div><b>Critical severity.</b> ${esc(inc.issue_help)}</div>`));
  }

  const grid = el("div", "grid");
  grid.style.gridTemplateColumns = "minmax(0,2fr) minmax(300px,1fr)";
  if (window.innerWidth < 1100) grid.style.gridTemplateColumns = "1fr";

  // ---------------- left column ----------------
  const left = el("div");

  const detail = el("div", "card mb");
  detail.innerHTML = `<header><h3>Report</h3><div class="grow"></div>
    ${sevPill(inc.severity)} ${statusPill(inc.status)}</header>`;
  const db = el("div", "body");
  db.innerHTML = `
    <div class="grid g2">
      <div><div class="small dim">Patient</div>
        <div>${inc.patient_display ? esc(inc.patient_display) : "—"}
          <span class="mono dim">${esc(inc.patient_id || "")}</span></div></div>
      <div><div class="small dim">Observed in</div><div>${esc(inc.observed_in || "—")}</div></div>
      <div><div class="small dim">Reported by</div>
        <div>${esc(inc.reported_by)} · ${esc(inc.unit || "—")}</div></div>
      <div><div class="small dim">Reported</div><div>${esc(fmtTime(inc.reported_at))}</div></div>
    </div>
    <div class="mt"><div class="small dim">Description</div>
      <div>${esc(inc.description)}</div></div>`;
  if (inc.patient_id) {
    const link = el("button", "btn sec sm mt", "View this patient's memory");
    link.addEventListener("click", () => go("patient", { id: inc.patient_id }));
    db.appendChild(link);
  }
  detail.appendChild(db);
  left.appendChild(detail);

  // ---- action panel: what to do next --------------------------------
  left.appendChild(await actionPanel(inc));

  // ---- recovery outcome ---------------------------------------------
  if (inc.recovery_summary && Object.keys(inc.recovery_summary).length) {
    const spreadBtn = el("button", "btn sec sm", "View contamination spread →");
    spreadBtn.addEventListener("click", () => go("spread", { id: inc.incident_id }));
    $("topbar-actions").prepend(spreadBtn);
    left.appendChild(recoveryCard(inc));
  }

  // ---- certificate ---------------------------------------------------
  if (inc.certificate_text) {
    const cert = el("div", "card mb");
    cert.innerHTML = `<header><h3>Recovery certificate</h3><div class="grow"></div>
      ${inc.certificate?.safe_resume
        ? '<span class="pill sev-low" style="background:var(--ok-soft);color:var(--ok);border-color:#b9e2cc">safe resume approved</span>'
        : '<span class="pill sev-moderate">review required</span>'}</header>`;
    const cb = el("div", "body");
    cb.appendChild(el("pre", "cert", esc(inc.certificate_text)));
    cert.appendChild(cb);
    left.appendChild(cert);
  }

  grid.appendChild(left);

  // ---------------- right column: timeline ----------------
  const right = el("div");
  const tl = el("div", "card");
  tl.innerHTML = `<header><h3>Activity</h3></header>`;
  const tb = el("div", "body");
  const timeline = el("div", "timeline");
  (inc.notes || []).slice().reverse().forEach((n) => {
    timeline.appendChild(el("div", `tl-item ${n.kind}`,
      `<div class="tl-head"><b>${esc(n.author)}</b> · ${esc(n.author_role.replace(/_/g, " "))}
        · ${esc(ago(n.at))}</div>
       <div class="tl-body">${esc(n.body)}</div>`));
  });
  tb.appendChild(timeline);

  if (inc.is_open) {
    const noteBox = el("div", "field mt", `<textarea id="i-note" placeholder="Add a note…"></textarea>`);
    tb.appendChild(noteBox);
    const nb = el("button", "btn sec sm", "Add note");
    nb.addEventListener("click", async () => {
      const body = $("i-note").value.trim();
      if (!body) return;
      try { await api(`/api/ops/incidents/${inc.incident_id}/notes`, { method: "POST", body: { body } });
        go("incident", { id: inc.incident_id });
      } catch (e) { toast(e.message, "err"); }
    });
    tb.appendChild(nb);
  }
  tl.appendChild(tb);
  right.appendChild(tl);
  grid.appendChild(right);

  c.appendChild(grid);
}

async function actionPanel(inc) {
  const card = el("div", "card mb");
  card.innerHTML = `<header><h3>Next step</h3></header>`;
  const b = el("div", "body");

  if (!inc.is_open) {
    b.appendChild(el("div", "banner ok",
      `<div class="ic">✓</div><div><b>${esc(inc.status)}.</b>
       ${esc(inc.resolution || "")}<div class="small dim mt">Closed by
       ${esc(inc.closed_by || "—")} · ${esc(fmtTime(inc.closed_at))}</div></div>`));
    card.appendChild(b); return card;
  }

  // --- triage ---------------------------------------------------------
  if (inc.status === "reported" && can("triage_incident")) {
    b.appendChild(el("p", "muted", "Assess the severity and take ownership."));
    const row = el("div", "row");
    const sev = el("select");
    A.meta.severities.forEach((s) => sev.appendChild(new Option(s, s)));
    sev.value = inc.severity;
    sev.style.cssText = "padding:8px 11px;border:1px solid var(--border-strong);border-radius:8px";
    const go1 = el("button", "btn", "Triage and assign to me");
    go1.addEventListener("click", async () => {
      try {
        await api(`/api/ops/incidents/${inc.incident_id}/triage`, {
          method: "POST", body: { severity: sev.value, assign_to: A.me.username } });
        toast("Incident triaged.", "ok");
        go("incident", { id: inc.incident_id });
      } catch (e) { toast(e.message, "err"); }
    });
    row.appendChild(sev); row.appendChild(go1);
    b.appendChild(row);
  }

  // --- confirm the seed ------------------------------------------------
  if (["reported", "triaged"].includes(inc.status) && can("confirm_seed")) {
    b.appendChild(el("div", "banner info mt",
      `<div class="ic">i</div><div>Recovery will not run on a suspicion. Identify the memory
       version that is actually wrong, and confirm it. Everything derived from it is then
       found automatically.</div>`));
    const pick = el("button", "btn mt", "Find the compromised memory");
    pick.addEventListener("click", () => confirmSeedDialog(inc));
    b.appendChild(pick);
  }

  // --- run recovery ----------------------------------------------------
  if (inc.status === "confirmed" && can("run_recovery")) {
    b.appendChild(el("div", "banner warn",
      `<div class="ic">!</div><div><b>${inc.seed_keys.length} compromised memory version(s)
       confirmed.</b> Running recovery will withdraw them from service, rebuild every
       affected downstream memory from the trusted record, and block reintroduction.
       Unrelated memory is left untouched.</div>`));
    const run = el("button", "btn mt", "Run recovery");
    run.addEventListener("click", async () => {
      run.disabled = true; run.innerHTML = '<span class="spin"></span>Recovering…';
      try {
        await api(`/api/ops/incidents/${inc.incident_id}/recover`, { method: "POST", body: {} });
        toast("Recovery complete.", "ok");
        await refreshCounts();
        go("incident", { id: inc.incident_id });
      } catch (e) {
        toast(e.message, "err");
        run.disabled = false; run.textContent = "Run recovery";
      }
    });
    b.appendChild(run);
  }

  // --- awaiting review --------------------------------------------------
  if (inc.status === "review_required") {
    b.appendChild(el("div", "banner warn",
      `<div class="ic">!</div><div><b>${inc.quarantined_keys.length} artifact(s) could not be
       rebuilt safely</b> and are being held rather than guessed. A reviewer must decide on
       each before this incident can close.</div>`));
    if (can("review_quarantine")) {
      const rq = el("button", "btn mt", "Open review queue");
      rq.addEventListener("click", () => go("review"));
      b.appendChild(rq);
    }
  }

  // --- close ------------------------------------------------------------
  if (["recovered", "review_required"].includes(inc.status) && can("close_incident")) {
    const close = el("button",
      `btn ${inc.status === "recovered" ? "" : "sec"} mt`, "Close incident");
    close.addEventListener("click", () => closeDialog(inc, false));
    b.appendChild(close);
  }
  if (["reported", "triaged", "confirmed"].includes(inc.status) && can("close_incident")) {
    const dismiss = el("button", "btn sec mt", "Dismiss — no contamination found");
    dismiss.style.marginLeft = "8px";
    dismiss.addEventListener("click", () => closeDialog(inc, true));
    b.appendChild(dismiss);
  }

  if (!b.children.length) {
    b.appendChild(el("p", "muted",
      "Waiting on another role. You do not have an action on this incident right now."));
  }
  card.appendChild(b);
  return card;
}

function recoveryCard(inc) {
  const r = inc.recovery_summary;
  const card = el("div", "card mb");
  card.innerHTML = `<header><h3>Recovery outcome</h3><div class="grow"></div>
    <span class="small dim">${r.rounds} round(s) · closure
    ${r.closure_reached ? "reached" : "NOT reached"}</span></header>`;
  const b = el("div", "body");

  const tiles = el("div", "grid g4");
  [
    ["Rebuilt", r.repaired, "from trusted record", r.repaired ? "okv" : ""],
    ["Held for review", r.quarantined, "not guessed", r.quarantined ? "warnv" : ""],
    ["Left untouched", r.cleared, "verified unaffected", "info"],
    ["Tombstoned", r.tombstones, "cannot return", ""],
  ].forEach(([k, v, s, kind]) => tiles.appendChild(el("div", `stat ${kind}`,
    `<div class="k">${esc(k)}</div><div class="v">${v ?? 0}</div><div class="s">${esc(s)}</div>`)));
  b.appendChild(tiles);

  const res = r.resurrection || {};
  if (res.attempts) {
    b.appendChild(el("div", `banner ${res.blocked === res.attempts ? "ok" : "crit"} mt`,
      `<div class="ic">${res.blocked === res.attempts ? "✓" : "!"}</div>
       <div><b>Reintroduction blocked ${res.blocked}/${res.attempts}.</b>
       The system attempted to re-inject the withdrawn memory, including a paraphrased
       version, and was refused.</div>`));
  }

  if ((r.repaired_detail || []).length) {
    const list = el("div", "mt");
    list.appendChild(el("div", "small dim mb", "Rebuilt memory versions"));
    r.repaired_detail.forEach((x) => list.appendChild(el("div", "mem r",
      `<div class="mh"><span class="ty mono">${esc(x.memory_key)}</span>
        <span class="dim">→</span><span class="mono">${esc(x.new_key)}</span></div>
       <div class="small dim">${esc(x.reason)}</div>`)));
    b.appendChild(list);
  }
  if ((r.quarantined_detail || []).length) {
    const list = el("div", "mt");
    list.appendChild(el("div", "small dim mb", "Held for review"));
    r.quarantined_detail.forEach((x) => list.appendChild(el("div", "mem q",
      `<div class="mh"><span class="ty mono">${esc(x.memory_key)}</span></div>
       <div class="reason">${esc(x.reason)}</div>`)));
    b.appendChild(list);
  }
  card.appendChild(b);
  return card;
}

/* ---------------- dialogs ---------------- */
function openDialog(title, sub, bodyNode, footNodes) {
  $("dlg-title").textContent = title;
  $("dlg-sub").textContent = sub || "";
  $("dlg-body").innerHTML = ""; $("dlg-body").appendChild(bodyNode);
  $("dlg-foot").innerHTML = ""; footNodes.forEach((n) => $("dlg-foot").appendChild(n));
  $("dlg").showModal();
}
const closeDlg = () => $("dlg").close();

async function confirmSeedDialog(inc) {
  const body = el("div");
  body.appendChild(el("div", "banner info mb",
    `<div class="ic">i</div><div>Memories that <b>name this patient but are filed under
     someone else</b> are listed first — a mis-association looks exactly like that, and
     filtering by patient alone would hide it. Select the entry that is wrong.</div>`));
  const list = el("div");
  body.appendChild(list);
  list.innerHTML = '<div class="empty"><span class="spin dark"></span> Loading…</div>';

  const selected = new Set();
  const confirm = el("button", "btn", "Confirm as compromised");
  confirm.disabled = true;
  const cancel = el("button", "btn sec", "Cancel");
  cancel.addEventListener("click", closeDlg);
  openDialog("Identify the compromised memory",
    `Incident ${inc.incident_id}`, body, [cancel, confirm]);

  try {
    const d = await api(`/api/ops/incidents/${inc.incident_id}/candidates`);
    list.innerHTML = "";
    if (!d.candidates.length) {
      list.appendChild(el("div", "empty",
        '<div class="big">◍</div><div class="et">No memory in service</div>'));
      return;
    }
    d.candidates.forEach((cand) => {
      const item = el("div", "pick");
      const flag = cand.match === "mentions"
        ? '<span class="pill sev-critical">possible mis-association</span>'
        : cand.match === "scoped"
          ? '<span class="pill sev-low">this patient</span>' : "";
      item.innerHTML = `
        <div class="ph">
          <span>${esc(cand.artifact_type.replace(/_/g, " "))}</span>
          <span class="dim small">${esc(cand.owner.replace(/_/g, " "))}</span>
          ${flag}
          ${cand.restricted ? '<span class="pill sev-critical">restricted</span>' : ""}
        </div>
        <div class="small dim" style="margin:4px 0">${esc(cand.why || "")}</div>
        <div class="pp">${esc(cand.preview)}</div>
        <div class="small dim mono" style="margin-top:5px">${esc(cand.memory_key)}</div>`;
      item.addEventListener("click", () => {
        if (selected.has(cand.memory_key)) { selected.delete(cand.memory_key); item.classList.remove("on"); }
        else { selected.add(cand.memory_key); item.classList.add("on"); }
        confirm.disabled = selected.size === 0;
      });
      list.appendChild(item);
    });
  } catch (e) {
    list.innerHTML = `<div class="banner crit">${esc(e.message)}</div>`;
  }

  confirm.addEventListener("click", async () => {
    confirm.disabled = true; confirm.innerHTML = '<span class="spin"></span>Confirming…';
    try {
      await api(`/api/ops/incidents/${inc.incident_id}/confirm`, {
        method: "POST", body: { memory_keys: [...selected] } });
      closeDlg(); toast("Compromised memory confirmed.", "ok");
      go("incident", { id: inc.incident_id });
    } catch (e) {
      toast(e.message, "err");
      confirm.disabled = false; confirm.textContent = "Confirm as compromised";
    }
  });
}

function closeDialog(inc, dismiss) {
  const body = el("div");
  body.appendChild(el("div", "field", `
    <label>Resolution summary</label>
    <textarea id="close-res" placeholder="${dismiss
      ? "Why was no contamination found?"
      : "What was done, and what did you verify?"}"></textarea>`));
  if (!dismiss && inc.quarantined_keys.length) {
    body.appendChild(el("div", "banner warn",
      `<div class="ic">!</div><div>${inc.quarantined_keys.length} artifact(s) still await
       review. The incident cannot close until they are resolved.</div>`));
  }
  const cancel = el("button", "btn sec", "Cancel");
  cancel.addEventListener("click", closeDlg);
  const ok = el("button", `btn ${dismiss ? "sec" : ""}`, dismiss ? "Dismiss incident" : "Close incident");
  ok.addEventListener("click", async () => {
    ok.disabled = true;
    try {
      await api(`/api/ops/incidents/${inc.incident_id}/close`, {
        method: "POST", body: { resolution: $("close-res").value, dismiss } });
      closeDlg(); toast(dismiss ? "Incident dismissed." : "Incident closed.", "ok");
      await refreshCounts();
      go("incident", { id: inc.incident_id });
    } catch (e) { toast(e.message, "err"); ok.disabled = false; }
  });
  openDialog(dismiss ? "Dismiss incident" : "Close incident",
    inc.incident_id, body, [cancel, ok]);
}

async function exportEvidence(incidentId) {
  try {
    const pack = await api(`/api/ops/incidents/${incidentId}/evidence`);
    const blob = new Blob([JSON.stringify(pack, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${incidentId}-evidence.json`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("Evidence pack downloaded.", "ok");
  } catch (e) { toast(e.message, "err"); }
}

function drillDialog() {
  const body = el("div");
  body.appendChild(el("div", "banner warn mb",
    `<div class="ic">!</div><div>A drill plants a genuine contamination event in the sandbox
     so the workflow can be practised. It uses the real agent write path and must be found
     by the real recovery loop. Disabled when connected to a live EHR.</div>`));
  const kinds = A.meta.issue_kinds.map((k) =>
    `<option value="${esc(k.kind)}">${esc(k.label)}</option>`).join("");
  body.appendChild(el("div", "field", `<label>Contamination type</label>
    <select id="drill-kind">${kinds}</select>`));
  const cancel = el("button", "btn sec", "Cancel");
  cancel.addEventListener("click", closeDlg);
  const run = el("button", "btn", "Run drill");
  run.addEventListener("click", async () => {
    run.disabled = true; run.innerHTML = '<span class="spin"></span>Running…';
    try {
      const d = await api("/api/ops/drill", {
        method: "POST", body: { issue_kind: $("drill-kind").value } });
      closeDlg();
      toast(`Drill planted: ${d.family_name} affecting ${d.patient_display} ` +
            `(${d.affected_count} downstream memories).`, "ok");
      go("dashboard");
    } catch (e) { toast(e.message, "err"); run.disabled = false; run.textContent = "Run drill"; }
  });
  openDialog("Run a training drill", "Sandbox only", body, [cancel, run]);
}

/* ================================================================== */
/* REVIEW QUEUE                                                        */
/* ================================================================== */
async function pageReview() {
  const d = await api("/api/ops/review");
  A.counts.review = d.items.length; renderNav();
  const c = $("content"); c.innerHTML = "";

  c.appendChild(el("div", "banner info mb",
    `<div class="ic">i</div><div>These memories were affected by an incident but could not be
     rebuilt safely from the trusted record, so the system held them rather than guessing.
     Approve only if the content is correct as it stands.</div>`));

  if (!d.items.length) {
    c.appendChild(el("div", "card"));
    c.lastChild.appendChild(el("div", "empty",
      '<div class="big">✓</div><div>Nothing awaiting review.</div>'));
    return;
  }

  d.items.forEach((item) => {
    const card = el("div", "card mb");
    card.innerHTML = `<header>
      <h3>${esc(item.artifact_type.replace(/_/g, " "))}</h3>
      <span class="dim small">${esc(item.owner.replace(/_/g, " "))}</span>
      <div class="grow"></div>
      ${item.incident_id ? `<span class="small dim">${esc(item.incident_id)}</span>` : ""}
    </header>`;
    const b = el("div", "body");
    b.innerHTML = `
      <div class="small dim">Patient</div>
      <div class="mb">${esc(item.patient_display || "—")}
        <span class="mono dim">${esc(item.patient_id || "")}</span></div>
      <div class="banner warn mb"><div class="ic">!</div>
        <div><b>Why it was held:</b> ${esc(item.reason)}</div></div>
      <div class="small dim mb">Content</div>`;
    b.appendChild(el("pre", "cert", esc(item.content)));
    const note = el("div", "field mt",
      `<label>Reviewer note</label><input class="rv-note" placeholder="Optional">`);
    b.appendChild(note);
    const row = el("div", "row mt");
    [["approve", "Approve — return to service", "btn ok"],
     ["reject", "Reject — permanently withdraw", "btn danger"],
     ["hold", "Keep in quarantine", "btn sec"]].forEach(([decision, label, cls]) => {
      const btn = el("button", cls, label);
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          const out = await api("/api/ops/review", {
            method: "POST",
            body: { memory_key: item.memory_key, decision,
                    note: note.querySelector(".rv-note").value },
          });
          toast(`${item.artifact_type}: ${out.outcome}.`, "ok");
          await refreshCounts();
          go("review");
        } catch (e) { toast(e.message, "err"); btn.disabled = false; }
      });
      row.appendChild(btn);
    });
    b.appendChild(row);
    card.appendChild(b);
    c.appendChild(card);
  });
}

/* ================================================================== */
/* PATIENTS                                                            */
/* ================================================================== */
async function pagePatients() {
  const c = $("content"); c.innerHTML = "";
  const card = el("div", "card");
  card.style.maxWidth = "640px";
  card.innerHTML = `<header><h3>Look up a patient</h3></header>`;
  const b = el("div", "body");
  b.appendChild(el("p", "muted",
    "See everything the assistants currently remember about one patient, per role."));
  b.appendChild(el("div", "field",
    `<div class="searchbox"><input id="p-q" placeholder="Search by name or MRN"></div>`));
  card.appendChild(b);
  c.appendChild(card);
  wirePatientSearch("p-q", (p) => go("patient", { id: p.patient_id }));
}

async function pagePatient({ id }) {
  const d = await api(`/api/ops/patients/${encodeURIComponent(id)}`);
  const c = $("content"); c.innerHTML = "";
  $("page-title").textContent = d.patient.display;
  $("page-crumb").textContent = `MRN ${d.patient.mrn}`;

  const tiles = el("div", "grid g4 mb");
  [
    ["Memory versions", d.totals.artifacts, "across all roles", ""],
    ["In service", d.totals.servable, "currently retrievable", "info"],
    ["Quarantined", d.totals.quarantined, "held for review",
      d.totals.quarantined ? "warnv" : ""],
    ["Incidents", d.incidents.length, "raised for this patient",
      d.incidents.some((i) => i.is_open) ? "crit" : ""],
  ].forEach(([k, v, s, kind]) => tiles.appendChild(el("div", `stat ${kind}`,
    `<div class="k">${esc(k)}</div><div class="v">${v}</div><div class="s">${esc(s)}</div>`)));
  c.appendChild(tiles);

  if (d.incidents.length) {
    const card = el("div", "card mb");
    card.innerHTML = "<header><h3>Incidents for this patient</h3></header>";
    const b = el("div", "body tight");
    b.appendChild(incidentTable(d.incidents));
    card.appendChild(b);
    c.appendChild(card);
  }

  Object.entries(d.memory_by_role).forEach(([role, entries]) => {
    const card = el("div", "card mb");
    card.innerHTML = `<header><h3>${esc(role.replace(/_/g, " "))}</h3>
      <div class="grow"></div><span class="small dim">${entries.length} version(s)</span></header>`;
    const b = el("div", "body");
    if (!entries.length) {
      b.appendChild(el("div", "dim small", "No memory held for this patient."));
    } else {
      entries.forEach((e) => {
        const cls = e.state === "quarantined" ? "q" : e.state === "repaired" ? "r"
          : (!e.servable ? "t" : "");
        const mem = el("div", `mem ${cls}`);
        mem.innerHTML = `
          <div class="mh">
            <span class="ty">${esc(e.artifact_type.replace(/_/g, " "))}</span>
            <span class="dim small">v${e.version}</span>
            ${msChip(e.state)}
            ${e.restricted ? '<span class="pill sev-critical">restricted content</span>' : ""}
            <span class="grow"></span>
            <span class="dim small">${esc(fmtTime(e.created_at))}</span>
          </div>`;
        mem.appendChild(el("pre", null, esc(e.content)));
        if (e.quarantine_reason) {
          mem.appendChild(el("div", "reason", esc(e.quarantine_reason)));
        }
        b.appendChild(mem);
      });
    }
    card.appendChild(b);
    c.appendChild(card);
  });
}

/* ================================================================== */
/* AUDIT                                                               */
/* ================================================================== */
async function pageAudit() {
  const d = await api("/api/ops/audit?limit=300");
  const c = $("content"); c.innerHTML = "";
  const card = el("div", "card");
  card.innerHTML = `<header><h3>Operational audit trail</h3><div class="grow"></div>
    <span class="small dim">${d.entries.length} most recent</span></header>`;
  const b = el("div", "body tight");
  const wrap = el("div", "tablewrap");
  const t = el("table");
  t.innerHTML = `<thead><tr><th>When</th><th>Operator</th><th>Role</th>
    <th>Action</th><th>Subject</th></tr></thead>`;
  const tb = el("tbody");
  d.entries.forEach((e) => tb.appendChild(el("tr", null, `
    <td class="nowrap">${esc(fmtTime(e.at))}</td>
    <td>${esc(e.actor)}</td>
    <td class="dim">${esc(String(e.actor_role).replace(/_/g, " "))}</td>
    <td><b>${esc(String(e.action).replace(/_/g, " "))}</b></td>
    <td class="mono dim">${esc(e.subject || "—")}</td>`)));
  t.appendChild(tb); wrap.appendChild(t); b.appendChild(wrap);
  card.appendChild(b); c.appendChild(card);
}

/* ================================================================== */
/* BOOT                                                                */
/* ================================================================== */
(async function boot() {
  try {
    await loadMeta();
    if (A.token) {
      try {
        const who = await api("/api/ops/session");
        A.me = who.operator;
        await enterApp();
        return;
      } catch (e) { signOutLocal(); }
    }
  } catch (e) {
    $("signin-error").innerHTML =
      `<div class="banner crit mb"><div class="ic">!</div>
       <div>Cannot reach the server. ${esc(e.message)}</div></div>`;
  }
})();
