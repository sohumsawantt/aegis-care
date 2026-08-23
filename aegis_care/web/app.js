/* AEGIS-Care dashboard.
 * Vanilla JS, no external dependencies: the whole prototype must run offline.
 */
"use strict";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 3) => (typeof v === "number" ? v.toFixed(d) : (v ?? "—"));

const STATE = { system: null, incidents: [], selected: null, lastRecovery: null };

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

/* ---------------- tabs ---------------- */
$("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  document.querySelectorAll("nav.tabs button").forEach((b) => b.classList.remove("active"));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  btn.classList.add("active");
  $("view-" + btn.dataset.view).classList.add("active");
  if (btn.dataset.view === "graph") drawGraph();
  if (btn.dataset.view === "audit") loadAudit();
  if (btn.dataset.view === "review") loadReview();
});

/* ---------------- table helper ---------------- */
function table(rows, columns, opts = {}) {
  if (!rows || !rows.length) return el("p", "muted small", "No data.");
  const wrap = el("div", "table-wrap");
  const t = el("table");
  const thead = el("thead");
  const tr = el("tr");
  columns.forEach((c) => tr.appendChild(el("th", null, esc(c.label ?? c.key))));
  thead.appendChild(tr);
  t.appendChild(thead);
  const tbody = el("tbody");
  rows.forEach((row) => {
    const r = el("tr");
    if (opts.highlight && opts.highlight(row)) r.classList.add("highlight");
    columns.forEach((c) => {
      const td = el("td", c.num ? "num" : (c.wrap ? "wrap" : null));
      td.innerHTML = c.render ? c.render(row) : esc(row[c.key] ?? "");
      r.appendChild(td);
    });
    tbody.appendChild(r);
  });
  t.appendChild(tbody);
  wrap.appendChild(t);
  return wrap;
}

function bar(value, kind = "") {
  const pct = Math.max(0, Math.min(1, Number(value) || 0)) * 100;
  return `<span class="bar ${kind}"><span style="width:${pct}%"></span></span>
          <span class="mono"> ${fmt(value)}</span>`;
}

function stateBadge(s) { return `<span class="badge ${esc(s)}">${esc(s)}</span>`; }

/* ---------------- boot ---------------- */
async function boot() {
  const health = await api("/api/health");
  $("version").textContent = `v${health.version} · ${health.model}`;
  STATE.system = await api("/api/system");
  renderOverview();
  populateSelectors();
  await refreshIncidents();
}

function renderOverview() {
  const s = STATE.system;
  const stats = $("overview-stats");
  stats.innerHTML = "";
  const cards = [
    ["Patients", s.stats.fhir.Patient, "synthetic FHIR R4 records", "accent"],
    ["Observations", s.stats.fhir.Observation, "vitals, labs, restricted screens", ""],
    ["Benchmark tasks", s.tasks.length, "identity · labs · documentation", ""],
    ["Memory artifacts", s.stats.memory
      ? Object.values(s.stats.memory).reduce((a, m) => a + (m.total || 0), 0) : 0,
      "versioned across three roles", ""],
    ["Agent roles", 3, "registration · nursing · summary", ""],
    ["Sketch size", `${s.sketch.dim}×${s.sketch.bits}b`,
      `${s.sketch.bytes} bytes per capsule`, ""],
    ["Ground-truth edges", s.stats.truth.edges, "private instrumentation only", ""],
    ["Contaminated nodes", s.stats.truth.contaminated, "labelled for scoring", "bad"],
  ];
  cards.forEach(([label, value, sub, kind]) => {
    const c = el("div", `stat ${kind}`);
    c.appendChild(el("div", "label", esc(label)));
    c.appendChild(el("div", "value", esc(value)));
    c.appendChild(el("div", "sub", esc(sub)));
    stats.appendChild(c);
  });

  $("roles-table").innerHTML = "";
  $("roles-table").appendChild(table(
    Object.entries(s.roles).map(([role, v]) => ({
      role, fields: v.fields.join(", ") || "none (no clinical read rights)",
      total: (v.memory && v.memory.total) || 0,
    })),
    [
      { key: "role", label: "Role", render: (r) => `<span class="badge role">${esc(r.role)}</span>` },
      { key: "fields", label: "Authorized fields", wrap: true },
      { key: "total", label: "Memories", num: true },
    ]));

  $("families-table").innerHTML = "";
  $("families-table").appendChild(table(
    Object.entries(s.families).map(([id, f]) => ({ id, ...f })),
    [
      { key: "id", label: "ID" },
      { key: "name", label: "Family" },
      { key: "propagation", label: "Propagation", wrap: true },
      { key: "failure", label: "Observable failure", wrap: true },
    ]));
}

function populateSelectors() {
  const s = STATE.system;
  const fam = $("in-family");
  fam.innerHTML = "";
  Object.entries(s.families).forEach(([id, f]) => {
    fam.appendChild(new Option(`${id} — ${f.name}`, id));
  });
  const task = $("in-task");
  task.innerHTML = "";
  s.tasks.forEach((t) => task.appendChild(
    new Option(`${t.task_id} · ${t.label} (${t.patient_id})`, t.task_id)));
  const prov = $("in-prov");
  prov.innerHTML = "";
  s.provenance_conditions.forEach((p) => prov.appendChild(new Option(p, p)));
  prov.value = "targeted";

  const exFam = $("ex-families");
  exFam.innerHTML = "";
  Object.keys(s.families).forEach((id) => {
    const o = new Option(id, id); o.selected = true; exFam.appendChild(o);
  });
  const exProv = $("ex-prov");
  exProv.innerHTML = "";
  s.provenance_conditions.forEach((p) => {
    const o = new Option(p, p);
    o.selected = ["complete", "random40", "targeted"].includes(p);
    exProv.appendChild(o);
  });
}

/* ---------------- incidents ---------------- */
async function refreshIncidents() {
  const data = await api("/api/incidents");
  STATE.incidents = data.incidents;
  ["care-incident", "bl-incident", "pv-incident"].forEach((id) => {
    const sel = $(id);
    const prev = sel.value;
    sel.innerHTML = "";
    STATE.incidents.forEach((i) => sel.appendChild(
      new Option(`${i.incident_id} (${i.family}, d${i.depth}, ${i.provenance?.condition ?? "?"})`,
        i.incident_id)));
    if (prev) sel.value = prev;
  });

  $("incident-list").innerHTML = "";
  $("incident-list").appendChild(table(STATE.incidents, [
    { key: "incident_id", label: "Incident" },
    { key: "family", label: "Family" },
    { key: "depth", label: "Depth", num: true },
    { key: "prov", label: "Provenance", render: (r) => esc(r.provenance?.condition ?? "—") },
    {
      key: "loss", label: "Edges masked", num: true,
      render: (r) => r.provenance
        ? `${r.provenance.edges_removed}/${r.provenance.edges_before}` : "—",
    },
    {
      key: "true_contaminated", label: "Contaminated", num: true,
      render: (r) => r.true_contaminated.length,
    },
    {
      key: "recovered", label: "Recovered",
      render: (r) => r.recovered ? '<span class="badge repaired">yes</span>'
        : '<span class="badge suspected">no</span>',
    },
  ]));
  STATE.system = await api("/api/system");
  renderOverview();
}

$("btn-create").addEventListener("click", async () => {
  const btn = $("btn-create");
  btn.disabled = true;
  const out = $("incident-result");
  out.innerHTML = '<div class="notice info"><span class="spinner"></span>Building incident…</div>';
  try {
    const body = {
      family: $("in-family").value,
      task_id: $("in-task").value,
      depth: Number($("in-depth").value),
      provenance: $("in-prov").value,
      n_controls: Number($("in-controls").value),
    };
    const inc = await api("/api/incidents", { method: "POST", body: JSON.stringify(body) });
    const detail = await api(`/api/incidents/${encodeURIComponent(inc.incident_id)}`);
    renderIncident(detail, out);
    await refreshIncidents();
    $("care-incident").value = inc.incident_id;
    $("bl-incident").value = inc.incident_id;
    $("pv-incident").value = inc.incident_id;
  } catch (e) {
    out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
});

$("btn-reset").addEventListener("click", async () => {
  await api("/api/system/reset", { method: "POST" });
  $("incident-result").innerHTML = '<div class="notice info">System reset.</div>';
  await refreshIncidents();
});

function renderChain(nodes, seedKey) {
  const chain = el("div", "chain");
  nodes.forEach((n, i) => {
    if (i) chain.appendChild(el("div", "chain-arrow", "→"));
    const cls = ["chain-node"];
    if (n.contaminated) cls.push("contaminated");
    if (n.state === "repaired") cls.push("repaired");
    if (n.key === seedKey) cls.push("seed");
    const node = el("div", cls.join(" "));
    node.innerHTML =
      `<div class="depth">depth ${n.depth}${n.key === seedKey ? " · SEED" : ""}</div>
       <div class="type">${esc(n.type)}</div>
       <div class="role">${esc(n.role)}</div>
       <div class="pt">patient ${esc(n.patient ?? "—")}</div>
       <div>${stateBadge(n.state)}</div>`;
    chain.appendChild(node);
  });
  return chain;
}

function renderIncident(inc, out) {
  out.innerHTML = "";
  const info = inc.family_info || {};
  out.appendChild(el("div", "notice info",
    `<b>${esc(inc.incident_id)}</b> — ${esc(info.name ?? inc.family)}.
     Seed: <span class="mono">${esc(info.seed ?? "")}</span><br>
     Propagation: ${esc(info.propagation ?? "")}<br>
     Observable failure: ${esc(info.failure ?? "")}`));

  const p = inc.provenance;
  if (p) {
    out.appendChild(el("div", "notice warn",
      `Provenance condition <b>${esc(p.condition)}</b> — ${esc(p.description)}
       Removed <b>${p.edges_removed}</b> of ${p.edges_before} observable edges
       (${(p.loss_fraction * 100).toFixed(0)}% loss).`));
  }

  out.appendChild(el("h3", null, "Contaminated trajectory"));
  out.appendChild(renderChain(inc.trajectory, inc.seed_key));

  const wrong = inc.wrong_patient
    ? `Intended patient <span class="mono">${esc(inc.intended_patient)}</span>,
       seed points at <span class="mono">${esc(inc.wrong_patient)}</span>.`
    : `Intended patient <span class="mono">${esc(inc.intended_patient)}</span>.`;
  out.appendChild(el("p", "small muted", wrong +
    ` Ground truth marks <b>${inc.true_contaminated.length}</b> contaminated descendant(s).`));

  if (inc.controls && inc.controls.length) {
    out.appendChild(el("h3", null, "Matched clean control (hard negative)"));
    inc.controls.forEach((c) => {
      out.appendChild(renderChain(
        c.nodes.map((n, i) => ({ ...n, depth: i, role: "—", contaminated: false })), null));
    });
    out.appendChild(el("p", "small muted",
      "Surface-similar but causally independent. Recovery must leave these intact — " +
      "that is what separates causal inheritance from semantic similarity."));
  }
}

/* ---------------- CARE ---------------- */
$("btn-recover").addEventListener("click", async () => {
  const incident_id = $("care-incident").value;
  if (!incident_id) return;
  const btn = $("btn-recover");
  btn.disabled = true;
  const out = $("care-result");
  out.innerHTML = '<div class="notice info"><span class="spinner"></span>Running CARE loop…</div>';
  try {
    const body = {
      incident_id,
      use_sketch: $("opt-sketch").checked,
      use_explicit_lineage: $("opt-lineage").checked,
      use_counterfactual: $("opt-counterfactual").checked,
      use_recompilation: $("opt-recompile").checked,
      use_enforcement: $("opt-enforce").checked,
      use_scoping: $("opt-scope").checked,
    };
    const r = await api("/api/recover", { method: "POST", body: JSON.stringify(body) });
    STATE.lastRecovery = r;
    renderRecovery(r, out);
    await refreshIncidents();
  } catch (e) {
    out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
});

document.querySelectorAll("#care-toggles input").forEach((cb) => {
  cb.addEventListener("change", () => {
    cb.closest(".toggle").classList.toggle("off", !cb.checked);
  });
});

function renderRecovery(r, out) {
  out.innerHTML = "";
  const m = r.metrics || {};
  const cert = r.certificate || {};

  const banner = cert.safe_resume
    ? el("div", "notice good",
      `<b>SAFE RESUME APPROVED</b> — closure reached in ${r.rounds} round(s), ` +
      `${r.repaired.length} artifact(s) rebuilt from trusted FHIR sources, ` +
      `${r.resurrection_probe.blocked}/${r.resurrection_probe.attempts} resurrection probes blocked.`)
    : el("div", "notice warn",
      `<b>SAFE RESUME BLOCKED</b> — review required. ` +
      `${(cert.unresolved_risk || []).length} unresolved risk item(s).`);
  out.appendChild(banner);

  // CARE stages
  const stages = el("div", "stages");
  const stageData = [
    ["C", "Candidate discovery", r.candidates.length, "candidates ranked"],
    ["A", "Attribution", `${r.confirmed.length} / ${r.cleared.length}`, "confirmed / cleared by replay"],
    ["R", "Recompilation", `${r.repaired.length} / ${r.quarantined.length}`, "repaired / quarantined"],
    ["E", "Enforcement", r.enforcement.tombstones, "tombstones committed"],
  ];
  stageData.forEach(([letter, name, metric, desc]) => {
    const s = el("div", "stage");
    s.innerHTML = `<div class="letter">${letter}</div><div class="name">${esc(name)}</div>
                   <div class="metric">${esc(metric)}</div><div class="desc">${esc(desc)}</div>`;
    stages.appendChild(s);
  });
  out.appendChild(stages);

  // metrics
  out.appendChild(el("h3", null, "Outcome metrics"));
  const metricRows = [
    ["Residual wrong-patient / unauthorized harm (RWH)", m.rwh, "bad", true],
    ["Descendant recall", m.descendant_recall, "good", false],
    ["Descendant precision", m.descendant_precision, "good", false],
    ["Benign-state retention (BSR)", m.bsr, "good", false],
    ["Repaired task success (RTS)", m.rts, "good", false],
    ["False repair rate", m.false_repair_rate, "bad", true],
    ["Unauthorized exposure rate (UER)", m.uer, "bad", true],
    ["Deletion resurrection rate (DRR)", m.drr, "bad", true],
  ].map(([label, value, kind, lower]) => ({ label, value, kind, lower }));
  out.appendChild(table(metricRows, [
    { key: "label", label: "Metric", wrap: true },
    { key: "value", label: "Value", num: true,
      render: (r2) => bar(r2.value, r2.kind === "bad"
        ? (r2.value > 0 ? "bad" : "good") : (r2.value >= 0.999 ? "good" : "warn")) },
    { key: "dir", label: "Direction", render: (r2) => r2.lower ? "lower better" : "higher better" },
  ]));

  // candidates
  if (r.candidates.length) {
    const det = el("details");
    det.appendChild(el("summary", null, `Candidate set (${r.candidates.length})`));
    det.appendChild(table(r.candidates, [
      { key: "memory_key", label: "Memory" },
      { key: "runtime", label: "Runtime" },
      { key: "score", label: "Score", num: true, render: (c) => fmt(c.score) },
      { key: "explicit", label: "Via", render: (c) => c.explicit
        ? '<span class="badge active">exact lineage</span>'
        : '<span class="badge suspected">latent sketch</span>' },
    ]));
    out.appendChild(det);
  }

  // verdicts
  if (r.verdicts.length) {
    const det = el("details");
    det.appendChild(el("summary", null,
      `Signed verdicts returned to coordinator (${r.verdicts.length}) — note the absence of clinical text`));
    det.appendChild(table(r.verdicts, [
      { key: "runtime", label: "Runtime" },
      { key: "influence_band", label: "Band",
        render: (v) => `<span class="badge ${v.influence_band === "high" ? "contaminated" : "suspected"}">${esc(v.influence_band)}</span>` },
      { key: "influence_score", label: "I(s→v)", num: true, render: (v) => fmt(v.influence_score) },
      { key: "predicate_changed", label: "Predicate changed",
        render: (v) => v.predicate_changed ? "yes" : "no" },
      { key: "disposition", label: "Disposition" },
      { key: "memory_commitment", label: "Commitment",
        render: (v) => `<span class="mono">${esc(String(v.memory_commitment).slice(0, 16))}…</span>` },
    ]));
    out.appendChild(det);
  }

  // repairs
  if (r.repaired.length) {
    const det = el("details");
    det.appendChild(el("summary", null, `Clean-room repairs (${r.repaired.length})`));
    det.appendChild(table(r.repaired, [
      { key: "memory_key", label: "Original" },
      { key: "new_key", label: "Repaired version" },
      { key: "confidence", label: "Confidence", num: true },
      { key: "reason", label: "Basis", wrap: true },
    ]));
    out.appendChild(det);
  }
  if (r.quarantined.length) {
    const det = el("details");
    det.appendChild(el("summary", null, `Quarantined for review (${r.quarantined.length})`));
    det.appendChild(table(r.quarantined, [
      { key: "memory_key", label: "Memory" },
      { key: "reason", label: "Reason", wrap: true },
    ]));
    out.appendChild(det);
  }

  // capsules
  if (r.capsules.length) {
    const det = el("details");
    det.appendChild(el("summary", null,
      "Recovery capsules — every field that left a runtime"));
    det.appendChild(table(r.capsules, [
      { key: "recipient", label: "Recipient" },
      { key: "patient_token", label: "Patient token",
        render: (c) => `<span class="mono">${esc(String(c.patient_token).slice(0, 18))}…</span>` },
      { key: "artifact_type_band", label: "Type band" },
      { key: "time_band", label: "Time band" },
      { key: "sketch_dim", label: "Sketch dim", num: true },
      { key: "size_bytes", label: "Bytes", num: true },
    ]));
    det.appendChild(el("p", "small muted",
      "No patient name, MRN, note, laboratory value, hidden state, or KV cache appears " +
      "in this table because the capsule schema has no field for them."));
    out.appendChild(det);
  }

  // certificate
  out.appendChild(el("h3", null, "Recovery certificate"));
  out.appendChild(el("pre", "cert", esc(r.certificate_text)));
}

/* ---------------- baselines ---------------- */
$("btn-baselines").addEventListener("click", async () => {
  const incident_id = $("bl-incident").value;
  if (!incident_id) return;
  const btn = $("btn-baselines"); btn.disabled = true;
  const out = $("baseline-result");
  out.innerHTML = '<div class="notice info"><span class="spinner"></span>Running all nine conditions…</div>';
  try {
    const r = await api("/api/baselines",
      { method: "POST", body: JSON.stringify({ incident_id }) });
    out.innerHTML = "";
    out.appendChild(el("div", "notice info",
      `Provenance condition: <b>${esc(r.provenance)}</b>. All conditions ran against the
       same frozen snapshot and the same follow-up tasks.`));
    out.appendChild(table(r.results.filter((x) => !x.error), [
      { key: "condition", label: "ID" },
      { key: "name", label: "Condition", wrap: true },
      { key: "rwh", label: "RWH ↓", num: true, render: (x) => bar(x.rwh, x.rwh > 0 ? "bad" : "good") },
      { key: "descendant_recall", label: "Recall ↑", num: true,
        render: (x) => bar(x.descendant_recall, x.descendant_recall >= 0.999 ? "good" : "warn") },
      { key: "descendant_precision", label: "Precision ↑", num: true,
        render: (x) => bar(x.descendant_precision, x.descendant_precision >= 0.999 ? "good" : "warn") },
      { key: "bsr", label: "BSR ↑", num: true,
        render: (x) => bar(x.bsr, x.bsr >= 0.999 ? "good" : "bad") },
      { key: "rts", label: "RTS ↑", num: true, render: (x) => fmt(x.rts) },
      { key: "uer", label: "UER ↓", num: true,
        render: (x) => bar(x.uer, x.uer > 0 ? "bad" : "good") },
      { key: "drr", label: "DRR ↓", num: true,
        render: (x) => bar(x.drr, x.drr > 0 ? "bad" : "good") },
    ], { highlight: (x) => x.condition === "I" }));
    out.appendChild(el("p", "small muted",
      "Row I is AEGIS-Care. Compare against D/E (explicit lineage only), F (similarity " +
      "treated as causality), C (full reset), G (raw-content oracle, note its UER), and " +
      "H (complete private oracle graph)."));
    const errs = r.results.filter((x) => x.error);
    if (errs.length) {
      out.appendChild(el("div", "notice bad",
        "Failed conditions: " + errs.map((x) => `${x.condition} (${esc(x.error)})`).join(", ")));
    }
  } catch (e) {
    out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
});

/* ---------------- privacy ---------------- */
$("btn-privacy").addEventListener("click", async () => {
  const incident_id = $("pv-incident").value;
  if (!incident_id) return;
  const btn = $("btn-privacy"); btn.disabled = true;
  const out = $("privacy-result");
  out.innerHTML = '<div class="notice info"><span class="spinner"></span>Attacking the recovery interface…</div>';
  try {
    const r = await api(`/api/privacy/${encodeURIComponent(incident_id)}`);
    out.innerHTML = "";
    const attacks = ["attribute_gender", "attribute_restricted", "membership", "linkability"]
      .map((k) => r[k]).filter(Boolean);
    out.appendChild(table(attacks, [
      { key: "name", label: "Attack", wrap: true },
      { key: "n", label: "n", num: true },
      { key: "accuracy", label: "Accuracy", num: true, render: (a) => fmt(a.accuracy) },
      { key: "baseline", label: "Baseline", num: true, render: (a) => fmt(a.baseline) },
      { key: "advantage", label: "Advantage", num: true,
        render: (a) => `<span style="color:${a.advantage > 0.05 ? "var(--bad)" : "var(--good)"}">${fmt(a.advantage)}</span>` },
    ]));

    const link = r.linkability || {};
    const ablation = link.detail && link.detail.unscoped_ablation_accuracy;
    if (ablation !== undefined) {
      out.appendChild(el("div", "notice good",
        `<b>Receiver scoping is load-bearing.</b> With scoping, cross-recipient linkage runs at
         chance (${fmt(link.accuracy)} vs ${fmt(link.baseline)} baseline). Remove scoping and
         linkage accuracy rises to <b>${fmt(ablation)}</b> — an honest-but-curious coordinator
         could join every recovery event back to the same patient.`));
    }

    const mem = r.membership || {};
    if (mem.advantage > 0.05) {
      out.appendChild(el("div", "notice warn",
        `<b>Residual leak reported, not hidden.</b> Membership inference achieves
         ${fmt(mem.accuracy)} against a ${fmt(mem.baseline)} baseline
         (advantage ${fmt(mem.advantage)}). The sketch is a candidate-discovery signal, and
         it does carry information. The proposal makes no confidentiality claim for it.`));
    }

    const rf = r.released_fields || {};
    out.appendChild(el("h3", null, "Released-field audit"));
    out.appendChild(el("div", rf.raw_content_exported ? "notice bad" : "notice good",
      `Raw clinical content exported through the recovery interface:
       <b>${rf.raw_content_exported ? "YES" : "NONE"}</b>.
       ${rf.capsules} capsule(s), ${rf.total_bytes} bytes total.`));
    out.appendChild(el("p", "small mono", esc((rf.fields_released || []).join(" · "))));
    if ((rf.undeclared_fields || []).length) {
      out.appendChild(el("div", "notice bad",
        "Undeclared fields present: " + esc(rf.undeclared_fields.join(", "))));
    }
  } catch (e) {
    out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
});

/* ---------------- review ---------------- */
async function loadReview() {
  const out = $("review-result");
  try {
    const r = await api("/api/review/queue");
    out.innerHTML = "";
    if (!r.count) {
      out.appendChild(el("div", "notice good",
        "Queue empty — no artifact required human escalation."));
      return;
    }
    r.items.forEach((item) => {
      const p = el("div", "panel");
      p.style.marginTop = "12px";
      p.innerHTML =
        `<h3>${esc(item.memory_id)} <span class="mono muted">v${item.version}</span>
           ${stateBadge(item.state)}</h3>
         <p class="small muted">${esc(item.quarantine_reason || "")}</p>
         <pre class="cert">${esc(item.content)}</pre>`;
      const row = el("div", "controls");
      ["approve", "reject", "keep_quarantined"].forEach((decision) => {
        const b = el("button", `btn small ${decision === "reject" ? "danger" : "ghost"}`,
          decision.replace("_", " "));
        b.addEventListener("click", async () => {
          await api("/api/review", {
            method: "POST",
            body: JSON.stringify({ memory_key: `${item.memory_id}@v${item.version}`, decision }),
          });
          loadReview();
        });
        row.appendChild(b);
      });
      p.appendChild(row);
      out.appendChild(p);
    });
  } catch (e) {
    out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
  }
}
$("btn-review-refresh").addEventListener("click", loadReview);

/* ---------------- graph ---------------- */
async function drawGraph() {
  const svg = $("graph-svg");
  svg.innerHTML = "";
  let data;
  try { data = await api("/api/memory/none/graph"); }
  catch (e) { return; }

  const nodes = data.nodes;
  if (!nodes.length) {
    svg.innerHTML = `<text x="20" y="30" fill="#8b97a8" font-size="13">
      No memory yet — create an incident first.</text>`;
    return;
  }

  // Layer nodes by owner role, order within layer by key.
  const roleOrder = ["registration", "nursing", "clinical_summary"];
  const layers = {};
  nodes.forEach((n) => (layers[n.owner] = layers[n.owner] || []).push(n));
  const W = svg.clientWidth || 1000;
  const H = 460;
  const laneH = H / roleOrder.length;
  const pos = {};
  roleOrder.forEach((role, li) => {
    const list = (layers[role] || []).sort((a, b) => a.key.localeCompare(b.key));
    list.forEach((n, i) => {
      pos[n.key] = {
        x: 70 + (i + 0.5) * ((W - 140) / Math.max(1, list.length)),
        y: laneH * li + laneH / 2,
        node: n,
      };
    });
  });

  const ns = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs) => {
    const e = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    return e;
  };

  // lane labels
  roleOrder.forEach((role, li) => {
    svg.appendChild(mk("line", {
      x1: 0, y1: laneH * (li + 1), x2: W, y2: laneH * (li + 1),
      stroke: "#263140", "stroke-width": 1,
    }));
    const t = mk("text", { x: 8, y: laneH * li + 16, fill: "#5f6b7c", "font-size": 11 });
    t.textContent = role;
    svg.appendChild(t);
  });

  // edges
  data.edges.forEach((e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const line = mk("path", {
      d: `M ${a.x} ${a.y} C ${a.x} ${(a.y + b.y) / 2}, ${b.x} ${(a.y + b.y) / 2}, ${b.x} ${b.y}`,
      fill: "none",
      stroke: e.observed ? "#4a9eff" : "#f85149",
      "stroke-width": e.observed ? 1.6 : 1.3,
      "stroke-dasharray": e.observed ? "" : "5,4",
      opacity: e.observed ? 0.8 : 0.55,
    });
    svg.appendChild(line);
  });

  // nodes
  Object.values(pos).forEach(({ x, y, node }) => {
    const colors = {
      active: ["#1c2532", "#3fb950"], repaired: ["rgba(63,185,80,.2)", "#3fb950"],
      suspected: ["rgba(248,81,73,.25)", "#f85149"],
      quarantined: ["rgba(210,153,34,.25)", "#d29922"],
      superseded: ["#1c2532", "#6e7681"], tombstoned: ["#1c2532", "#6e7681"],
    };
    const [fill, stroke] = colors[node.state] || ["#1c2532", "#8b97a8"];
    svg.appendChild(mk("circle", {
      cx: x, cy: y, r: node.focus ? 13 : 10, fill, stroke,
      "stroke-width": node.focus ? 3 : 2,
    }));
    const label = mk("text", {
      x, y: y + 25, fill: "#8b97a8", "font-size": 9.5, "text-anchor": "middle",
    });
    label.textContent = node.type.replace(/_/g, " ");
    svg.appendChild(label);
    const pt = mk("text", {
      x, y: y - 17, fill: "#5f6b7c", "font-size": 9, "text-anchor": "middle",
    });
    pt.textContent = node.patient || "";
    svg.appendChild(pt);
    const title = mk("title", {});
    title.textContent = `${node.key}\nstate: ${node.state}\npatient: ${node.patient}`;
    svg.appendChild(title);
  });
}
$("btn-graph-refresh").addEventListener("click", drawGraph);

/* ---------------- experiment ---------------- */
$("btn-experiment").addEventListener("click", async () => {
  const btn = $("btn-experiment"); btn.disabled = true;
  const out = $("experiment-result");
  out.innerHTML = '<div class="notice info"><span class="spinner"></span>' +
    'Running the full matrix — this executes every condition on paired frozen state…</div>';
  const sel = (id) => Array.from($(id).selectedOptions).map((o) => o.value);
  try {
    const body = {
      families: sel("ex-families"),
      depths: sel("ex-depths").map(Number),
      provenance_conditions: sel("ex-prov"),
      tasks_per_family: Number($("ex-tasks").value),
    };
    const r = await api("/api/experiment", { method: "POST", body: JSON.stringify(body) });
    out.innerHTML = "";
    out.appendChild(el("div", "notice good",
      `Complete in ${r.wall_seconds}s — ${r.incidents} incidents, ${r.runs} condition runs.
       Tables, figures, and report written to <span class="mono">results/</span>.`));

    out.appendChild(el("h3", null, "Aggregate by condition"));
    out.appendChild(table(r.by_condition, [
      { key: "condition", label: "ID" },
      { key: "n", label: "n", num: true },
      { key: "rwh", label: "RWH ↓", num: true, render: (x) => bar(x.rwh, x.rwh > 0 ? "bad" : "good") },
      { key: "descendant_recall", label: "Recall ↑", num: true,
        render: (x) => bar(x.descendant_recall, "good") },
      { key: "descendant_precision", label: "Precision ↑", num: true,
        render: (x) => bar(x.descendant_precision, "good") },
      { key: "bsr", label: "BSR ↑", num: true, render: (x) => bar(x.bsr, "good") },
      { key: "uer", label: "UER ↓", num: true, render: (x) => bar(x.uer, x.uer > 0 ? "bad" : "good") },
      { key: "drr", label: "DRR ↓", num: true, render: (x) => fmt(x.drr) },
    ], { highlight: (x) => x.condition === "I" }));

    out.appendChild(el("h3", null, "RQ1 — recovery under provenance loss"));
    out.appendChild(table(r.by_condition_provenance, [
      { key: "condition", label: "ID" },
      { key: "provenance", label: "Provenance" },
      { key: "descendant_recall", label: "Recall ↑", num: true,
        render: (x) => bar(x.descendant_recall, "good") },
      { key: "descendant_precision", label: "Precision ↑", num: true,
        render: (x) => fmt(x.descendant_precision) },
      { key: "bsr", label: "BSR ↑", num: true, render: (x) => fmt(x.bsr) },
      { key: "rwh", label: "RWH ↓", num: true, render: (x) => fmt(x.rwh) },
    ], { highlight: (x) => x.condition === "I" }));

    out.appendChild(el("h3", null, "Oracle regret vs condition H"));
    out.appendChild(table(
      Object.entries(r.oracle_regret).map(([condition, regret]) => ({ condition, regret })),
      [{ key: "condition", label: "Condition" },
       { key: "regret", label: "Regret", num: true, render: (x) => fmt(x.regret, 4) }],
      { highlight: (x) => x.condition === "I" }));

    if (r.verification_failures && r.verification_failures.length) {
      out.appendChild(el("h3", null, "Verification failures (reported, not discarded)"));
      out.appendChild(table(r.verification_failures, [
        { key: "incident", label: "Incident", wrap: true },
        { key: "condition", label: "Condition" },
        { key: "reason", label: "Reason", wrap: true,
          render: (x) => esc(x.reason || x.error || "") },
      ]));
    }

    const link = el("p", "small muted");
    link.innerHTML = 'Full markdown report: <a href="/api/experiment/report" ' +
      'target="_blank" style="color:var(--accent)">/api/experiment/report</a>';
    out.appendChild(link);
  } catch (e) {
    out.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
  } finally { btn.disabled = false; }
});

/* ---------------- audit ---------------- */
async function loadAudit() {
  const log = $("audit-log");
  try {
    const r = await api("/api/events?limit=300");
    log.innerHTML = "";
    r.events.forEach((e) => {
      const row = el("div", "row");
      row.innerHTML =
        `<span class="muted">${esc(String(e.at).slice(11, 23))}</span>
         <span class="actor">${esc(e.actor)}</span>
         <span class="kind">${esc(e.kind)}</span>
         <span class="subject">${esc(e.subject ?? "")}</span>`;
      log.appendChild(row);
    });
    if (!r.events.length) log.innerHTML = '<span class="muted">No events yet.</span>';
  } catch (e) {
    log.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`;
  }
}
$("btn-audit-refresh").addEventListener("click", loadAudit);

/* ---------------- go ---------------- */
boot().catch((e) => {
  document.querySelector("main").innerHTML =
    `<div class="notice bad">Failed to start: ${esc(e.message)}</div>`;
});
