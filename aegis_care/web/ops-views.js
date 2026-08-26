/* AEGIS-Care Operations Console — spread tree and access matrix.
 * Loaded after ops.js and shares its globals (api, el, esc, go, A).
 */
"use strict";

/* ================================================================== */
/* CONTAMINATION SPREAD                                                */
/* ================================================================== */
const ROLE_LANES = [
  { role: "registration", label: "Registration desk" },
  { role: "nursing", label: "Nursing" },
  { role: "clinical_summary", label: "Clinical summary" },
];

const OUTCOME_STYLE = {
  repaired:    { fill: "#edf8f2", stroke: "#158049" },
  quarantined: { fill: "#fffbeb", stroke: "#9c8109" },
  withdrawn:   { fill: "#fef2f3", stroke: "#c9314a" },
  active:      { fill: "#f0f2f5", stroke: "#98a1b0" },
};

async function pageSpread({ id }) {
  const [inc, tree] = await Promise.all([
    api("/api/ops/incidents/" + encodeURIComponent(id)),
    api("/api/ops/incidents/" + encodeURIComponent(id) + "/spread"),
  ]);
  const c = $("content");
  c.innerHTML = "";
  $("page-title").textContent = "Contamination spread";
  $("page-crumb").textContent = inc.incident_id + " · " + inc.title;

  const back = el("button", "btn sec sm", "← Back to incident");
  back.addEventListener("click", () => go("incident", { id: id }));
  $("topbar-actions").appendChild(back);

  if (!tree.nodes.length) {
    c.appendChild(el("div", "banner info",
      '<div class="ic">i</div><div><b>Nothing to show yet.</b> The spread is discovered ' +
      "during recovery. Confirm the compromised memory and run recovery, then come back — " +
      "this page will show every memory that inherited the error and what happened to it." +
      "</div>"));
    return;
  }

  const s = tree.stats;
  const tiles = el("div", "grid g4 mb");
  [
    ["Memories affected", s.affected, "including the original", s.affected ? "crit" : ""],
    ["Derivation hops", s.hops, "from origin to furthest", ""],
    ["Care roles reached", s.roles + " of 3", "crossed team boundaries",
      s.roles > 1 ? "warnv" : ""],
    ["Found by inference", s.sketch_discovered, "no recorded lineage",
      s.sketch_discovered ? "info" : ""],
  ].forEach(function (row) {
    tiles.appendChild(el("div", "stat " + row[3],
      '<div class="k">' + esc(row[0]) + '</div><div class="v">' + row[1] +
      '</div><div class="s">' + esc(row[2]) + "</div>"));
  });
  c.appendChild(tiles);

  if (s.sketch_discovered) {
    c.appendChild(el("div", "banner info mb",
      '<div class="ic">i</div><div><b>' + s.sketch_discovered + " of " + s.affected +
      " affected memories had no recorded link back to the original.</b> They were found " +
      "by matching a compressed signature of the compromised memory against each team's " +
      "own local index, then confirmed by replaying how they were written. Following " +
      "recorded provenance alone would have missed them.</div>"));
  }

  const card = el("div", "card mb");
  card.innerHTML =
    '<header><h3>How it travelled</h3>' +
    '<span class="sub">Each column is one derivation step; each row is a care role.</span>' +
    "</header>";
  const body = el("div", "body");
  const holder = el("div", "spread", '<svg id="spread-svg"></svg>');
  body.appendChild(holder);
  body.appendChild(el("div", "spread-legend",
    '<span class="item"><span class="sw" style="background:#fef2f3;border-color:#c9314a">' +
    "</span>original compromised memory</span>" +
    '<span class="item"><span class="sw" style="background:#edf8f2;border-color:#158049">' +
    "</span>rebuilt from trusted record</span>" +
    '<span class="item"><span class="sw" style="background:#fffbeb;border-color:#9c8109">' +
    "</span>held for review</span>" +
    '<span class="item"><span class="ln"></span> recorded lineage</span>' +
    '<span class="item"><span class="ln inf"></span> inferred (lineage missing)</span>'));
  card.appendChild(body);
  c.appendChild(card);

  const list = el("div", "card");
  list.innerHTML = '<header><h3>Step by step</h3><span class="sub">' +
    (tree.intended_patient.display
      ? "Intended patient: " + esc(tree.intended_patient.display) : "") +
    "</span></header>";
  const lb = el("div", "body");

  tree.nodes.forEach(function (n) {
    const cls = n.is_seed ? "seed" : n.outcome === "repaired" ? "repaired" : "";
    const item = el("div", "hopcard " + cls);
    const via = n.is_seed
      ? "this is where the error was introduced"
      : n.discovery === "latent_sketch"
        ? "found by signature match — no lineage edge existed"
        : "followed a recorded lineage edge";
    let fixed = "";
    if (n.repaired_key) {
      fixed = '<div class="hopmeta" style="color:var(--ok-700)">Rebuilt as a new version ' +
        "naming <b>" + esc(n.repaired_patient || "—") + "</b>, the correct patient.</div>";
    } else if (n.quarantine_reason) {
      fixed = '<div class="hopmeta" style="color:var(--warn-700)">Held for review: ' +
        esc(n.quarantine_reason) + "</div>";
    }
    const badges =
      (n.is_seed ? '<span class="pill sev-critical">origin</span>' : "") +
      (n.restricted ? '<span class="pill sev-critical">restricted data</span>' : "") +
      (n.influence_band && !n.is_seed
        ? '<span class="pill plain sev-' +
          (n.influence_band === "high" ? "critical" : "moderate") + '">' +
          esc(n.influence_band) + " influence</span>"
        : "");
    item.innerHTML =
      '<div class="hopnum">' + (n.is_seed ? "!" : n.depth) + "</div>" +
      '<div class="hopbody"><div class="hoptitle">' +
      esc(n.artifact_type.replace(/_/g, " ")) +
      '<span class="dim tiny">' + esc(n.role.replace(/_/g, " ")) + "</span>" +
      badges + "</div>" +
      '<div class="hopmeta">Named <b style="color:var(--danger-500)">' +
      esc(n.patient_display || n.patient_id || "—") + "</b>" +
      (n.wrong_patient ? " — the wrong patient" : "") + " · " + esc(via) + "</div>" +
      fixed + "</div>" +
      '<span class="ms ms-' + esc(n.state) + '">' + esc(n.state) + "</span>";
    lb.appendChild(item);
  });

  if (s.cleared) {
    lb.appendChild(el("div", "banner ok mt",
      '<div class="ic">✓</div><div><b>' + s.cleared + " other memories were examined and " +
      "left untouched.</b> They looked similar, but replaying how each was written showed " +
      "the error never reached them. Nothing unrelated was disturbed.</div>"));
  }
  list.appendChild(lb);
  c.appendChild(list);

  requestAnimationFrame(function () { drawSpread(tree); });
}

function drawSpread(tree) {
  const svg = $("spread-svg");
  if (!svg) return;
  const ns = "http://www.w3.org/2000/svg";
  const mk = function (tag, attrs) {
    const e = document.createElementNS(ns, tag);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  };
  svg.innerHTML = "";

  const lanes = ROLE_LANES.filter(function (l) {
    return tree.nodes.some(function (n) { return n.role === l.role; });
  });
  const maxDepth = tree.nodes.reduce(function (m, n) { return Math.max(m, n.depth); }, 0);
  const padL = 142, padR = 30, padT = 30, laneH = 96;
  const width = Math.max(svg.clientWidth || 900, 560);
  const height = padT + lanes.length * laneH + 20;
  const colW = (width - padL - padR) / Math.max(1, maxDepth + 1);
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.setAttribute("height", height);

  const pos = {};
  tree.nodes.forEach(function (n) {
    let lane = lanes.findIndex(function (l) { return l.role === n.role; });
    if (lane < 0) lane = 0;
    pos[n.key] = {
      x: padL + n.depth * colW + colW / 2,
      y: padT + lane * laneH + laneH / 2,
    };
  });

  lanes.forEach(function (l, i) {
    const y = padT + i * laneH;
    svg.appendChild(mk("rect", { x: 0, y: y, width: width, height: laneH,
      fill: i % 2 ? "transparent" : "rgba(20,26,34,.018)" }));
    svg.appendChild(mk("line", { x1: 0, y1: y, x2: width, y2: y,
      stroke: "#e7eaef", "stroke-width": 1 }));
    const t = mk("text", { x: 14, y: y + laneH / 2 + 4, fill: "#6b7688",
      "font-size": 12, "font-weight": 600 });
    t.textContent = l.label;
    svg.appendChild(t);
  });
  svg.appendChild(mk("line", { x1: padL - 16, y1: padT, x2: padL - 16,
    y2: padT + lanes.length * laneH, stroke: "#dde1e8", "stroke-width": 1 }));

  for (let d = 0; d <= maxDepth; d++) {
    const t = mk("text", { x: padL + d * colW + colW / 2, y: 18, fill: "#98a1b0",
      "font-size": 10.5, "text-anchor": "middle", "font-weight": 700 });
    t.textContent = d === 0 ? "ORIGIN" : "STEP " + d;
    svg.appendChild(t);
  }

  const defs = mk("defs", {});
  ["#2f6fdb", "#c9314a"].forEach(function (colour, i) {
    const m = mk("marker", { id: "ah" + i, viewBox: "0 0 8 8", refX: 7, refY: 4,
      markerWidth: 7, markerHeight: 7, orient: "auto" });
    m.appendChild(mk("path", { d: "M0 0 L8 4 L0 8 z", fill: colour }));
    defs.appendChild(m);
  });
  svg.appendChild(defs);

  tree.edges.forEach(function (e, i) {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const inferred = e.kind === "inferred";
    const colour = inferred ? "#c9314a" : "#2f6fdb";
    const midX = (a.x + b.x) / 2;
    const path = mk("path", {
      d: "M " + (a.x + 26) + " " + a.y + " C " + midX + " " + a.y + ", " +
         midX + " " + b.y + ", " + (b.x - 29) + " " + b.y,
      fill: "none", stroke: colour, "stroke-width": inferred ? 1.8 : 2,
      opacity: 0, "marker-end": "url(#ah" + (inferred ? 1 : 0) + ")",
    });
    if (inferred) path.setAttribute("stroke-dasharray", "6,5");
    svg.appendChild(path);
    path.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: 420, delay: 160 + i * 120, fill: "forwards", easing: "ease-out" });
  });

  tree.nodes.forEach(function (n, i) {
    const p = pos[n.key];
    const style = n.is_seed
      ? { fill: "#fef2f3", stroke: "#c9314a" }
      : (OUTCOME_STYLE[n.outcome] || OUTCOME_STYLE.active);
    const g = mk("g", { opacity: 0 });

    if (n.is_seed) {
      g.appendChild(mk("circle", { cx: p.x, cy: p.y, r: 28, fill: "#c9314a", opacity: .09 }));
    }
    g.appendChild(mk("circle", { cx: p.x, cy: p.y, r: 21, fill: style.fill,
      stroke: style.stroke, "stroke-width": n.is_seed ? 2.5 : 2 }));

    const icon = mk("text", { x: p.x, y: p.y + 4.5, "text-anchor": "middle",
      "font-size": 13, fill: style.stroke, "font-weight": 700 });
    icon.textContent = n.is_seed ? "!" : n.outcome === "repaired" ? "✓"
      : n.outcome === "quarantined" ? "?" : "•";
    g.appendChild(icon);

    const label = mk("text", { x: p.x, y: p.y + 38, "text-anchor": "middle",
      "font-size": 10.5, fill: "#39424f", "font-weight": 600 });
    label.textContent = n.artifact_type.replace(/_/g, " ");
    g.appendChild(label);

    const pt = mk("text", { x: p.x, y: p.y - 30, "text-anchor": "middle",
      "font-size": 9.5, fill: n.wrong_patient ? "#c9314a" : "#98a1b0" });
    pt.textContent = n.patient_id || "";
    g.appendChild(pt);

    const title = mk("title", {});
    title.textContent = n.artifact_type + " (" + n.role + ")\npatient: " +
      (n.patient_display || n.patient_id) + "\nstate: " + n.state +
      "\nfound via: " + n.discovery;
    g.appendChild(title);

    svg.appendChild(g);
    g.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: 320, delay: i * 130, fill: "forwards", easing: "ease-out" });
  });
}

/* ================================================================== */
/* YOUR ACCESS                                                         */
/* ================================================================== */
const PERM_LABELS = {
  report_incident: "Report a memory safety issue",
  view_worklist: "See the incident worklist",
  view_incident: "Open an incident",
  triage_incident: "Set severity and assign ownership",
  confirm_seed: "Confirm which memory is compromised",
  run_recovery: "Run recovery",
  review_quarantine: "Decide on memory held for review",
  view_patient_memory: "View what agents remember about a patient",
  view_audit: "Read the audit trail",
  export_evidence: "Export an evidence pack",
  close_incident: "Close or dismiss an incident",
  manage_operators: "Manage operator accounts",
};

async function pageAccess() {
  const c = $("content");
  c.innerHTML = "";

  c.appendChild(el("div", "banner info mb",
    '<div class="ic">i</div><div>You are signed in as <b>' + esc(A.me.display_name) +
    "</b> (" + esc(A.me.role_label) + "). Permissions are enforced by the server, not by " +
    "hiding buttons — a request you are not entitled to make is refused even when sent " +
    "straight to the API.</div>"));

  const mine = el("div", "card mb");
  mine.innerHTML = "<header><h3>What you can do</h3></header>";
  const mb = el("div", "body");
  const grid = el("div", "grid g2");

  const yes = el("div", "rolecard me");
  yes.innerHTML = "<h5>Permitted</h5><div class=\"who\">" + esc(A.me.role_label) +
    "</div><ul>" + A.me.permissions.map(function (p) {
      return "<li>" + esc(PERM_LABELS[p] || p.replace(/_/g, " ")) + "</li>";
    }).join("") + "</ul>";
  grid.appendChild(yes);

  const denied = Object.keys(PERM_LABELS).filter(function (p) {
    return A.me.permissions.indexOf(p) < 0;
  });
  const no = el("div", "rolecard");
  no.innerHTML = "<h5>Not available to you</h5>" +
    "<div class=\"who\">Handled by another role</div>" +
    "<ul class=\"no\">" + (denied.length
      ? denied.map(function (p) { return "<li>" + esc(PERM_LABELS[p]) + "</li>"; }).join("")
      : "<li>Nothing — you hold every permission.</li>") + "</ul>";
  grid.appendChild(no);
  mb.appendChild(grid);
  mine.appendChild(mb);
  c.appendChild(mine);

  const rp = A.meta.role_permissions || {};
  const matrix = el("div", "card");
  matrix.innerHTML = "<header><h3>Who can do what</h3>" +
    '<span class="sub">Separation of duties across the care team</span></header>';
  const body = el("div", "body tight");
  const wrap = el("div", "tablewrap");
  const t = el("table", "matrix");
  const roles = A.meta.roles;
  t.innerHTML = "<thead><tr><th>Permission</th>" + roles.map(function (r) {
    return '<th style="text-align:center">' + esc(r.label) + "</th>";
  }).join("") + "</tr></thead>";
  const tb = el("tbody");
  Object.keys(PERM_LABELS).forEach(function (perm) {
    const tr = el("tr");
    tr.innerHTML = '<td class="perm">' + esc(PERM_LABELS[perm]) + "</td>" +
      roles.map(function (r) {
        const has = (rp[r.role] || []).indexOf(perm) >= 0;
        return '<td class="' + (has ? "yes" : "no") + '">' + (has ? "●" : "—") + "</td>";
      }).join("");
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  wrap.appendChild(t);
  body.appendChild(wrap);
  matrix.appendChild(body);
  c.appendChild(matrix);
}
