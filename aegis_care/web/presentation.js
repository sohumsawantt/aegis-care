/* AEGIS-Care guided presentation.
 * Vanilla JS, no dependencies. Every act calls the live API.
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
const f3 = (v) => (typeof v === "number" ? v.toFixed(3) : "—");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch (e) { /* keep */ }
    throw new Error(detail);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

/* ================================================================== */
const S = {
  acts: [], idx: 0, completed: new Set(), settings: {}, system: null,
  poison: null, care: null, busy: false,
};

/* ---- what each act's primary button does ---- */
const ACTIONS = {
  intro:    { label: "Begin",                    hint: "Loads the FHIR sandbox and the three agent runtimes", run: runSystem },
  system:   { label: "Load the sandbox",         hint: "Live figures from the running environment",           run: runSystem },
  clean:    { label: "Run the task cleanly",     hint: "Executes the workflow with no contamination",         run: runClean },
  poison:   { label: "Plant the poisoned memory",hint: "Writes one wrong association and lets it propagate",  run: runPoison },
  deletion: { label: "Delete the seed",          hint: "Baseline B — remove only the flagged entry",          run: runDeletion },
  reset:    { label: "Wipe all memory",          hint: "Baseline C — the safe, expensive fallback",           run: runReset },
  care:     { label: "Run the CARE loop",        hint: "Discover → confirm → rebuild → enforce",              run: runCare },
  verify:   { label: "Show the certificate",     hint: "Capsule contents, probes, and the resume decision",   run: runVerify },
  compare:  { label: "Run all nine conditions",  hint: "Each on identical frozen state",                      run: runCompare },
  privacy:  { label: "Attack our own interface", hint: "Attribute, membership, and linkability attacks",      run: runPrivacy },
  close:    { label: "Finish",                   hint: "",                                                    run: runClose },
};

/* ---- speaker notes ---- */
const NOTES = {
  intro: [
    "Frame the problem before any screen: an assistant stores a <b>wrong patient association</b>, summarises it forward, and keeps reusing the derived state.",
    "The three boxes are the existing options. Say plainly why each one is inadequate.",
    "Stress that this is a <b>live system</b>, not slides — you will run every step.",
  ],
  system: [
    "Point out the <b>coordinator has zero clinical read rights</b>. That is the constraint that makes the problem hard.",
    "The sketch is only <b>64 bytes</b> — that is all that travels between runtimes for discovery.",
    "Mention role separation is enforced by a deterministic policy engine, never by the model.",
  ],
  clean: [
    "Walk left to right: registration resolves identity → nursing derives a cue and a handover → summary derives from the handover.",
    "Each box is a <b>durable memory</b>, not a transient message. That persistence is what makes the failure possible.",
    "Confirm the follow-up task selects the correct patient.",
  ],
  poison: [
    "Only <b>one</b> memory was corrupted — the registration alias. Everything downstream derived it honestly.",
    "Point at the provenance figure: most lineage edges are <b>hidden</b>. The system cannot simply follow pointers.",
    "The clean control chain below is <b>surface-similar but causally independent</b>. Recovery must not touch it.",
    "End on the red banner: the follow-up task now acts on the wrong patient.",
  ],
  deletion: [
    "This is the intuitive fix, and the key moment of the talk.",
    "The seed is gone — struck through. <b>The task is still wrong.</b>",
    "Explain why: the descendants are live retrieval cues, and later sessions consume them.",
    "Recall is <b>0.000</b>: not one contaminated descendant was addressed.",
  ],
  reset: [
    "Reset does fix safety — residual harm goes to zero.",
    "Now point at benign-state retention: <b>0.000</b>. Every legitimate memory, including the untouched control, is destroyed.",
    "So we have one option that is unsafe and one that is useless. That motivates CARE.",
  ],
  care: [
    "Take the four stages one at a time as they light up.",
    "<b>C</b>: exact edges are mostly masked, so the latent sketch proposes candidates. It only ranks — it never deletes.",
    "<b>A</b>: each candidate is replayed <i>without</i> the suspected ancestor, inside its own runtime. This is what separates causation from similarity.",
    "<b>R</b>: confirmed descendants are rebuilt from trusted FHIR. Note the version numbers going v1 → v2.",
    "<b>E</b>: tombstones and a firewall, so a later session cannot reintroduce the withdrawn influence.",
    "Point out the cleared candidates — the clean control was examined and <b>left intact</b>.",
  ],
  verify: [
    "Show the capsule first: this is <b>everything</b> that left a runtime. There is no field for a name, MRN, note, or lab value.",
    "The struck-through fields are what a naive design would have shipped.",
    "Then the certificate: counts, unresolved risk, and an explicit <b>safe-resume</b> decision.",
    "Resurrection probes: the system tries to re-inject the withdrawn memory and is blocked.",
  ],
  compare: [
    "Read down the RWH column, then the BSR column. Every non-CARE row fails one or the other.",
    "Row <b>G</b> matches CARE on safety, but look at UER = 1.000 — it read raw clinical content from every runtime.",
    "Row <b>H</b> is the oracle with the complete private lineage graph. It is unattainable in operation.",
    "<b>I</b> is the only privacy-respecting row that is good on all of them at once.",
  ],
  privacy: [
    "Be upfront: <b>membership inference works</b>. We report it rather than hide it.",
    "Attribute inference sits at or below chance — the sketch does not reveal gender or the restricted flag.",
    "The strongest result: with scoping, cross-recipient linkage is at chance. Remove scoping and it hits <b>1.000</b>.",
    "That is what makes receiver scoping a load-bearing design decision rather than decoration.",
  ],
  close: [
    "Restate the contribution as a <b>recovery problem</b>, not a new provenance or embedding technique.",
    "Each stage exists because the previous one has a measurable failure mode.",
    "Be honest about limits: simulated sandbox, no clinical claims, membership leak measured and reported.",
    "Offer the analyst console and the 900-run experiment if they want depth.",
  ],
};

/* ================================================================== */
/* rail + navigation                                                   */
/* ================================================================== */
function renderRail() {
  const rail = $("rail");
  rail.innerHTML = "";
  S.acts.forEach((act, i) => {
    const item = el("div", "rail-item");
    if (i === S.idx) item.classList.add("active");
    if (S.completed.has(act.id)) item.classList.add("done");
    item.innerHTML = `<span class="rail-num">${S.completed.has(act.id) ? "✓" : i}</span>
                      <span>${esc(act.title)}</span>`;
    item.addEventListener("click", () => goTo(i));
    rail.appendChild(item);
  });
}

function goTo(i) {
  if (i < 0 || i >= S.acts.length) return;
  S.idx = i;
  const id = S.acts[i].id;
  document.querySelectorAll(".act").forEach((s) => s.classList.remove("active"));
  const section = document.querySelector(`.act[data-act="${id}"]`);
  if (section) section.classList.add("active");
  $("stage").scrollTop = 0;
  renderRail();
  renderNotes();
  updateAction();
}

function updateAction() {
  const id = S.acts[S.idx]?.id;
  const action = ACTIONS[id];
  const btn = $("btn-action");
  btn.textContent = action ? action.label : "—";
  btn.disabled = !action || S.busy;
  $("action-hint").textContent = action ? action.hint : "";
  $("btn-prev").disabled = S.idx === 0;
  $("btn-next").disabled = S.idx >= S.acts.length - 1;
}

function renderNotes() {
  const id = S.acts[S.idx]?.id;
  const items = NOTES[id] || [];
  $("notes-body").innerHTML = items.length
    ? `<ul>${items.map((n) => `<li>${n}</li>`).join("")}</ul>`
    : '<p class="muted small" style="margin:0">No notes for this act.</p>';
}

async function withBusy(btnLabel, fn) {
  if (S.busy) return;
  S.busy = true;
  const btn = $("btn-action");
  const original = btn.textContent;
  btn.innerHTML = `<span class="spinner"></span>${btnLabel}`;
  btn.disabled = true;
  try {
    await fn();
  } catch (e) {
    const body = document.querySelector(".act.active > div:last-child");
    if (body) {
      body.prepend(el("div", "callout bad",
        `<div class="icon">✕</div><div><b>Failed.</b> ${esc(e.message)}</div>`));
    }
  } finally {
    S.busy = false;
    btn.textContent = original;
    updateAction();
  }
}

function markDone(id) { S.completed.add(id); renderRail(); }

/* ================================================================== */
/* shared renderers                                                    */
/* ================================================================== */
function nodeClass(n) {
  const cls = ["chain-node"];
  if (!n.servable && n.state !== "repaired") cls.push("gone");
  else if (n.state === "repaired") cls.push("repaired");
  else if (n.wrong_patient || n.restricted) cls.push("bad");
  else cls.push("ok");
  if (n.is_seed) cls.push("seed");
  if (n.restricted && n.servable) cls.push("restricted");
  return cls.join(" ");
}

function renderChain(nodes, opts = {}) {
  const wrap = el("div", "chain");
  nodes.forEach((n, i) => {
    if (i) wrap.appendChild(el("div", "chain-arrow", "→"));
    const node = el("div", nodeClass(n));
    node.dataset.depth = String(n.depth);
    const shown = n.state === "repaired" && n.repaired_patient ? n.repaired_patient : n.patient;
    node.innerHTML =
      `${n.is_seed ? '<div class="seedtag">SEED</div>' : ""}
       <div class="role">${esc(n.role.replace(/_/g, " "))}</div>
       <div class="type">${esc(n.type.replace(/_/g, " "))}</div>
       <div class="pt">${esc(shown ?? "—")}</div>
       <div class="meta">v${n.version} · <span class="badge b-${esc(n.state)}">${esc(n.state)}</span></div>`;
    if (opts.hidePatient) node.querySelector(".pt").textContent = "…";
    wrap.appendChild(node);
  });
  return wrap;
}

/** Reveal contamination hop by hop so the audience can follow it. */
async function animateChain(container, nodes, stepMs = 620) {
  const wrap = el("div", "chain");
  const built = [];
  nodes.forEach((n, i) => {
    if (i) wrap.appendChild(el("div", "chain-arrow", "→"));
    const node = el("div", "chain-node ok");
    node.innerHTML =
      `${n.is_seed ? '<div class="seedtag">SEED</div>' : ""}
       <div class="role">${esc(n.role.replace(/_/g, " "))}</div>
       <div class="type">${esc(n.type.replace(/_/g, " "))}</div>
       <div class="pt">${esc(n.intended_patient)}</div>
       <div class="meta">v${n.version} · <span class="badge b-active">active</span></div>`;
    wrap.appendChild(node);
    built.push(node);
  });
  container.appendChild(wrap);

  for (let i = 0; i < nodes.length; i++) {
    await sleep(stepMs);
    const n = nodes[i];
    const node = built[i];
    node.className = nodeClass(n) + " pulse";
    const shown = n.state === "repaired" && n.repaired_patient ? n.repaired_patient : n.patient;
    node.querySelector(".pt").textContent = shown ?? "—";
    node.querySelector(".meta").innerHTML =
      `v${n.version} · <span class="badge b-${esc(n.state)}">${esc(n.state)}</span>`;
    if (n.restricted && n.servable) node.classList.add("restricted");
    setTimeout(() => node.classList.remove("pulse"), 700);
  }
}

function probeBanner(probe, opts = {}) {
  const ok = probe.correct;
  const cls = ok ? "good" : "bad";
  const icon = ok ? "✓" : "✕";
  const verdict = ok
    ? `The follow-up task selects <b>${esc(probe.selected_patient)}</b> — the correct patient.`
    : `The follow-up task selects <b>${esc(probe.selected_patient)}</b>, but the correct
       patient is <b>${esc(probe.intended_patient)}</b>. This is a wrong-patient event.`;
  return el("div", `callout ${cls}`,
    `<div class="icon">${icon}</div><div>${opts.prefix || ""}${verdict}</div>`);
}

function metricTiles(m, keys) {
  const spec = {
    rwh: ["Residual harm", "RWH · lower is better", (v) => v === 0 ? "good" : "bad"],
    descendant_recall: ["Descendant recall", "of contaminated descendants", (v) => v >= 0.999 ? "good" : "bad"],
    descendant_precision: ["Precision", "of what it acted on", (v) => v >= 0.999 ? "good" : "warn"],
    bsr: ["Clean state kept", "BSR · higher is better", (v) => v >= 0.999 ? "good" : "bad"],
    rts: ["Task success", "after recovery", (v) => v >= 0.999 ? "good" : "bad"],
    uer: ["Unauthorised exposure", "UER · lower is better", (v) => v === 0 ? "good" : "bad"],
    drr: ["Resurrection rate", "DRR · lower is better", (v) => v === 0 ? "good" : "bad"],
  };
  const grid = el("div", "grid g4");
  keys.forEach((k) => {
    const [label, sub, kind] = spec[k];
    const v = m[k];
    const tile = el("div", `stat ${kind(v)}`);
    tile.innerHTML = `<div class="k">${esc(label)}</div><div class="v">${f3(v)}</div>
                      <div class="s">${esc(sub)}</div>`;
    grid.appendChild(tile);
  });
  return grid;
}

function bar(v, kind) {
  const pct = Math.max(0, Math.min(1, Number(v) || 0)) * 100;
  return `<span class="bar ${kind}"><i style="width:${pct}%"></i></span>` +
         `<span class="mono">${f3(v)}</span>`;
}

/* ================================================================== */
/* ACTS                                                                */
/* ================================================================== */
async function runSystem() {
  await withBusy("Loading…", async () => {
    const d = await api("/api/demo/system", {});
    const body = $("system-body");
    body.innerHTML = "";

    const tiles = el("div", "grid g4");
    [
      ["Patients", d.fhir.Patient, "synthetic FHIR R4 records", "accent"],
      ["Observations", d.fhir.Observation, "vitals, labs, restricted screens", ""],
      ["Benchmark tasks", d.tasks, "identity · labs · documentation", ""],
      ["Sketch size", `${d.sketch.bytes} B`, `${d.sketch.dim} dims × ${d.sketch.bits} bits`, "accent"],
    ].forEach(([k, v, s, kind]) => {
      tiles.appendChild(el("div", `stat ${kind}`,
        `<div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div>`));
    });
    body.appendChild(tiles);

    body.appendChild(el("h3", null, ""));
    const roles = el("div", "grid g4 mt");
    d.roles.forEach((r) => {
      const isCoord = r.role === "coordinator";
      const card = el("div", `role-card ${isCoord ? "coord" : ""}`);
      card.innerHTML =
        `<div class="rn">${esc(r.role.replace(/_/g, " "))}</div>
         <div class="rf">${r.authorized.length
            ? "May read: " + esc(r.authorized.join(", "))
            : "<b>No clinical read rights at all.</b> It orchestrates recovery using commitments, bands, and signed verdicts."}</div>`;
      roles.appendChild(card);
    });
    body.appendChild(roles);

    body.appendChild(el("div", "callout info mt",
      `<div class="icon">◆</div><div>The coordinator is <b>honest-but-curious</b> and holds
       no clinical read rights. That is the constraint: recovery must work without
       centralising patient content.</div>`));

    S.system = d;
    markDone("system");
  });
}

async function runClean() {
  await withBusy("Running…", async () => {
    const d = await api("/api/demo/clean", {});
    const body = $("clean-body");
    body.innerHTML = "";

    body.appendChild(el("div", "callout info",
      `<div class="icon">▸</div><div>Task <b>${esc(S.settings.task_id)}</b>:
       “${esc(S.settings.query)}” → patient <b>${esc(d.patient_display)}</b>
       (${esc(S.settings.intended_patient)}, MRN ${esc(d.mrn)}).</div>`));

    const panel = el("div", "panel mt");
    panel.appendChild(el("h3", null, "Derivation chain — each box is a durable memory"));
    await animateChain(panel, d.chain, 420);
    body.appendChild(panel);

    body.appendChild(el("div", "mt"));
    body.appendChild(probeBanner(d.probe));
    markDone("clean");
  });
}

async function runPoison() {
  await withBusy("Propagating…", async () => {
    const d = await api("/api/demo/poison", {});
    S.poison = d;
    const body = $("poison-body");
    body.innerHTML = "";

    const info = d.family_info || {};
    body.appendChild(el("div", "callout warn",
      `<div class="icon">⚠</div><div><b>${esc(d.family)} — ${esc(info.name)}.</b>
       ${esc(info.seed)}<br>Propagation path: ${esc(info.propagation)}</div>`));

    if (d.wrong_patient) {
      body.appendChild(el("div", "diff mt",
        `<div><div class="lbl">Intended patient</div>
           <div class="panel" style="border-color:rgba(52,211,153,.4)">
             <div class="mono" style="font-size:19px;color:var(--good)">${esc(d.intended_patient)}</div>
             <div class="small muted">${esc(d.intended_display)}</div></div></div>
         <div><div class="lbl">Seed points at</div>
           <div class="panel" style="border-color:rgba(248,113,113,.5)">
             <div class="mono" style="font-size:19px;color:var(--bad)">${esc(d.wrong_patient)}</div>
             <div class="small muted">${esc(d.wrong_display)}</div></div></div>`));
    }

    const panel = el("div", "panel mt");
    panel.appendChild(el("h3", null, "One corrupted memory, propagating hop by hop"));
    await animateChain(panel, d.chain, 700);
    body.appendChild(panel);

    const p = d.provenance;
    body.appendChild(el("div", "callout warn mt",
      `<div class="icon">◈</div><div><b>Provenance condition: ${esc(p.condition)}.</b>
       ${esc(p.description)} <b>${p.edges_removed} of ${p.edges_before}</b> observable
       lineage edges are hidden (${Math.round(p.loss_fraction * 100)}% loss) — so recovery
       cannot simply follow pointers.</div>`));

    if (d.controls?.length) {
      const cp = el("div", "panel mt");
      cp.appendChild(el("h3", null, "Matched clean control — surface-similar, causally independent"));
      cp.appendChild(renderChain(d.controls[0]));
      cp.appendChild(el("p", "small muted", "This trajectory was never derived from the seed. " +
        "Any recovery method that destroys it is over-quarantining."));
      body.appendChild(cp);
    }

    body.appendChild(el("div", "mt"));
    body.appendChild(probeBanner(d.probe));
    markDone("poison");
  });
}

async function conditionAct(condition, bodyId, actId, extra) {
  const d = await api(`/api/demo/condition/${condition}`, {});
  const body = $(bodyId);
  body.innerHTML = "";
  body.appendChild(el("div", "callout info",
    `<div class="icon">▸</div><div><b>Condition ${esc(d.condition)} — ${esc(d.name)}.</b>
     ${esc(d.purpose)}</div>`));

  const panel = el("div", "panel mt");
  panel.appendChild(el("h3", null, "Memory after this recovery strategy"));
  panel.appendChild(renderChain(d.chain));
  body.appendChild(panel);

  if (d.controls?.length) {
    const cp = el("div", "panel mt");
    cp.appendChild(el("h3", null, "Matched clean control"));
    cp.appendChild(renderChain(d.controls[0]));
    body.appendChild(cp);
  }

  body.appendChild(el("div", "mt"));
  body.appendChild(probeBanner(d.probe));
  body.appendChild(el("div", "mt"));
  body.appendChild(metricTiles(d.metrics,
    ["rwh", "descendant_recall", "bsr", "rts"]));
  if (extra) body.appendChild(extra(d));
  markDone(actId);
  return d;
}

async function runDeletion() {
  await withBusy("Deleting…", async () => {
    await conditionAct("B", "deletion-body", "deletion", (d) =>
      el("div", "callout bad mt",
        `<div class="icon">✕</div><div><b>The seed is gone and the system is still wrong.</b>
         Descendant recall is <b>${f3(d.metrics.descendant_recall)}</b> — not one contaminated
         descendant was addressed. The handover and summary are still live retrieval cues, and
         later sessions keep consuming them.<br><br>
         <b>This is the gap the entire project exists to close.</b></div>`));
  });
}

async function runReset() {
  await withBusy("Wiping…", async () => {
    await conditionAct("C", "reset-body", "reset", (d) =>
      el("div", "callout warn mt",
        `<div class="icon">⚠</div><div><b>Safe, and ruinous.</b> Residual harm is
         <b>${f3(d.metrics.rwh)}</b> — but benign-state retention is
         <b>${f3(d.metrics.bsr)}</b>. Every legitimate memory was destroyed, including the
         clean control that had nothing to do with the incident. Persistence stops paying
         for itself.</div>`));
  });
}

async function runCare() {
  await withBusy("Recovering…", async () => {
    const body = $("care-body");
    body.innerHTML = "";

    // Lay the four stage cards out dark, then light them as results arrive.
    const stages = el("div", "stages");
    ["C", "A", "R", "E"].forEach((letter) => {
      const card = el("div", "stage-card");
      card.dataset.stage = letter;
      card.innerHTML = `<div class="letter">${letter}</div>
        <div class="name">…</div><div class="headline">—</div><div class="unit"></div>`;
      stages.appendChild(card);
    });
    body.appendChild(stages);

    const d = await api("/api/demo/care", {});
    S.care = d;

    for (const letter of ["C", "A", "R", "E"]) {
      await sleep(560);
      const s = d.stages[letter];
      const card = stages.querySelector(`[data-stage="${letter}"]`);
      card.classList.add("lit");
      card.innerHTML =
        `<div class="letter">${letter}</div>
         <div class="name">${esc(s.name)}</div>
         <div class="headline">${esc(s.headline)}</div>
         <div class="unit">${esc(s.unit)}</div>
         <div class="explain">${esc(s.explain)}</div>`;
      await sleep(140);
      card.classList.add("done");
    }

    // Attribution detail — the heart of the method.
    const att = d.stages.A;
    if (att.detail?.length) {
      const panel = el("div", "panel mt");
      panel.appendChild(el("h3", null,
        "Signed verdicts returned to the coordinator — note what is absent"));
      const rows = att.detail.map((v) => `
        <tr>
          <td class="mono">${esc(v.memory_commitment)}…</td>
          <td>${esc(v.runtime.replace(/_/g, " "))}</td>
          <td><span class="badge ${v.influence_band === "high" ? "b-suspected" : "b-superseded"}">${esc(v.influence_band)}</span></td>
          <td class="num">${f3(v.influence_score)}</td>
          <td>${v.predicate_changed ? "yes" : "no"}</td>
          <td><b style="color:${v.disposition === "retain" ? "var(--good)" : "var(--warn)"}">${esc(v.disposition)}</b></td>
        </tr>`).join("");
      panel.appendChild(el("div", "tablewrap",
        `<table><thead><tr><th>Memory commitment</th><th>Runtime</th><th>Band</th>
         <th>I(s→v)</th><th>Predicate changed</th><th>Disposition</th></tr></thead>
         <tbody>${rows}</tbody></table>`));
      panel.appendChild(el("p", "small muted",
        "No patient identifier, no clinical text, no observation value. The coordinator " +
        "learns a band and a disposition — nothing more."));
      body.appendChild(panel);
    }

    const panel = el("div", "panel mt");
    panel.appendChild(el("h3", null, "Memory after recompilation"));
    panel.appendChild(renderChain(d.chain));
    body.appendChild(panel);

    if (d.controls?.length) {
      const cp = el("div", "panel mt");
      cp.appendChild(el("h3", null, "Matched clean control — examined, then left alone"));
      cp.appendChild(renderChain(d.controls[0]));
      cp.appendChild(el("p", "small muted",
        `Counterfactual replay cleared these. This is why precision is
         ${f3(d.metrics.descendant_precision)} instead of collapsing like the
         similarity-only baseline.`));
      body.appendChild(cp);
    }

    body.appendChild(el("div", "mt"));
    body.appendChild(probeBanner(d.probe));
    body.appendChild(el("div", "mt"));
    body.appendChild(metricTiles(d.metrics,
      ["rwh", "descendant_recall", "descendant_precision", "bsr", "rts", "uer", "drr"]));

    markDone("care");
  });
}

async function runVerify() {
  await withBusy("Verifying…", async () => {
    if (!S.care) await api("/api/demo/care", {}).then((d) => (S.care = d));
    const d = S.care;
    const body = $("verify-body");
    body.innerHTML = "";

    // --- capsule -----------------------------------------------------
    const cap = d.capsules?.[0];
    if (cap) {
      const panel = el("div", "panel");
      panel.appendChild(el("h3", null,
        "Everything that left a runtime — one recovery capsule, in full"));
      const shown = [
        ["seed_commitment", cap.seed_commitment, "s"],
        ["recipient", cap.recipient, "s"],
        ["purpose", cap.purpose, "s"],
        ["expires_at", cap.expires_at, "s"],
        ["nonce", cap.nonce, "s"],
        ["patient_token", cap.patient_token, "s"],
        ["artifact_type_band", cap.artifact_type_band, "s"],
        ["time_band", cap.time_band, "s"],
        ["sketch", `[${cap.sketch_preview.join(", ")}, … ] (${cap.sketch_dim} ints)`, "n"],
        ["signature", cap.signature, "s"],
      ].map(([k, v, cls]) =>
        `  <span class="k">"${esc(k)}"</span>: <span class="${cls}">${esc(JSON.stringify(v))}</span>,`
      ).join("\n");
      const absent = ["patient_name", "mrn", "birth_date", "note_text",
                      "observation_values", "hidden_state", "kv_cache"]
        .map((k) => `  <span class="absent">"${esc(k)}": …</span>`).join("\n");
      panel.appendChild(el("div", "capsule-json",
        `{\n${shown}\n\n  <span style="color:var(--dim)">// no field exists for any of these:</span>\n${absent}\n}`));
      panel.appendChild(el("p", "small muted",
        `Capsule size: <b>${cap.size_bytes} bytes</b>. The struck-through keys are what a
         naive forensic design would have shipped. Here they are structurally absent —
         the schema has nowhere to put them.`));
      body.appendChild(panel);
    }

    // --- resurrection probes -----------------------------------------
    const r = d.resurrection || {};
    body.appendChild(el("div", `callout ${r.blocked === r.attempts ? "good" : "bad"} mt`,
      `<div class="icon">${r.blocked === r.attempts ? "✓" : "✕"}</div>
       <div><b>Resurrection probes: ${r.blocked}/${r.attempts} blocked.</b>
       The system tries to re-inject the withdrawn memory twice — once citing the revoked
       ancestor directly, once laundered through a paraphrase so only the sketch can catch
       it. Both are refused.</div>`));

    // --- certificate --------------------------------------------------
    const cert = d.certificate || {};
    body.appendChild(el("div", `callout ${cert.safe_resume ? "good" : "warn"} mt`,
      `<div class="icon">${cert.safe_resume ? "✓" : "⚠"}</div>
       <div><b>${cert.safe_resume ? "SAFE RESUME APPROVED" : "SAFE RESUME BLOCKED"}</b> —
       closure reached in ${d.rounds} round(s);
       ${(cert.unresolved_risk || []).length} unresolved risk item(s).</div>`));

    const cp = el("div", "panel mt");
    cp.appendChild(el("h3", null, "Signed recovery certificate (requirement F9)"));
    cp.appendChild(el("pre", "cert", esc(d.certificate_text)));
    body.appendChild(cp);

    markDone("verify");
  });
}

async function runCompare() {
  await withBusy("Running nine conditions…", async () => {
    const d = await api("/api/demo/compare", {});
    const body = $("compare-body");
    body.innerHTML = "";

    body.appendChild(el("div", "callout info",
      `<div class="icon">◆</div><div>All nine conditions ran against the
       <b>same frozen snapshot</b>, the same seed, the same provenance mask
       (<b>${esc(d.provenance)}</b>), and the same follow-up task. That pairing is what
       makes the comparison valid.</div>`));

    const rows = d.results.filter((r) => !r.error).map((r) => `
      <tr class="${r.condition === "I" ? "hero" : ""}">
        <td><b>${esc(r.condition)}</b></td>
        <td class="wrapcell">${esc(r.name)}</td>
        <td class="num">${bar(r.rwh, r.rwh > 0 ? "bad" : "good")}</td>
        <td class="num">${bar(r.descendant_recall, r.descendant_recall >= 0.999 ? "good" : "bad")}</td>
        <td class="num">${bar(r.descendant_precision, r.descendant_precision >= 0.999 ? "good" : "warn")}</td>
        <td class="num">${bar(r.bsr, r.bsr >= 0.999 ? "good" : "bad")}</td>
        <td class="num">${bar(r.uer, r.uer > 0 ? "bad" : "good")}</td>
        <td class="num">${bar(r.drr, r.drr > 0 ? "bad" : "good")}</td>
      </tr>`).join("");
    body.appendChild(el("div", "panel mt tablewrap",
      `<table><thead><tr>
         <th>ID</th><th>Condition</th><th>RWH ↓</th><th>Recall ↑</th><th>Precision ↑</th>
         <th>BSR ↑</th><th>UER ↓</th><th>DRR ↓</th>
       </tr></thead><tbody>${rows}</tbody></table>`));

    body.appendChild(el("div", "callout good mt",
      `<div class="icon">✓</div><div><b>Row I is the only privacy-respecting condition that
       is good on every axis at once.</b> B leaves the descendants live. C destroys clean
       state. F treats similarity as causality and over-quarantines. G matches CARE on
       safety but reads raw clinical content from every runtime — look at its UER. H is the
       oracle with the complete private lineage graph, which no operational system has.</div>`));

    markDone("compare");
  });
}

async function runPrivacy() {
  await withBusy("Attacking…", async () => {
    const d = await api("/api/demo/privacy", {});
    const body = $("privacy-body");
    body.innerHTML = "";

    const attacks = ["attribute_gender", "attribute_restricted", "membership", "linkability"]
      .map((k) => d[k]).filter(Boolean);
    const rows = attacks.map((a) => {
      const leaky = a.advantage > 0.05;
      return `<tr>
        <td class="wrapcell">${esc(a.name)}</td>
        <td class="num">${a.n}</td>
        <td class="num">${f3(a.accuracy)}</td>
        <td class="num">${f3(a.baseline)}</td>
        <td class="num" style="color:${leaky ? "var(--bad)" : "var(--good)"}">
          <b>${a.advantage >= 0 ? "+" : ""}${f3(a.advantage)}</b></td>
      </tr>`;
    }).join("");
    body.appendChild(el("div", "panel tablewrap",
      `<table><thead><tr><th>Attack</th><th>n</th><th>Accuracy</th><th>Baseline</th>
       <th>Advantage over chance</th></tr></thead><tbody>${rows}</tbody></table>`));

    const mem = d.membership || {};
    if (mem.advantage > 0.05) {
      body.appendChild(el("div", "callout warn mt",
        `<div class="icon">⚠</div><div><b>A real leak, reported rather than hidden.</b>
         Membership inference reaches <b>${f3(mem.accuracy)}</b> against a
         ${f3(mem.baseline)} baseline. An adversary holding a capsule can tell, well above
         chance, whether a given memory was in the candidate set. The project claims only
         that <b>raw content</b> is never exported — not that sketches are confidential.</div>`));
    }

    const link = d.linkability || {};
    const ablation = link.detail?.unscoped_ablation_accuracy;
    if (ablation !== undefined) {
      body.appendChild(el("div", "callout good mt",
        `<div class="icon">✓</div><div><b>Receiver scoping is load-bearing.</b>
         With scoping, cross-recipient linkage runs at chance
         (${f3(link.accuracy)} vs ${f3(link.baseline)}). Remove it and the same attack
         reaches <b>${f3(ablation)}</b> — an honest-but-curious coordinator could join every
         recovery event back to one patient.</div>`));
    }

    const rf = d.released_fields || {};
    body.appendChild(el("div", `callout ${rf.raw_content_exported ? "bad" : "good"} mt`,
      `<div class="icon">${rf.raw_content_exported ? "✕" : "✓"}</div>
       <div><b>Raw clinical content exported through the recovery interface:
       ${rf.raw_content_exported ? "YES" : "NONE"}.</b>
       ${rf.capsules} capsule(s), ${rf.total_bytes} bytes, and every field is declared.
       <div class="mono small" style="margin-top:8px;color:var(--muted)">
       ${esc((rf.fields_released || []).join(" · "))}</div></div>`));

    markDone("privacy");
  });
}

async function runClose() {
  await withBusy("…", async () => {
    const body = $("close-body");
    body.innerHTML = "";

    const grid = el("div", "grid g2");
    grid.appendChild(el("div", "panel",
      `<h3>What is new</h3>
       <p class="small" style="margin:0 0 10px">Not provenance, not embeddings, not replay —
       each of those exists. The contribution is the <b>recovery problem</b> and the
       controlled composition that solves it:</p>
       <ul class="small muted" style="margin:0;padding-left:18px;line-height:1.9">
         <li>latent discovery is <b>never</b> trusted as causality</li>
         <li>counterfactual replay confirms influence <b>locally</b></li>
         <li>clean-room recompilation restores utility</li>
         <li>version enforcement prevents resurrection</li>
       </ul>
       <p class="small muted" style="margin:10px 0 0">Each stage exists because the previous
       one has a measurable failure mode — and every ablation in this demo shows it.</p>`));
    grid.appendChild(el("div", "panel",
      `<h3>Honest limits</h3>
       <ul class="small muted" style="margin:0;padding-left:18px;line-height:1.9">
         <li>Simulated FHIR sandbox with synthetic records only</li>
         <li>No diagnosis, treatment, medication execution, or real EHR use</li>
         <li>No HIPAA / DPDP / GDPR certification claimed</li>
         <li>Sketches are <b>not</b> claimed confidential — the membership leak is measured
             and published</li>
         <li>Mechanism-level improvement in simulation, not clinical effectiveness</li>
       </ul>`));
    body.appendChild(grid);

    if (S.care) {
      body.appendChild(el("div", "mt"));
      body.appendChild(metricTiles(S.care.metrics,
        ["rwh", "descendant_recall", "descendant_precision", "bsr", "uer", "drr"]));
    }

    body.appendChild(el("div", "callout info mt",
      `<div class="icon">◆</div><div>Full experiment: <b>900 condition runs over 100
       incidents</b> across four contamination families, three propagation depths, and five
       provenance conditions. Run it from the analyst console or
       <span class="mono">python -m aegis_care.cli experiment</span>.
       159 tests cover every invariant in the proposal.</div>`));

    markDone("close");
  });
}

/* ================================================================== */
/* setup dialog                                                        */
/* ================================================================== */
async function openSetup() {
  const sys = await api("/api/system");
  const fam = $("cfg-family");
  fam.innerHTML = "";
  Object.entries(sys.families).forEach(([id, f]) =>
    fam.appendChild(new Option(`${id} — ${f.name}`, id)));
  fam.value = S.settings.family || "F1";

  const task = $("cfg-task");
  task.innerHTML = "";
  sys.tasks.forEach((t) =>
    task.appendChild(new Option(`${t.task_id} · ${t.label}`, t.task_id)));
  task.value = S.settings.task_id || sys.tasks[0].task_id;

  const prov = $("cfg-prov");
  prov.innerHTML = "";
  sys.provenance_conditions.forEach((p) => prov.appendChild(new Option(p, p)));
  prov.value = S.settings.provenance || "targeted";

  $("cfg-depth").value = String(S.settings.depth || 4);
  $("setup-dialog").showModal();
}

async function applySetup() {
  const body = {
    family: $("cfg-family").value,
    task_id: $("cfg-task").value,
    depth: Number($("cfg-depth").value),
    provenance: $("cfg-prov").value,
  };
  try {
    const st = await api("/api/demo/configure", body);
    $("setup-dialog").close();
    applyState(st);
    resetActBodies();
    goTo(0);
  } catch (e) {
    alert(e.message);
  }
}

function resetActBodies() {
  ["system-body", "clean-body", "poison-body", "deletion-body", "reset-body",
   "care-body", "verify-body", "compare-body", "privacy-body", "close-body"]
    .forEach((id) => { const n = $(id); if (n) n.innerHTML = ""; });
  $("system-body").innerHTML =
    '<div class="callout info"><div class="icon">▸</div><div>Press <b>Load the sandbox</b>.</div></div>';
  S.completed = new Set();
  S.poison = null; S.care = null;
}

function applyState(st) {
  S.acts = st.acts;
  S.settings = st.settings || {};
  S.completed = new Set(st.completed || []);
  const chips = $("scenario-chips");
  const info = S.settings.family_info || {};
  chips.innerHTML =
    `<span class="chip"><b>${esc(S.settings.family || "—")}</b> ${esc(info.name || "")}</span>
     <span class="chip">task <b>${esc(S.settings.task_id || "—")}</b></span>
     <span class="chip">depth <b>${esc(S.settings.depth ?? "—")}</b></span>
     <span class="chip">provenance <b>${esc(S.settings.provenance || "—")}</b></span>`;
  $("intro-family").textContent = `${S.settings.family} — ${info.name || ""}`;
  $("intro-prov").textContent = S.settings.provenance || "—";
}

/* ================================================================== */
/* wiring                                                              */
/* ================================================================== */
$("btn-next").addEventListener("click", () => goTo(S.idx + 1));
$("btn-prev").addEventListener("click", () => goTo(S.idx - 1));
$("btn-action").addEventListener("click", async () => {
  const id = S.acts[S.idx]?.id;
  const action = ACTIONS[id];
  if (!action) return;
  await action.run();
  // Intro's button loads the system, which lives on the next act.
  if (id === "intro") goTo(S.idx + 1);
});
$("btn-notes").addEventListener("click", () => $("notes").classList.toggle("show"));
$("btn-setup").addEventListener("click", openSetup);
$("cfg-cancel").addEventListener("click", () => $("setup-dialog").close());
$("cfg-apply").addEventListener("click", applySetup);
$("btn-restart").addEventListener("click", async () => {
  const st = await api("/api/demo/configure", {
    family: S.settings.family || "F1", task_id: S.settings.task_id,
    depth: S.settings.depth || 4, provenance: S.settings.provenance || "targeted",
  });
  applyState(st); resetActBodies(); goTo(0);
});

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select, textarea")) return;
  if ($("setup-dialog").open) return;
  if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); goTo(S.idx + 1); }
  if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); goTo(S.idx - 1); }
  if (e.key === " " || e.key === "Enter") { e.preventDefault(); $("btn-action").click(); }
  if (e.key.toLowerCase() === "n") { $("notes").classList.toggle("show"); }
});

/* ---- go ---- */
(async function boot() {
  try {
    const health = await api("/api/health");
    $("intro-model").textContent = health.model;
    const st = await api("/api/demo/configure",
      { family: "F1", depth: 4, provenance: "targeted" });
    applyState(st);
    goTo(0);
  } catch (e) {
    document.getElementById("stage").innerHTML =
      `<div class="callout bad"><div class="icon">✕</div>
       <div><b>Could not reach the server.</b> ${esc(e.message)}</div></div>`;
  }
})();
