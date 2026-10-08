// Payment Integrity Analyst console. Vanilla ES module, no build step.

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const tooltip = $("#tooltip");

const state = {
  role: "analyst",
  meta: null,
  run: null,
  runs: [],
  policy: null,      // last policy result
  outline: null,
  activeSection: null,
  evals: null,
  cases: null,
  caseFilters: { q: "", status: "open", severity: "all", rule: "all", investigator: "all" },
  caseSort: { key: "severity", asc: false },
  auditFilter: "all",
  pendingAsk: null,
};
try { state.role = localStorage.getItem("pia-role") || "analyst"; } catch (e) {}

const NODES = [
  ["understand", "Understand", "plan + catalog"],
  ["guard", "Guard", "SQL policy"],
  ["approve", "Approve", "SIU lead"],
  ["execute", "Execute", "read-only"],
  ["compose", "Compose", "narrate rows"],
  ["verify", "Verify", "groundedness"],
];

const ICON = {
  ok: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  error: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  pending: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 5v3.2l2 1.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  stop: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="4.5" y="4.5" width="7" height="7" rx="1.5" fill="currentColor"/></svg>',
  skipped: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>',
  lock: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>',
  shield: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 1.8l5 2v4.1c0 3-2.2 5.1-5 6.3-2.8-1.2-5-3.3-5-6.3V3.8z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  info: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 7.2v4M8 4.8v.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  copy: '<svg viewBox="0 0 16 16" width="14" height="14"><rect x="5" y="5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M3 10.5V3.8C3 3.4 3.4 3 3.8 3h6.7" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
  play: '<svg viewBox="0 0 16 16" width="14" height="14"><path d="M5 3.5l8 4.5-8 4.5z" fill="currentColor"/></svg>',
};

// ------------------------------------------------------------------ utils --
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "content-type": "application/json" }, ...opts });
  let body = null;
  try { body = await res.json(); } catch (e) {}
  if (!res.ok) {
    const msg = body?.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return body;
}

function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
function fmt(v, col = "") {
  if (v === null || v === undefined) return '<span class="muted">null</span>';
  if (!isNum(v)) return esc(v);
  const c = col.toLowerCase();
  if ((c.includes("rate") || c.includes("share")) && v >= 0 && v <= 1) return (v * 100).toFixed(1) + "%";
  if (Number.isInteger(v)) return v.toLocaleString();
  const money = /(amount|billed|paid|overpaid|recovery|total|usd)/.test(c);
  return v.toLocaleString(undefined, { minimumFractionDigits: money ? 2 : 0, maximumFractionDigits: money ? 2 : 2 });
}
const fmtPlain = (v, col) => fmt(v, col).replace(/<[^>]+>/g, "");

function timeAgo(ts) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

const STATUS = {
  done: ["pill-good", "ok", "Done"],
  refused: ["pill-bad", "shield", "Refused"],
  needs_clarification: ["pill-warn", "info", "Needs clarification"],
  awaiting_approval: ["pill-warn", "pending", "Awaiting SIU lead"],
  rejected: ["pill-bad", "error", "Rejected"],
  failed: ["pill-bad", "error", "Failed"],
  running: ["pill-info", "pending", "Running"],
};
function statusPill(status) {
  const [cls, icon, label] = STATUS[status] || ["", "info", status];
  return `<span class="pill ${cls}">${ICON[icon]}${esc(label)}</span>`;
}
const roleLabel = (r) => (r === "siu_lead" ? "SIU lead" : "Analyst");

// --------------------------------------------------------------- tooltips --
function bindTooltips(root) {
  root.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("pointerenter", () => { tooltip.innerHTML = el.dataset.tip; tooltip.hidden = false; });
    el.addEventListener("pointermove", (e) => {
      const pad = 14, w = tooltip.offsetWidth, h = tooltip.offsetHeight;
      let x = e.clientX + pad, y = e.clientY + pad;
      if (x + w > innerWidth - 8) x = e.clientX - w - pad;
      if (y + h > innerHeight - 8) y = e.clientY - h - pad;
      tooltip.style.left = x + "px"; tooltip.style.top = y + "px";
    });
    el.addEventListener("pointerleave", () => { tooltip.hidden = true; });
  });
}

// ----------------------------------------------------------- SQL highlight --
const KW = /\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|HAVING|LIMIT|AS|AND|OR|NOT|IN|IS|NULL|CASE|WHEN|THEN|ELSE|END|WITH|DISTINCT|BETWEEN|DESC|ASC|UNION|ALL|LIKE|USING)\b/gi;
const FN = /\b(COUNT|SUM|AVG|MIN|MAX|CAST|ROUND|julianday|COALESCE|REAL)\b/gi;
function highlightSQL(sql) {
  const parts = String(sql || "").split(/('(?:[^']|'')*')/g);
  return parts.map((p, i) => {
    if (i % 2) return `<span class="s">${esc(p)}</span>`;
    return esc(p)
      .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="n">$1</span>')
      .replace(FN, (m) => `<span class="f">${m}</span>`)
      .replace(KW, (m) => `<span class="k">${m.toUpperCase()}</span>`);
  }).join("");
}
function prettySQL(sql) {
  return String(sql || "").replace(/\s+(FROM|WHERE|JOIN|LEFT JOIN|GROUP BY|ORDER BY|HAVING|LIMIT|SELECT(?= (?!DISTINCT)))\b/g, "\n$1")
    .replace(/^\n/, "");
}

// ------------------------------------------------------------------ charts --
// Single-series charts use series-1; the retrieval comparison uses slots 1 and 2.
// Bars: <=24px thick, 4px rounded data-end, square at the baseline, 2px gap, hover tooltip.
function barPath(x0, y, w, h, r = 4) {
  if (w <= 0) return "";
  r = Math.min(r, w, h / 2);
  return `M${x0},${y} H${x0 + w - r} Q${x0 + w},${y} ${x0 + w},${y + r} V${y + h - r} Q${x0 + w},${y + h} ${x0 + w - r},${y + h} H${x0} Z`;
}

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

function hBarChart({ labels, values, colLabel, valueCol, width = 720, title = true }) {
  const labelW = Math.min(220, Math.max(70, Math.max(...labels.map((l) => String(l).length)) * 7.2 + 12));
  const barH = Math.min(24, 22), gap = 10, top = 22, padR = 70;
  const h = top + labels.length * (barH + gap) + 6;
  const max = niceMax(Math.max(...values, 0));
  const plotW = width - labelW - padR;
  const x = (v) => labelW + (v / max) * plotW;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const showAll = labels.length <= 8;
  const maxIdx = values.indexOf(Math.max(...values));
  let g = `<g class="grid">${ticks.map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${top - 6}" y2="${h}"/>`).join("")}</g>`;
  g += ticks.map((t) => `<text x="${x(t)}" y="${top - 10}" text-anchor="middle">${esc(fmtPlain(t, valueCol))}</text>`).join("");
  labels.forEach((l, i) => {
    const y = top + i * (barH + gap);
    const w = Math.max(0, x(values[i]) - labelW);
    const tip = `<b>${esc(l)}</b><br>${esc(valueCol)}: ${esc(fmtPlain(values[i], valueCol))}`;
    g += `<text x="${labelW - 10}" y="${y + barH / 2 + 4}" text-anchor="end">${esc(String(l).length > 30 ? String(l).slice(0, 29) + "…" : l)}</text>`;
    g += `<path class="bar" d="${barPath(labelW, y, w, barH)}"/>`;
    if (showAll || i === maxIdx) g += `<text class="val" x="${labelW + w + 6}" y="${y + barH / 2 + 4}">${esc(fmtPlain(values[i], valueCol))}</text>`;
    g += `<rect class="hit" x="0" y="${y - gap / 2}" width="${width}" height="${barH + gap}" data-tip="${esc(tip)}"/>`;
  });
  g += `<line class="axis" x1="${labelW}" x2="${labelW}" y1="${top - 6}" y2="${h}" stroke="var(--border-strong)"/>`;
  return `<div class="chart">${title ? `<div class="chart-title">${esc(valueCol)} by ${esc(colLabel)}</div>` : ""}<svg viewBox="0 0 ${width} ${h}" role="img" aria-label="Bar chart of ${esc(valueCol)} by ${esc(colLabel)}">${g}</svg></div>`;
}

function lineChart({ labels, values, colLabel, valueCol, width = 720, height = 240, title = true }) {
  const padL = 56, padR = 24, top = 18, bottom = 28;
  const max = niceMax(Math.max(...values, 0));
  const plotW = width - padL - padR, plotH = height - top - bottom;
  const x = (i) => padL + (labels.length === 1 ? plotW / 2 : (i / (labels.length - 1)) * plotW);
  const y = (v) => top + plotH - (v / max) * plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  let g = `<g class="grid">${ticks.map((t) => `<line x1="${padL}" x2="${width - padR}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>`;
  g += ticks.map((t) => `<text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end">${esc(fmtPlain(t, valueCol))}</text>`).join("");
  g += labels.map((l, i) => `<text x="${x(i)}" y="${height - 8}" text-anchor="middle">${esc(l)}</text>`).join("");
  g += `<path class="line" d="${values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ")}"/>`;
  const maxIdx = values.indexOf(Math.max(...values));
  const lastIdx = values.length - 1;
  values.forEach((v, i) => {
    g += `<circle class="marker" cx="${x(i)}" cy="${y(v)}" r="4.5"/>`;
    if (i === maxIdx || i === lastIdx) g += `<text class="val" x="${x(i)}" y="${y(v) - 10}" text-anchor="middle">${esc(fmtPlain(v, valueCol))}</text>`;
  });
  const band = labels.length > 1 ? plotW / (labels.length - 1) : plotW;
  labels.forEach((l, i) => {
    const tip = `<b>${esc(l)}</b><br>${esc(valueCol)}: ${esc(fmtPlain(values[i], valueCol))}`;
    g += `<rect class="hit" data-cross="${x(i)}" x="${x(i) - band / 2}" y="${top}" width="${band}" height="${plotH}" data-tip="${esc(tip)}"/>`;
  });
  g += `<line class="cross" id="cross" x1="0" x2="0" y1="${top}" y2="${top + plotH}" visibility="hidden"/>`;
  return `<div class="chart">${title ? `<div class="chart-title">${esc(valueCol)} by ${esc(colLabel)}</div>` : ""}<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Line chart of ${esc(valueCol)} over ${esc(colLabel)}">${g}</svg></div>`;
}

function bindCrosshair(root) {
  root.querySelectorAll("svg").forEach((svg) => {
    const cross = svg.querySelector("#cross");
    if (!cross) return;
    svg.querySelectorAll("[data-cross]").forEach((r) => {
      r.addEventListener("pointerenter", () => { cross.setAttribute("x1", r.dataset.cross); cross.setAttribute("x2", r.dataset.cross); cross.setAttribute("visibility", "visible"); });
      r.addEventListener("pointerleave", () => cross.setAttribute("visibility", "hidden"));
    });
  });
}

function autoChart(columns, rows) {
  if (!rows.length || rows.length > 40 || columns.length < 2) return null;
  const numIdx = columns.findIndex((c, i) => i > 0 && rows.every((r) => isNum(r[i]) || r[i] === null));
  if (numIdx < 0) return null;
  const labelIdx = columns.findIndex((c, i) => i !== numIdx && rows.every((r) => !isNum(r[i])));
  if (labelIdx < 0) return null;
  const labels = rows.map((r) => r[labelIdx] ?? "null");
  const values = rows.map((r) => r[numIdx] ?? 0);
  const spec = { labels, values, colLabel: columns[labelIdx], valueCol: columns[numIdx] };
  return labels.every((l) => /^\d{4}-\d{2}$/.test(l)) && labels.length > 1 ? lineChart(spec) : hBarChart(spec);
}

// ------------------------------------------------------------------- table --
function dataTable(columns, rows, id) {
  const numeric = columns.map((_, i) => rows.length && rows.every((r) => isNum(r[i]) || r[i] === null));
  return `<div class="table-wrap"><table class="data" id="${id}"><thead><tr>${columns.map((c, i) =>
    `<th class="${numeric[i] ? "num" : ""}" data-col="${i}" scope="col">${esc(c)}<span class="dir"></span></th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((v, i) => `<td class="${numeric[i] ? "num" : ""}">${fmt(v, columns[i])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function bindSort(table, columns, rows) {
  if (!table) return;
  let sortCol = -1, asc = true;
  table.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
    const i = +th.dataset.col;
    asc = sortCol === i ? !asc : true; sortCol = i;
    const sorted = [...rows].sort((a, b) => (a[i] === b[i] ? 0 : (a[i] > b[i] ? 1 : -1)) * (asc ? 1 : -1));
    table.querySelector("tbody").innerHTML = sorted.map((r) => `<tr>${r.map((v, j) => `<td class="${table.querySelectorAll("th")[j].classList.contains("num") ? "num" : ""}">${fmt(v, columns[j])}</td>`).join("")}</tr>`).join("");
    table.querySelectorAll(".dir").forEach((d, j) => (d.textContent = j === i ? (asc ? "▲" : "▼") : ""));
  }));
}

// ---------------------------------------------------------------- shell UI --
function setRole(role) {
  state.role = role;
  try { localStorage.setItem("pia-role", role); } catch (e) {}
  syncIdentity();
  route();
}
function syncIdentity() {
  document.querySelectorAll("#roleSwitch button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.role === state.role)));
  const lead = state.role === "siu_lead";
  $("#avatar").textContent = lead ? "SL" : "AN";
  $("#avatar").style.background = lead ? "#7a4b00" : "#1f5874";
  $("#userName").textContent = lead ? "SIU lead workspace" : "Analyst workspace";
  $("#userRole").textContent = lead ? "Approves sensitive queries" : "Minimum-necessary access";
}
document.querySelectorAll("#roleSwitch button").forEach((b) => b.addEventListener("click", () => setRole(b.dataset.role)));
$("#themeBtn").addEventListener("click", () => {
  const cur = document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("pia-theme", next); } catch (e) {}
});

async function refreshQueue() {
  try {
    const q = await api("/runs?status=awaiting_approval");
    const b = $("#queueBadge");
    b.textContent = q.length; b.hidden = q.length === 0;
    return q;
  } catch (e) { return []; }
}

// --------------------------------------------------------------- ASK view --
function stepper(run) {
  const subs = {};
  for (const e of run.events) subs[e.node] = e;
  return `<div class="stepper" role="list" aria-label="Pipeline">${NODES.map(([key, label, hint]) => {
    const st = run.pipeline[key] || "skipped";
    const ev = subs[key];
    const tip = ev ? `<b>${esc(label)}</b> · ${esc(st)}<br>${esc(ev.detail || hint)}` : `<b>${esc(label)}</b><br>${st === "skipped" ? "not reached" : esc(hint)}`;
    return `<div class="step ${st}" role="listitem" data-tip="${esc(tip)}"><span class="dot">${ICON[st] || ICON.skipped}</span>${esc(label)}<span class="sub">${esc(st === "skipped" ? hint : st)}</span></div>`;
  }).join("")}</div>`;
}

function timeline(run) {
  return `<ol class="timeline">${run.events.map((e) =>
    `<li class="${e.outcome}"><span class="node">${esc(e.node)}</span><span class="attempt">attempt ${e.attempt}</span>
      ${e.detail ? `<div class="detail">${esc(e.detail)}</div>` : ""}</li>`).join("")}</ol>`;
}

function answerBlock(run) {
  if (run.status === "refused") return `<div class="answer refused"><div class="label">${ICON.shield} Refused</div>${esc(run.answer)}</div>`;
  if (run.status === "needs_clarification") return `<div class="answer clarify"><div class="label">${ICON.info} Clarifying question</div>${esc(run.answer)}</div>`;
  if (run.status === "awaiting_approval") return `<div class="answer clarify"><div class="label">${ICON.pending} Held for SIU-lead approval</div>The guard validated this query against a sensitive table. Nothing has run yet; review the SQL below.</div>`;
  if (run.status === "rejected") return `<div class="answer refused"><div class="label">${ICON.error} Rejected by reviewer</div>The query was not executed.</div>`;
  if (run.status === "failed") return `<div class="answer refused"><div class="label">${ICON.error} Failed</div>${esc(run.error || "")}</div>`;
  return `<div class="answer"><div class="label">${ICON.ok} Answer</div>${esc(run.answer)}</div>`;
}

function runCard(run) {
  const meta = [
    statusPill(run.status),
    `<span class="pill">${esc(roleLabel(run.role))}</span>`,
    run.grounded === true ? `<span class="pill pill-good" data-tip="Every number in the answer exists in the result rows">${ICON.ok}Grounded</span>` : "",
    run.grounded === false ? `<span class="pill pill-bad">${ICON.error}Ungrounded</span>` : "",
    `<span class="pill" data-tip="Plan attempts, including retries after guard or SQL errors">${run.attempts} attempt${run.attempts === 1 ? "" : "s"}</span>`,
    run.status === "done" ? `<span class="pill">${run.row_count} row${run.row_count === 1 ? "" : "s"}</span>` : "",
    run.execute_ms != null ? `<span class="pill">${run.execute_ms} ms</span>` : "",
  ].join("");

  const approval = run.status === "awaiting_approval" ? (state.role === "siu_lead"
    ? `<div class="approval"><div class="what"><b>Approve this query?</b><div class="secondary">It reads <code>${esc(run.tables.join(", "))}</code>. Your decision is recorded in the run trace.</div></div>
        <label class="muted" for="reviewer">Reviewer</label><input id="reviewer" value="lead_2" autocomplete="off">
        <button class="btn btn-danger" data-decide="false">Reject</button><button class="btn btn-good" data-decide="true">${ICON.ok} Approve and run</button></div>`
    : `<div class="notice">${ICON.info}<div>Waiting for an SIU lead. Switch the role at the top right to review it, or open <a href="#/approvals">Approvals</a>.</div></div>`) : "";

  const facts = [];
  if (run.intent) facts.push(`<div><h3>Interpretation</h3><div class="secondary" style="margin-top:6px">${esc(run.intent)}</div></div>`);
  if (run.assumptions?.length) facts.push(`<div><h3>Assumptions</h3><ul>${run.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>`);
  if (run.caveats?.length) facts.push(`<div><h3>Caveats</h3><ul>${run.caveats.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>`);

  const chart = run.status === "done" ? autoChart(run.columns, run.rows) : null;
  const tabs = [];
  if (run.status === "done") tabs.push(["results", `Results (${run.row_count})`]);
  if (run.sql) tabs.push(["sql", "SQL"]);
  tabs.push(["timeline", "Timeline"], ["trace", "Raw trace"]);
  const first = tabs[0][0];

  return `<article class="card card-pad" id="runCard">
    <div class="run-head"><div><div class="run-q">${esc(run.question)}</div><div class="run-meta">${meta}</div></div>
      <span class="muted mono" title="Run id">${esc(run.run_id)}</span></div>
    ${stepper(run)}
    <div style="margin-top:14px">${answerBlock(run)}</div>
    ${approval}
    ${facts.length ? `<div class="facts">${facts.join("")}</div>` : ""}
    <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${k === first}">${esc(l)}</button>`).join("")}</div>
    <div class="tabpanel" data-panel="results" ${first === "results" ? "" : "hidden"}>
      ${run.status === "done" ? (run.rows.length ? `${chart ? `<div class="row" style="margin-bottom:10px"><div class="segmented" role="tablist" aria-label="Result view"><button role="tab" data-rv="chart" aria-selected="true">Chart</button><button role="tab" data-rv="table" aria-selected="false">Table</button></div></div><div data-rvp="chart">${chart}</div><div data-rvp="table" hidden>${dataTable(run.columns, run.rows, "resultTable")}</div>` : dataTable(run.columns, run.rows, "resultTable")}` : `<div class="empty">The query returned no rows.</div>`) : ""}
    </div>
    <div class="tabpanel" data-panel="sql" ${first === "sql" ? "" : "hidden"}>
      <div class="sql-wrap"><pre class="sql">${highlightSQL(prettySQL(run.sql))}</pre><button class="btn btn-sm copy" data-copy>${ICON.copy} Copy</button></div>
      <p class="muted" style="font-size:12.5px;margin:10px 0 0">${run.status === "refused" ? "This is the plan the guard rejected. It never reached the database." : "This is the SQL the guard returned and the executor ran, with the row limit applied."}</p>
    </div>
    <div class="tabpanel" data-panel="timeline" ${first === "timeline" ? "" : "hidden"}>${timeline(run)}</div>
    <div class="tabpanel" data-panel="trace" hidden><pre class="sql">${esc(run.trace.join("\n"))}</pre></div>
  </article>`;
}

function bindRunCard(root, run) {
  root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
    root.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    root.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== b.dataset.tab));
  }));
  root.querySelectorAll("[data-rv]").forEach((b) => b.addEventListener("click", () => {
    root.querySelectorAll("[data-rv]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    root.querySelectorAll("[data-rvp]").forEach((p) => (p.hidden = p.dataset.rvp !== b.dataset.rv));
  }));
  const copy = root.querySelector("[data-copy]");
  if (copy) copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(run.sql); toast("SQL copied"); } catch (e) { toast("Copy failed", true); }
  });
  root.querySelectorAll("[data-decide]").forEach((b) => b.addEventListener("click", () =>
    decide(run.run_id, b.dataset.decide === "true", $("#reviewer")?.value || "siu_lead")));
  bindSort(root.querySelector("#resultTable"), run.columns, run.rows);
  bindTooltips(root);
  bindCrosshair(root);
}

async function decide(runId, approved, reviewer) {
  try {
    const run = await api(`/runs/${runId}/decision`, { method: "POST", body: JSON.stringify({ approved, reviewer, reviewer_role: state.role }) });
    toast(approved ? "Approved. Query executed." : "Rejected. Query not executed.");
    await refreshQueue();
    if (location.hash.startsWith("#/approvals")) renderApprovals();
    else { state.run = run; renderAsk(); }
  } catch (e) { toast(e.message, true); }
}

async function ask(question) {
  const q = question.trim();
  if (q.length < 3) { toast("Type a question first", true); return; }
  const btn = $("#askBtn");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Running';
  $("#runSlot").innerHTML = `<div class="card card-pad"><div class="skeleton" style="width:60%"></div><div class="skeleton" style="width:90%;margin-top:14px"></div><div class="skeleton" style="width:75%;margin-top:10px"></div></div>`;
  try {
    state.run = await api("/ask", { method: "POST", body: JSON.stringify({ question: q, role: state.role }) });
    await refreshQueue();
    renderAsk();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false; btn.innerHTML = `${ICON.play} Ask`;
    $("#runSlot").innerHTML = "";
  }
}

async function renderAsk() {
  const examples = (state.meta?.examples || []).filter((e) => e.role === state.role || (state.role === "siu_lead" && e.role === "analyst"));
  const runs = await api("/runs?limit=30").catch(() => []);
  view.innerHTML = `
    <div class="page-head"><div><h1>Ask the payment-integrity mart</h1>
      <p>The model plans the query, a deterministic guard decides whether it may run, SQLite computes the numbers, and a verifier checks every number in the answer. PHI columns cannot be queried by any role.</p></div></div>
    <div class="ask-grid">
      <div>
        <div class="card card-pad composer">
          <label for="q" class="muted" style="font-size:12px;font-weight:600">Question · acting as ${esc(roleLabel(state.role))}</label>
          <textarea id="q" placeholder="e.g. What is the false positive rate of closed cases by rule?" rows="2"></textarea>
          <div class="composer-foot"><span class="muted"><span class="kbd">Ctrl</span> + <span class="kbd">Enter</span> to run · <span class="kbd">/</span> to focus</span>
            <button class="btn btn-primary" id="askBtn">${ICON.play} Ask</button></div>
        </div>
        <div class="examples" aria-label="Example questions">${examples.map((e) =>
          `<button class="chip" data-example="${esc(e.question)}">${e.role === "siu_lead" ? '<span class="role-tag">SIU</span>' : ""}${esc(e.question)}</button>`).join("")}</div>
        <div id="runSlot" style="margin-top:18px">${state.run ? runCard(state.run) : `<div class="card"><div class="empty">${ICON.info}<div>Pick an example or ask your own question.<br>Try the medical-record-number question to watch the PHI guard reject the plan.</div></div></div>`}</div>
      </div>
      <aside class="card card-pad history" aria-label="Run history">
        <div class="row"><h2>History</h2><span class="spacer"></span><span class="muted" style="font-size:12px">${runs.length} run${runs.length === 1 ? "" : "s"}</span></div>
        ${runs.length ? `<ol>${runs.map((r) => `<li><button data-run="${esc(r.run_id)}" aria-current="${state.run?.run_id === r.run_id}">
            <span class="q">${esc(r.question)}</span><span class="m">${statusPill(r.status)}<span>${esc(roleLabel(r.role))}</span><span>${timeAgo(r.created_at)}</span></span></button></li>`).join("")}</ol>`
          : `<div class="empty" style="padding:24px 0">No runs yet.</div>`}
      </aside>
    </div>`;
  const ta = $("#q");
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ask(ta.value); } });
  $("#askBtn").addEventListener("click", () => ask(ta.value));
  view.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => { ta.value = b.dataset.example; ask(b.dataset.example); }));
  view.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", async () => {
    try { state.run = await api(`/runs/${b.dataset.run}`); renderAsk(); } catch (e) { toast(e.message, true); }
  }));
  if (state.run) bindRunCard($("#runCard"), state.run);
  if (state.pendingAsk) { const q = state.pendingAsk; state.pendingAsk = null; ta.value = q; ask(q); }
  bindTooltips(view);
}

// --------------------------------------------------------- APPROVALS view --
async function renderApprovals() {
  view.innerHTML = `<div class="page-head"><div><h1>SIU-lead approvals</h1><p>Queries against sensitive tables pause at a checkpoint after the guard validates them. Nothing runs until an SIU lead approves. Decisions are written to the run trace.</p></div></div><div id="queue"><div class="skeleton" style="width:50%"></div></div>`;
  const q = await refreshQueue();
  const notice = state.role !== "siu_lead"
    ? `<div class="notice" style="margin-bottom:14px">${ICON.lock}<div>You are acting as an analyst, so decisions are disabled. Switch to <b>SIU lead</b> at the top right to approve or reject.</div></div>` : "";
  if (!q.length) {
    $("#queue").innerHTML = `${notice}<div class="card"><div class="empty">${ICON.ok}<div>The queue is empty.<br>As SIU lead, ask “Show the investigator notes for open cases.” to create one.</div></div></div>`;
    return;
  }
  const disabled = state.role !== "siu_lead" ? "disabled" : "";
  $("#queue").innerHTML = `${notice}<div class="queue">${q.map((r) => `
    <article class="card card-pad queue-item">
      <div class="head"><div><div class="run-q">${esc(r.question)}</div>
        <div class="run-meta">${statusPill(r.status)}<span class="pill">requested by ${esc(roleLabel(r.role))}</span><span class="pill">${timeAgo(r.created_at)}</span>${r.tables.map((t) => `<span class="pill pill-info">${esc(t)}</span>`).join("")}</div></div>
        <span class="muted mono">${esc(r.run_id)}</span></div>
      <pre class="sql">${highlightSQL(prettySQL(r.sql))}</pre>
      <div class="actions"><label class="muted" for="rv-${esc(r.run_id)}">Reviewer</label><input id="rv-${esc(r.run_id)}" value="lead_2" ${disabled}>
        <span class="spacer"></span>
        <button class="btn btn-danger" data-id="${esc(r.run_id)}" data-ok="false" ${disabled}>Reject</button>
        <button class="btn btn-good" data-id="${esc(r.run_id)}" data-ok="true" ${disabled}>${ICON.ok} Approve and run</button></div>
    </article>`).join("")}</div>`;
  view.querySelectorAll("[data-id]").forEach((b) => b.addEventListener("click", () =>
    decide(b.dataset.id, b.dataset.ok === "true", $(`#rv-${b.dataset.id}`)?.value || "siu_lead")));
}

// ------------------------------------------------------------ POLICY view --
const POLICY_EXAMPLES = [
  "A claim was paid twice after a portal resubmission. Can we auto-recover, and when does an investigator need to approve?",
  "A vendor emailed new bank details six days before a 48,000 USD invoice. What must happen before payment?",
  "Two panel component codes were billed separately on the same day. What is the disposition?",
  "Which member fields may an analyst see, and what can be sent to the model?",
];

async function renderPolicy() {
  if (!state.outline) state.outline = await api("/policy/outline");
  const res = state.policy;
  const order = {};
  (res?.sections_read || []).forEach((s, i) => (order[s] = i + 1));
  const outline = state.outline.sections.map((s) => `<li class="l${s.level} ${order[s.id] ? "read" : ""} ${state.activeSection === s.id ? "active" : ""}">
      <button data-sec="${esc(s.id)}"><span class="sid">${esc(s.id)}</span><span>${esc(s.title)}</span>${order[s.id] ? `<span class="order" title="Read step ${order[s.id]}">${order[s.id]}</span>` : ""}</button></li>`).join("");

  let path = "";
  if (res) {
    path = `<div class="path" aria-label="Reading path">${res.tool_calls.map((c, i) => {
      const hop = c.name === "search"
        ? `<span class="hop search" data-tip="${esc("search → " + c.result)}">search</span>`
        : `<span class="hop" data-sec="${esc(c.args.section_id)}" data-tip="${esc(c.result)}"><b>§${esc(c.args.section_id)}</b>${esc(state.outline.sections.find((s) => s.id === c.args.section_id)?.title || "")}</span>`;
      return (i ? '<span class="arrow">→</span>' : "") + hop;
    }).join("")}</div>`;
  }

  view.innerHTML = `<div class="page-head"><div><h1>Policy navigator</h1><p>The agent searches once, reads whole sections, then follows the most relevant links: explicit “see Section X” references first, then sections that cite what it just read. Numbers in the outline show the reading order.</p></div></div>
    <div class="policy-grid">
      <aside class="card card-pad outline"><h2 style="font-size:14px">${esc(state.outline.title)}</h2><ol>${outline}</ol></aside>
      <div>
        <div class="card card-pad composer">
          <label for="pq" class="muted" style="font-size:12px;font-weight:600">Policy question</label>
          <textarea id="pq" rows="2" placeholder="Ask about dispositions, approvals, thresholds, PHI handling…"></textarea>
          <div class="composer-foot"><span class="muted"><span class="kbd">Ctrl</span> + <span class="kbd">Enter</span> to run</span><button class="btn btn-primary" id="pBtn">${ICON.play} Navigate</button></div>
        </div>
        <div class="examples">${POLICY_EXAMPLES.map((q) => `<button class="chip" data-pex="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        ${res ? `<div class="card card-pad" style="margin-top:18px">
            <div class="run-head"><div class="run-q">${esc(res.question)}</div>
              <div class="run-meta"><span class="pill pill-info">${res.sections_read.length} sections read</span><span class="pill">${res.tool_calls.length} tool calls</span><span class="pill ${res.answer.confidence === "high" ? "pill-good" : "pill-warn"}">confidence ${esc(res.answer.confidence)}</span></div></div>
            <h3 style="margin-top:16px">Reading path</h3>${path}
            <h3 style="margin-top:18px">Answer</h3><div class="answer" style="margin-top:8px;white-space:pre-wrap">${esc(res.answer.answer)}</div>
            <div class="links"><span class="muted">Citations</span>${res.answer.citations.map((c) => `<button class="linkchip" data-sec="${esc(c)}">§${esc(c)}</button>`).join("")}</div>
          </div>` : ""}
        <div id="secSlot"></div>
      </div>
    </div>`;
  const ta = $("#pq");
  if (res) ta.value = res.question;
  const go = async (q) => {
    if (q.trim().length < 3) return;
    const b = $("#pBtn"); b.disabled = true; b.innerHTML = '<span class="spinner"></span> Reading';
    try {
      const r = await api("/policy/ask", { method: "POST", body: JSON.stringify({ question: q }) });
      state.policy = { ...r, question: q };
      state.activeSection = r.sections_read[0] || null;
      await renderPolicy();
    } catch (e) { toast(e.message, true); b.disabled = false; b.innerHTML = `${ICON.play} Navigate`; }
  };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); go(ta.value); } });
  $("#pBtn").addEventListener("click", () => go(ta.value));
  view.querySelectorAll("[data-pex]").forEach((b) => b.addEventListener("click", () => { ta.value = b.dataset.pex; go(b.dataset.pex); }));
  view.querySelectorAll("[data-sec]").forEach((b) => b.addEventListener("click", () => showSection(b.dataset.sec)));
  bindTooltips(view);
  if (state.activeSection) showSection(state.activeSection, false);
}

async function showSection(id, scroll = true) {
  try {
    const s = await api(`/policy/section/${encodeURIComponent(id)}`);
    state.activeSection = id;
    view.querySelectorAll(".outline li").forEach((li) => li.classList.toggle("active", li.querySelector("[data-sec]")?.dataset.sec === id));
    $("#secSlot").innerHTML = `<article class="card card-pad" style="margin-top:16px">
      <div class="crumb">${s.breadcrumb.map(esc).join(" › ")}</div><h2>§${esc(s.id)} ${esc(s.title)}</h2>
      <div class="section-view" style="margin-top:10px">${esc(s.text)}</div>
      <div class="links"><span class="muted">Refers to</span>${s.cross_refs.length ? s.cross_refs.map((r) => `<button class="linkchip" data-sec2="${esc(r)}">§${esc(r)}</button>`).join("") : '<span class="muted">none</span>'}
        <span class="muted" style="margin-left:10px">Cited by</span>${s.cited_by.length ? s.cited_by.map((r) => `<button class="linkchip" data-sec2="${esc(r)}">§${esc(r)}</button>`).join("") : '<span class="muted">none</span>'}</div>
    </article>`;
    $("#secSlot").querySelectorAll("[data-sec2]").forEach((b) => b.addEventListener("click", () => showSection(b.dataset.sec2)));
    if (scroll) $("#secSlot").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------- EVALS view --
function retrievalChart(rows) {
  const width = 720, labelW = 250, padR = 50, barH = 12, gapIn = 2, gapOut = 16, top = 26;
  const plotW = width - labelW - padR;
  const h = top + rows.length * (barH * 2 + gapIn + gapOut);
  const x = (v) => labelW + v * plotW;
  let g = `<g class="grid">${[0, 0.25, 0.5, 0.75, 1].map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${top - 6}" y2="${h}"/>`).join("")}</g>`;
  g += [0, 0.5, 1].map((t) => `<text x="${x(t)}" y="${top - 10}" text-anchor="middle">${t * 100}%</text>`).join("");
  rows.forEach((r, i) => {
    const y = top + i * (barH * 2 + gapIn + gapOut);
    const q = r.question.length > 38 ? r.question.slice(0, 37) + "…" : r.question;
    g += `<text x="${labelW - 10}" y="${y + barH + 4}" text-anchor="end">${esc(q)}</text>`;
    const tip = `<b>${esc(r.question)}</b><br>Required: ${esc(r.required.join(", "))}<br>Navigator: ${Math.round(r.navigator_recall * 100)}% (${esc(r.navigator.join(", "))})<br>Flat chunks: ${Math.round(r.baseline_recall * 100)}% (${esc(r.baseline.join(", "))})`;
    g += `<path class="bar" d="${barPath(labelW, y, x(r.navigator_recall) - labelW, barH, 3)}"/>`;
    g += `<path class="bar s2" d="${barPath(labelW, y + barH + gapIn, x(r.baseline_recall) - labelW, barH, 3)}"/>`;
    g += `<text class="val" x="${x(r.navigator_recall) + 5}" y="${y + barH - 2}">${Math.round(r.navigator_recall * 100)}%</text>`;
    g += `<text class="val" x="${x(r.baseline_recall) + 5}" y="${y + 2 * barH + gapIn - 2}">${Math.round(r.baseline_recall * 100)}%</text>`;
    g += `<rect class="hit" x="0" y="${y - gapOut / 2}" width="${width}" height="${barH * 2 + gapIn + gapOut}" data-tip="${esc(tip)}"/>`;
  });
  g += `<line x1="${labelW}" x2="${labelW}" y1="${top - 6}" y2="${h}" stroke="var(--border-strong)"/>`;
  return `<div class="legend"><span><i style="background:var(--series-1)"></i>Navigator</span><span><i style="background:var(--series-2)"></i>Flat top-3 chunks</span></div>
    <div class="chart"><svg viewBox="0 0 ${width} ${h}" role="img" aria-label="Retrieval recall per question, navigator versus flat chunks">${g}</svg></div>`;
}

async function renderEvals() {
  const ev = state.evals;
  const s = ev?.summary;
  const mean = (k) => ev ? ev.retrieval.reduce((a, r) => a + r[k], 0) / ev.retrieval.length : 0;
  view.innerHTML = `<div class="page-head"><div><h1>Evaluation</h1><p>The golden set compares result rows against a trusted reference query, so two different but equivalent queries both pass. Stages are scored separately. The retrieval A/B measures whether the sections an expert marked as required were actually read.</p></div>
      <button class="btn btn-primary" id="runEvals">${ICON.play} ${ev ? "Run again" : "Run eval suite"}</button></div>
    ${ev ? `
      <div class="kpis">
        <div class="card card-pad kpi"><div class="label">Golden cases passed</div><div class="value">${s.passed}/${s.cases}</div><div class="note">${s.passed === s.cases ? "all stages green" : "see failures below"}</div></div>
        <div class="card card-pad kpi"><div class="label">Result match</div><div class="value">${esc(s.result_match_rate)}</div><div class="note">rows equal the reference query</div></div>
        <div class="card card-pad kpi"><div class="label">Grounded answers</div><div class="value">${esc(s.grounded_rate)}</div><div class="note">every number traced to a row</div></div>
        <div class="card card-pad kpi"><div class="label">Policy retrieval recall</div><div class="value">${Math.round(mean("navigator_recall") * 100)}%</div><div class="note">navigator · flat chunks ${Math.round(mean("baseline_recall") * 100)}%</div></div>
      </div>
      <div class="card card-pad"><div class="row"><h2>Golden set</h2><span class="spacer"></span><span class="muted" style="font-size:12px">ran in ${ev.elapsed_ms} ms against the deterministic stand-in model</span></div>
        <div class="table-wrap" style="margin-top:12px"><table class="data"><thead><tr><th scope="col">Case</th><th scope="col">Result</th><th scope="col">Status</th><th scope="col">Rows match</th><th scope="col">Grounded</th><th class="num" scope="col">Attempts</th><th scope="col">Tags</th></tr></thead>
        <tbody>${ev.cases.map((c) => `<tr><td class="mono">${esc(c.id)}</td><td>${c.passed ? `<span class="pill pill-good">${ICON.ok}Pass</span>` : `<span class="pill pill-bad">${ICON.error}Fail</span>`}</td>
          <td>${c.status_ok ? "✓" : "✗"}</td><td>${c.result_match === null ? '<span class="muted">n/a</span>' : c.result_match ? "✓" : "✗"}</td>
          <td>${c.grounded === null ? '<span class="muted">n/a</span>' : c.grounded ? "✓" : "✗"}</td><td class="num">${c.attempts}</td>
          <td><div class="tagcell">${c.tags.map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</div></td></tr>`).join("")}</tbody></table></div>
        <h3 style="margin-top:16px">By capability</h3><div class="tagroll">${Object.entries(s.by_tag).map(([t, v]) => {
          const [a, b] = v.split("/").map(Number);
          return `<span class="pill ${a === b ? "pill-good" : "pill-bad"}">${a === b ? ICON.ok : ICON.error}${esc(t)} ${esc(v)}</span>`; }).join("")}</div>
      </div>
      <div class="card card-pad"><h2>Policy retrieval: navigator vs flat chunks</h2><p class="secondary" style="margin:4px 0 14px">Share of expert-required sections each approach actually read. Hover a row for the sections involved.</p>
        ${retrievalChart(ev.retrieval)}
        <details style="margin-top:12px"><summary class="muted" style="cursor:pointer">Table view</summary>
          <div class="table-wrap" style="margin-top:8px"><table class="data"><thead><tr><th>Question</th><th>Required</th><th class="num">Navigator</th><th class="num">Flat chunks</th></tr></thead>
          <tbody>${ev.retrieval.map((r) => `<tr><td>${esc(r.question)}</td><td class="mono">${esc(r.required.join(", "))}</td><td class="num">${Math.round(r.navigator_recall * 100)}%</td><td class="num">${Math.round(r.baseline_recall * 100)}%</td></tr>`).join("")}</tbody></table></div></details>
      </div>`
    : `<div class="card"><div class="empty">${ICON.info}<div>Run the suite to see per-stage results and the retrieval comparison.<br>It takes well under a second and uses the deterministic stand-in model, so it costs nothing.</div></div></div>`}`;
  $("#runEvals").addEventListener("click", async () => {
    const b = $("#runEvals"); b.disabled = true; b.innerHTML = '<span class="spinner"></span> Running';
    try { state.evals = await api("/evals/run", { method: "POST" }); renderEvals(); }
    catch (e) { toast(e.message, true); b.disabled = false; }
  });
  bindTooltips(view);
}

// ------------------------------------------------------------ SCHEMA view --
async function renderSchema() {
  const sc = await api(`/schema?role=${state.role}`);
  view.innerHTML = `<div class="page-head"><div><h1>Catalog as ${esc(roleLabel(state.role))} sees it</h1>
      <p>This is the semantic layer the model plans against. Struck-through columns are PHI or banking details: the model never sees them and the guard rejects any query that names them. Locked tables are outside this role.</p></div></div>
    <div class="schema-grid">${sc.tables.map((t) => `<article class="card card-pad tbl ${t.allowed ? "" : "locked"}">
      <div class="head"><span class="name">${esc(t.name)}</span><span class="row" style="gap:6px">
        ${t.requires_approval ? `<span class="pill pill-warn">${ICON.pending}approval</span>` : ""}
        ${t.allowed ? `<span class="pill pill-good">${ICON.ok}queryable</span>` : `<span class="pill pill-bad">${ICON.lock}${esc(t.allowed_roles.map(roleLabel).join(", "))} only</span>`}</span></div>
      <p>${esc(t.description)}</p>
      <ul class="cols">${t.columns.map((c) => `<li class="${c.restricted ? "restricted" : ""}"><span class="muted">${c.restricted ? ICON.lock : ""}</span><span class="cn">${esc(c.name)} <span class="muted">${esc(c.type)}</span></span><span class="ct">${esc(c.description)}</span></li>`).join("")}</ul>
    </article>`).join("")}</div>
    <div class="card card-pad" style="margin-top:16px"><h2>Metric definitions</h2><p class="secondary" style="margin:4px 0 6px">The planner is told to use these semantics verbatim. They are where text-to-SQL most often goes subtly wrong.</p>
      ${sc.metrics.map((m) => `<div class="metric"><b class="mono">${esc(m.name)}</b><div class="secondary">${esc(m.definition)}</div><code>${esc(m.sql_hint)}</code></div>`).join("")}</div>`;
}

// ======================================================================
// Shell: navigation toggle, command palette, counts
// ======================================================================
const money = (v, digits = 0) => "$" + Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const pct = (v, d = 1) => (v == null ? "n/a" : (v * 100).toFixed(d) + "%");
const sevBadge = (s) => `<span class="sev ${esc(s)}"><i></i>${esc(s[0].toUpperCase() + s.slice(1))}</span>`;
const OUTCOME = {
  confirmed: ["pill-bad", "Confirmed"], false_positive: ["pill-good", "False positive"],
  closed_no_action: ["", "No action"], open: ["pill-info", "Open"],
};
const outcomePill = (status, outcome) => {
  const [cls, label] = OUTCOME[status === "open" ? "open" : outcome] || ["", outcome || status];
  return `<span class="pill ${cls}">${esc(label)}</span>`;
};

function initShell() {
  $("#navToggle").addEventListener("click", () => {
    const root = document.documentElement;
    if (matchMedia("(max-width: 900px)").matches) {
      if (root.getAttribute("data-nav-open") === "true") root.removeAttribute("data-nav-open");
      else root.setAttribute("data-nav-open", "true");
      return;
    }
    const collapsed = root.dataset.nav === "collapsed";
    if (collapsed) delete root.dataset.nav; else root.dataset.nav = "collapsed";
    try { localStorage.setItem("pia-nav", collapsed ? "" : "collapsed"); } catch (e) {}
  });
  $("#paletteBtn").addEventListener("click", openPalette);
  addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
    if (e.key === "Escape") { closePalette(); closeDrawer(); }
  });
  $("#paletteScrim").addEventListener("click", (e) => { if (e.target.id === "paletteScrim") closePalette(); });
  $("#scrim").addEventListener("click", closeDrawer);
  // On small screens the sidebar overlays the page; a tap outside it closes it.
  document.querySelector(".frame").addEventListener("click", (e) => {
    if (document.documentElement.getAttribute("data-nav-open") === "true" && !e.target.closest("#navToggle")) {
      document.documentElement.removeAttribute("data-nav-open");
    }
  });
}

async function loadCases(force = false) {
  if (!state.cases || force) state.cases = await api("/cases");
  return state.cases;
}
async function refreshOpenCount() {
  try {
    const cs = await loadCases();
    const n = cs.filter((c) => c.status === "open").length;
    const b = $("#openCount"); b.textContent = n; b.hidden = !n;
  } catch (e) {}
}

// ---- command palette ----
let paletteItems = [], paletteIdx = 0;
const PAGES = Object.entries({ overview: "Overview", cases: "Case worklist", approvals: "SIU approvals", ask: "Ask the data",
  policy: "Policy navigator", evals: "Evaluation", audit: "Audit trail", schema: "Data catalog" });

async function openPalette() {
  $("#paletteScrim").hidden = false;
  const input = $("#paletteInput");
  input.value = "";
  input.focus();
  await loadCases().catch(() => []);
  renderPalette("");
  input.oninput = () => renderPalette(input.value);
  input.onkeydown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); paletteIdx = Math.min(paletteIdx + 1, paletteItems.length - 1); paintPalette(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); paletteIdx = Math.max(paletteIdx - 1, 0); paintPalette(); }
    else if (e.key === "Enter") { e.preventDefault(); paletteItems[paletteIdx]?.run(); }
  };
}
function closePalette() { $("#paletteScrim").hidden = true; }

function renderPalette(text) {
  const q = text.trim().toLowerCase();
  const items = [];
  const exampleHits = (state.meta?.examples || []).filter((e) => !q || e.question.toLowerCase().includes(q)).slice(0, q ? 6 : 4);
  if (q && exampleHits.length) exampleHits.forEach((e) => items.push({ grp: "Example questions", label: e.question, kind: roleLabel(e.role), run: () => goAsk(e.question) }));
  if (q.length >= 3) items.push({ grp: "Ask the agent", label: `Ask: "${text.trim()}"`, kind: roleLabel(state.role), run: () => goAsk(text.trim()) });
  PAGES.filter(([, l]) => !q || l.toLowerCase().includes(q)).forEach(([k, l]) =>
    items.push({ grp: "Go to", label: l, kind: "page", run: () => { closePalette(); location.hash = `#/${k}`; } }));
  (state.cases || []).filter((c) => q && (c.case_id.toLowerCase().includes(q) || c.claim_id.toLowerCase().includes(q) || (c.counterparty || "").toLowerCase().includes(q)))
    .slice(0, 8).forEach((c) => items.push({
      grp: "Cases", label: `${c.case_id} · ${c.counterparty || ""} · ${c.procedure_code}`,
      kind: c.status === "open" ? "open case" : (c.outcome || "").replace("_", " "),
      run: () => { closePalette(); openCase(c.case_id); },
    }));
  if (!q) exampleHits.forEach((e) => items.push({ grp: "Example questions", label: e.question, kind: roleLabel(e.role), run: () => goAsk(e.question) }));
  paletteItems = items; paletteIdx = 0; paintPalette();
}
function paintPalette() {
  let last = "";
  $("#paletteList").innerHTML = paletteItems.map((it, i) => {
    const head = it.grp !== last ? `<li class="grp" aria-hidden="true">${esc(it.grp)}</li>` : "";
    last = it.grp;
    return `${head}<li role="option" data-i="${i}" aria-selected="${i === paletteIdx}">${esc(it.label)}<span class="kind">${esc(it.kind)}</span></li>`;
  }).join("") || `<li class="grp">No matches</li>`;
  $("#paletteList").querySelectorAll("[data-i]").forEach((li) => {
    li.addEventListener("click", () => paletteItems[+li.dataset.i].run());
    li.addEventListener("mousemove", () => { if (paletteIdx !== +li.dataset.i) { paletteIdx = +li.dataset.i; paintPalette(); } });
  });
  $("#paletteList [aria-selected='true']")?.scrollIntoView({ block: "nearest" });
}
function goAsk(q) {
  closePalette();
  state.pendingAsk = q;
  if (location.hash === "#/ask") renderAsk(); else location.hash = "#/ask";
}

// ======================================================================
// OVERVIEW
// ======================================================================
function sparkline(values, label, fmtv) {
  if (!values.length) return "";
  const w = 200, h = 34, pad = 4;
  const max = Math.max(...values), min = Math.min(...values);
  const span = max - min || 1;
  const x = (i) => pad + (i / (values.length - 1 || 1)) * (w - pad * 2);
  const y = (v) => h - pad - ((v - min) / span) * (h - pad * 2);
  const line = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(values.length - 1)},${h} L${x(0)},${h} Z`;
  const tip = `<b>${esc(label)}</b><br>Jan ${esc(fmtv(values[0]))} → Jun ${esc(fmtv(values[values.length - 1]))}<br>peak ${esc(fmtv(max))}`;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" data-tip="${esc(tip)}" role="img" aria-label="${esc(label)} trend"><path class="a" d="${area}"/><path class="l" d="${line}" vector-effect="non-scaling-stroke"/></svg>`;
}

async function renderOverview() {
  const [d, audit] = await Promise.all([api("/dashboard"), api("/audit?limit=8").catch(() => ({ events: [] }))]);
  const k = d.kpis, m = d.monthly;
  const tiles = [
    ["Claims adjudicated", k.claims.toLocaleString(), `${m.length} months · Jan–Jun 2025`, sparkline(m.map((r) => r.claims), "Claims per month", (v) => v.toLocaleString())],
    ["Paid amount", money(k.paid_amount), "disbursed on paid claims", sparkline(m.map((r) => r.paid_amount), "Paid per month", (v) => money(v))],
    ["Flagged claims", k.flagged_claims.toLocaleString(), `${pct(k.flag_rate)} of all claims`, sparkline(m.map((r) => r.flagged), "Flagged claims per month", (v) => String(v))],
    ["Open cases", k.open_cases.toLocaleString(), `avg ${k.avg_days_to_close.toFixed(1)} days to close`, ""],
    ["False-positive rate", pct(k.false_positive_rate), `of ${k.closed_cases} closed cases`, ""],
    ["Recovered", money(k.recovered), `${k.duplicate_claims} duplicate payments · ${money(k.overpaid)} overpaid`, ""],
  ];
  const fpMax = Math.max(...d.by_rule.map((r) => r.false_positive_rate || 0), 0.01);
  const queueHtml = d.queue.length
    ? d.queue.slice(0, 4).map((r) => `<div class="qitem"><span class="pill pill-warn">${ICON.pending}</span><div><div class="t">${esc(r.question)}</div><div class="muted" style="font-size:12px">${esc(r.tables.join(", "))} · ${timeAgo(r.created_at)}</div></div></div>`).join("")
    : `<div class="empty" style="padding:20px 0">${ICON.ok}<div>No queries waiting for approval.</div></div>`;
  const activityHtml = audit.events.length
    ? audit.events.slice(0, 6).map((e) => `<div class="qitem">${auditBadge(e)}<div><div class="t" style="font-weight:550">${esc(e.detail)}</div><div class="muted" style="font-size:12px">${esc(roleLabel(e.role))} · ${timeAgo(e.ts)}</div></div></div>`).join("")
    : `<div class="muted" style="font-size:12.5px">No agent activity yet in this session.</div>`;
  const ruleRows = d.by_rule.map((r) => `<tr><td><span class="rule">${esc(r.rule_id)}</span></td><td>${esc(r.signal_type.replaceAll("_", " "))}</td><td>${sevBadge(r.severity)}</td><td class="num">${r.flags}</td><td class="num">${r.closed}</td>
      <td><div class="row" style="gap:8px;flex-wrap:nowrap"><div class="meter" style="flex:1" data-tip="${esc(`<b>${r.rule_id}</b> ${r.false_positive} of ${r.closed} closed cases were false positives`)}"><i style="width:${((r.false_positive_rate || 0) / fpMax) * 100}%"></i></div><span class="num-t" style="min-width:40px;text-align:right">${pct(r.false_positive_rate, 0)}</span></div></td></tr>`).join("");
  const providerRows = d.providers.map((p) => `<tr><td><b>${esc(p.name)}</b><span class="sub2 mono">${esc(p.provider_id)}</span></td><td>${esc(p.specialty)}</td><td class="num">${p.claims}</td><td class="num">${p.flagged}</td>
      <td><div class="row" style="gap:8px;flex-wrap:nowrap"><div class="meter" style="flex:1" data-tip="${esc(`<b>${p.name}</b><br>${p.flagged} of ${p.claims} claims flagged`)}"><i style="width:${p.flag_rate * 100}%"></i></div><span class="num-t" style="min-width:40px;text-align:right">${pct(p.flag_rate, 0)}</span></div></td><td class="num">${money(p.billed)}</td></tr>`).join("");

  view.innerHTML = `
    <div class="page-head"><div><h1>Payment integrity overview</h1><p>Book of business, detection performance, and investigation outcomes. Figures come from fixed, reviewed queries over the de-identified mart, not from the model.</p></div>
      <div class="page-actions"><a class="btn" href="#/cases">Open worklist</a><button class="btn btn-primary" id="dashAsk">${ICON.play} Ask a question</button></div></div>
    <div class="kpis six">${tiles.map(([l, v, n, sp], i) => `<div class="card card-pad kpi ${i === 3 ? "accent" : ""}"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="note">${esc(n)}</div>${sp}</div>`).join("")}</div>
    <div class="dash-grid">
      <section class="card card-pad span-8"><div class="card-head"><h2>Flagged claims by month</h2><span class="sub">distinct claims with at least one risk flag</span></div>
        ${lineChart({ labels: m.map((r) => r.period), values: m.map((r) => r.flagged), colLabel: "month", valueCol: "flagged claims", title: false, height: 230 })}</section>
      <section class="card card-pad span-4"><div class="card-head"><h2>SIU approval queue</h2><a href="#/approvals" class="sub">View all</a></div>${queueHtml}
        <div class="card-head" style="margin:18px 0 6px"><h2>Recent agent activity</h2><a href="#/audit" class="sub">Audit trail</a></div>${activityHtml}</section>
      <section class="card card-pad span-6"><div class="card-head"><h2>Rule performance</h2><span class="sub">closed-case false-positive rate by rule</span></div>
        <div class="table-wrap"><table class="data"><thead><tr><th>Rule</th><th>Signal</th><th>Base severity</th><th class="num">Flags</th><th class="num">Closed</th><th>False-positive rate</th></tr></thead><tbody>${ruleRows}</tbody></table></div></section>
      <section class="card card-pad span-6"><div class="card-head"><h2>Case outcomes</h2><span class="sub">${d.outcomes.reduce((a, o) => a + o.cases, 0)} investigations</span></div>
        ${hBarChart({ labels: d.outcomes.map((o) => (OUTCOME[o.outcome]?.[1] || o.outcome)), values: d.outcomes.map((o) => o.cases), colLabel: "outcome", valueCol: "cases", title: false, width: 560 })}</section>
      <section class="card card-pad span-12"><div class="card-head"><h2>Provider risk</h2><span class="sub">share of each provider's claims carrying a risk flag</span></div>
        <div class="table-wrap"><table class="data"><thead><tr><th>Provider</th><th>Specialty</th><th class="num">Claims</th><th class="num">Flagged</th><th style="width:30%">Flag rate</th><th class="num">Billed</th></tr></thead><tbody>${providerRows}</tbody></table></div></section>
    </div>`;
  $("#dashAsk").addEventListener("click", () => { location.hash = "#/ask"; });
  bindTooltips(view); bindCrosshair(view);
}

// ======================================================================
// CASE WORKLIST + DRAWER
// ======================================================================
const SEV_ORDER = { high: 3, medium: 2, low: 1 };
function filteredCases() {
  const f = state.caseFilters;
  const q = f.q.trim().toLowerCase();
  let rows = state.cases.filter((c) =>
    (f.status === "all" || c.status === f.status) &&
    (f.severity === "all" || c.severity === f.severity) &&
    (f.rule === "all" || c.rules.includes(f.rule)) &&
    (f.investigator === "all" || c.investigator === f.investigator) &&
    (!q || [c.case_id, c.claim_id, c.counterparty, c.procedure_code, c.procedure_desc, c.member_id].some((v) => String(v || "").toLowerCase().includes(q))));
  const { key, asc } = state.caseSort;
  const val = (c) => (key === "severity" ? SEV_ORDER[c.severity] * 1000 + (c.max_score || 0) : Array.isArray(c[key]) ? c[key].join(",") : c[key]);
  return [...rows].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * (asc ? 1 : -1));
}

function downloadCsv(filename, head, rows) {
  const cell = (v) => `"${String(Array.isArray(v) ? v.join(" ") : v ?? "").replaceAll('"', '""')}"`;
  const csv = [head.join(",")].concat(rows.map((r) => head.map((h) => cell(r[h])).join(","))).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function renderCases() {
  await loadCases();
  const all = state.cases, f = state.caseFilters;
  const investigators = [...new Set(all.map((c) => c.investigator))].sort();
  const opt = (v, cur, label = v) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(label)}</option>`;
  view.innerHTML = `
    <div class="page-head"><div><h1>Case worklist</h1><p>Investigations opened from flagged claims, prioritised by severity and model score. Members appear by pseudonymous id only; identity resolution happens outside this console under minimum-necessary rules.</p></div>
      <div class="page-actions"><button class="btn" id="exportCases">Export CSV</button></div></div>
    <div class="card card-pad">
      <div class="toolbar" role="search">
        <label class="field"><svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M16 16l4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
          <input id="cq" placeholder="Case, claim, provider, CPT…" value="${esc(f.q)}" aria-label="Search cases"></label>
        <select class="sel" id="cstatus" aria-label="Status">${opt("all", f.status, "All statuses")}${opt("open", f.status, "Open")}${opt("closed", f.status, "Closed")}</select>
        <select class="sel" id="csev" aria-label="Severity">${opt("all", f.severity, "All severities")}${opt("high", f.severity, "High")}${opt("medium", f.severity, "Medium")}${opt("low", f.severity, "Low")}</select>
        <select class="sel" id="crule" aria-label="Rule">${opt("all", f.rule, "All rules")}${["R1", "R2", "R3", "R4", "R5"].map((r) => opt(r, f.rule)).join("")}</select>
        <select class="sel" id="cinv" aria-label="Investigator">${opt("all", f.investigator, "All investigators")}${investigators.map((i) => opt(i, f.investigator)).join("")}</select>
        <span class="spacer"></span><span class="muted" id="ccount"></span>
      </div>
      <div id="caseTable"></div>
    </div>`;
  const cols = [["case_id", "Case"], ["severity", "Severity"], ["rules", "Signals"], ["counterparty", "Counterparty"], ["procedure_code", "Procedure"],
    ["billed_amount", "Billed", "num"], ["status", "Status"], ["investigator", "Owner"], ["age_days", "Age", "num"]];
  const paint = () => {
    const rows = filteredCases();
    $("#ccount").textContent = `${rows.length} of ${all.length} cases`;
    const { key, asc } = state.caseSort;
    $("#caseTable").innerHTML = rows.length ? `<div class="table-wrap" style="max-height:none"><table class="data"><thead><tr>${cols.map(([k2, l, c]) =>
      `<th class="${c || ""}" data-sort="${k2}" aria-sort="${key === k2 ? (asc ? "ascending" : "descending") : "none"}">${esc(l)}<span class="dir">${key === k2 ? (asc ? "▲" : "▼") : ""}</span></th>`).join("")}</tr></thead>
      <tbody>${rows.map((c) => `<tr class="clickable" tabindex="0" data-case="${esc(c.case_id)}">
        <td><span class="idcell">${esc(c.case_id)}</span><span class="sub2">${esc(c.claim_id)}</span></td>
        <td>${sevBadge(c.severity)}<span class="sub2">score ${(c.max_score ?? 0).toFixed(2)}</span></td>
        <td>${c.rules.map((r) => `<span class="rule">${esc(r)}</span>`).join("")}</td>
        <td>${esc(c.counterparty || "n/a")}<span class="sub2">${esc(c.claim_type)} · ${esc(c.counterparty_id || "")}</span></td>
        <td><span class="mono">${esc(c.procedure_code)}</span><span class="sub2">${esc(c.procedure_desc)}</span></td>
        <td class="num">${money(c.billed_amount, 2)}</td>
        <td>${outcomePill(c.status, c.outcome)}</td>
        <td class="mono">${esc(c.investigator)}</td>
        <td class="num">${c.age_days}d</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${ICON.info}<div>No cases match these filters.</div></div>`;
    $("#caseTable").querySelectorAll("[data-sort]").forEach((th) => th.addEventListener("click", () => {
      const k2 = th.dataset.sort;
      state.caseSort = { key: k2, asc: state.caseSort.key === k2 ? !state.caseSort.asc : k2 !== "severity" };
      paint();
    }));
    $("#caseTable").querySelectorAll("[data-case]").forEach((tr) => {
      tr.addEventListener("click", () => openCase(tr.dataset.case));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openCase(tr.dataset.case); });
    });
  };
  const bind = (id, key) => $(id).addEventListener(id === "#cq" ? "input" : "change", (e) => { state.caseFilters[key] = e.target.value; paint(); });
  bind("#cq", "q"); bind("#cstatus", "status"); bind("#csev", "severity"); bind("#crule", "rule"); bind("#cinv", "investigator");
  $("#exportCases").addEventListener("click", () => {
    const rows = filteredCases();
    downloadCsv("payment-integrity-cases.csv", ["case_id", "claim_id", "severity", "max_score", "rules", "counterparty", "claim_type", "procedure_code",
      "billed_amount", "status", "outcome", "investigator", "age_days", "member_id"], rows);
    toast(`Exported ${rows.length} cases`);
  });
  paint();
}

let drawerReturnFocus = null;
async function openCase(caseId) {
  drawerReturnFocus = document.activeElement;
  const dr = $("#drawer");
  dr.innerHTML = `<div class="drawer-head"><div class="skeleton" style="width:50%"></div></div><div class="drawer-body"><div class="skeleton"></div><div class="skeleton" style="width:80%"></div></div>`;
  $("#scrim").hidden = false; dr.classList.add("open"); dr.setAttribute("aria-hidden", "false");
  try {
    const c = await api(`/cases/${encodeURIComponent(caseId)}`);
    const dupes = c.payments.length > 1;
    const flagRows = c.flags.map((f) => `<tr><td><span class="rule">${esc(f.rule_id)}</span></td><td>${esc(f.signal_type.replaceAll("_", " "))}</td><td>${sevBadge(f.severity)}</td>
        <td><div class="row" style="gap:8px;flex-wrap:nowrap"><div class="meter" style="flex:1"><i style="width:${f.score * 100}%"></i></div><span class="num-t">${f.score.toFixed(2)}</span></div></td><td class="mono">${esc(f.flagged_date)}</td></tr>`).join("");
    const payRows = c.payments.map((p) => `<tr><td class="mono">${esc(p.payment_id)}</td><td class="mono">${esc(p.paid_date)}</td><td class="mono">${esc(p.payee_id)}</td><td>${esc(p.method)}</td><td class="num">${money(p.amount, 2)}</td></tr>`).join("");
    const histRows = c.member_history.map((h) => `<tr${h.claim_id === c.claim_id ? ' style="font-weight:650"' : ""}><td class="mono">${esc(h.claim_id)}</td><td class="mono">${esc(h.service_date)}</td><td class="mono">${esc(h.procedure_code)}</td><td>${esc(h.status)}</td><td class="num">${money(h.billed_amount, 2)}</td></tr>`).join("");
    const lead = state.role === "siu_lead";
    dr.innerHTML = `
      <div class="drawer-head"><div style="flex:1">
          <div class="row" style="gap:8px"><span class="idcell" style="font-size:16px">${esc(c.case_id)}</span>${sevBadge(c.severity)}${outcomePill(c.status, c.outcome)}</div>
          <div class="secondary" style="margin-top:4px">${esc(c.procedure_desc)} · ${esc(c.counterparty || "")}</div></div>
        <button class="icon-btn" id="closeDrawer" aria-label="Close case detail"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button></div>
      <div class="drawer-body">
        <div class="section"><h3>Claim</h3><dl class="kv">
          <dt>Claim</dt><dd class="mono">${esc(c.claim_id)} · ${esc(c.claim_type)}</dd>
          <dt>Service date</dt><dd>${esc(c.service_date)} <span class="muted">(${esc(c.period)})</span></dd>
          <dt>Procedure</dt><dd><span class="mono">${esc(c.procedure_code)}</span> ${esc(c.procedure_desc)}</dd>
          <dt>Counterparty</dt><dd>${esc(c.counterparty || "n/a")} <span class="muted mono">${esc(c.counterparty_id || "")}</span></dd>
          <dt>Billed / paid</dt><dd class="num-t">${money(c.billed_amount, 2)} / ${money(c.paid_amount, 2)}</dd>
          <dt>Owner</dt><dd class="mono">${esc(c.investigator)}</dd>
          <dt>Opened</dt><dd>${esc(c.opened_date)}${c.closed_date ? ` · closed ${esc(c.closed_date)}` : ""} <span class="muted">(${c.age_days} days)</span></dd>
          ${c.recovery_amount ? `<dt>Recovered</dt><dd class="num-t">${money(c.recovery_amount, 2)}</dd>` : ""}
        </dl></div>
        <div class="section"><h3>Member</h3><dl class="kv"><dt>Member</dt><dd class="mono">${esc(c.member.member_id)}</dd><dt>Plan</dt><dd>${esc(c.member.plan)}</dd><dt>Region</dt><dd>${esc(c.member.region)}</dd></dl>
          <div class="phi-note" style="margin-top:8px">${ICON.lock}<span>Name, MRN and date of birth are withheld. This console works on pseudonymous ids under minimum-necessary access.</span></div></div>
        <div class="section"><h3>Risk signals</h3><div class="table-wrap"><table class="data"><thead><tr><th>Rule</th><th>Signal</th><th>Severity</th><th style="width:34%">Score</th><th>Flagged</th></tr></thead><tbody>${flagRows}</tbody></table></div></div>
        <div class="section"><h3>Payments${dupes ? ` <span class="pill pill-bad" style="margin-left:6px">${ICON.error}Duplicate disbursement</span>` : ""}</h3>
          ${c.payments.length ? `<div class="table-wrap"><table class="data"><thead><tr><th>Payment</th><th>Date</th><th>Payee</th><th>Method</th><th class="num">Amount</th></tr></thead><tbody>${payRows}</tbody></table></div>` : `<div class="muted">No disbursement recorded.</div>`}</div>
        <div class="section"><h3>Member claim history</h3><div class="table-wrap"><table class="data"><thead><tr><th>Claim</th><th>Date</th><th>CPT</th><th>Status</th><th class="num">Billed</th></tr></thead><tbody>${histRows}</tbody></table></div></div>
        <div class="section"><h3>Investigator notes</h3>
          <div class="notice ${lead ? "" : "warn"}">${ICON.lock}<div>${c.note_count} note${c.note_count === 1 ? "" : "s"} on file. Notes are restricted to the SIU lead, and every read is held for approval.
            ${lead ? `<div style="margin-top:8px"><button class="btn btn-sm" id="reqNotes">${ICON.play} Request notes for open cases</button></div>` : ""}</div></div></div>
        <div class="section"><h3>Investigate with the agent</h3><div class="row">
          <button class="chip" data-q="How many claims were flagged by each risk rule in March 2025?">Rule volume, March</button>
          <button class="chip" data-q="What is the false positive rate of closed cases by rule?">False-positive rate by rule</button>
          <button class="chip" data-q="How many duplicate payments were made and what was the total overpaid amount?">Duplicate overpayment</button>
          <a class="chip" href="#/policy" id="toPolicy">Check the governing policy</a></div></div>
      </div>`;
    $("#closeDrawer").addEventListener("click", closeDrawer);
    $("#reqNotes")?.addEventListener("click", () => { closeDrawer(); goAsk("Show the investigator notes for open cases."); });
    dr.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => { closeDrawer(); goAsk(b.dataset.q); }));
    $("#toPolicy").addEventListener("click", closeDrawer);
    bindTooltips(dr);
    dr.focus();
  } catch (e) { closeDrawer(); toast(e.message, true); }
}
function closeDrawer() {
  const dr = $("#drawer");
  if (!dr.classList.contains("open")) return;
  dr.classList.remove("open"); dr.setAttribute("aria-hidden", "true"); $("#scrim").hidden = true;
  if (drawerReturnFocus && document.contains(drawerReturnFocus)) drawerReturnFocus.focus();
}

// ======================================================================
// AUDIT TRAIL
// ======================================================================
const AUDIT_TYPES = {
  phi_blocked: ["pill-bad", "PHI access blocked"], guard_rejected: ["pill-serious", "Guard rejected"],
  approval_requested: ["pill-warn", "Approval requested"], approval_approved: ["pill-good", "Approved"], approval_rejected: ["pill-bad", "Rejected"],
  refused: ["pill-serious", "Refused"], failed: ["pill-bad", "Failed"], ungrounded: ["pill-warn", "Ungrounded"],
  answered: ["pill-info", "Answered"], clarification: ["", "Clarification"], query: ["", "Query"],
};
function auditBadge(e) {
  const [cls, label] = AUDIT_TYPES[e.type] || ["", e.type];
  return `<span class="pill ${cls}">${esc(label)}</span>`;
}
async function renderAudit() {
  const a = await api("/audit?limit=1000");
  const c = a.counts;
  const filters = [["all", "All events"], ["security", "Security"], ["approvals", "Approvals"], ["outcomes", "Outcomes"]];
  const groups = { security: ["phi_blocked", "guard_rejected", "refused"], approvals: ["approval_requested", "approval_approved", "approval_rejected"], outcomes: ["answered", "clarification", "failed", "ungrounded"] };
  const events = a.events.filter((e) => state.auditFilter === "all" || groups[state.auditFilter].includes(e.type));
  view.innerHTML = `
    <div class="page-head"><div><h1>Audit trail</h1><p>Every agent interaction in this session: who asked, what the guard decided, which queries were held for approval and who approved them. PHI access attempts are recorded even when they are blocked.</p></div>
      <div class="page-actions"><button class="btn" id="exportAudit">Export CSV</button></div></div>
    <div class="kpis">
      <div class="card card-pad kpi"><div class="label">Queries</div><div class="value">${c.query || 0}</div><div class="note">questions asked</div></div>
      <div class="card card-pad kpi"><div class="label">PHI access blocked</div><div class="value" style="color:var(--critical-ink)">${c.phi_blocked || 0}</div><div class="note">restricted columns rejected by the guard</div></div>
      <div class="card card-pad kpi"><div class="label">Held for approval</div><div class="value">${c.approval_requested || 0}</div><div class="note">${c.approval_approved || 0} approved · ${c.approval_rejected || 0} rejected</div></div>
      <div class="card card-pad kpi"><div class="label">Refused or failed</div><div class="value">${(c.refused || 0) + (c.failed || 0)}</div><div class="note">no data returned</div></div>
    </div>
    <div class="card card-pad">
      <div class="toolbar"><div class="segmented" role="tablist" aria-label="Filter events">${filters.map(([k2, l]) => `<button role="tab" data-af="${k2}" aria-selected="${state.auditFilter === k2}">${esc(l)}</button>`).join("")}</div>
        <span class="spacer"></span><span class="muted">${events.length} event${events.length === 1 ? "" : "s"}</span></div>
      ${events.length ? `<ul class="audit-list">${events.map((e) => `<li>
          <span class="when">${new Date(e.ts * 1000).toLocaleTimeString()}</span>
          <span>${auditBadge(e)}<span class="sub2" style="margin-top:4px">${esc(roleLabel(e.role))}</span></span>
          <span><div>${esc(e.detail)}</div><div class="q">"${esc(e.question)}"</div></span>
          <a class="rid" href="#/ask" data-run="${esc(e.run_id)}">${esc(e.run_id)}</a></li>`).join("")}</ul>`
        : `<div class="empty">${ICON.info}<div>No events yet. Ask a question to start the trail.</div></div>`}
    </div>`;
  view.querySelectorAll("[data-af]").forEach((b) => b.addEventListener("click", () => { state.auditFilter = b.dataset.af; renderAudit(); }));
  view.querySelectorAll("[data-run]").forEach((l) => l.addEventListener("click", async (ev) => {
    ev.preventDefault();
    try { state.run = await api(`/runs/${l.dataset.run}`); location.hash = "#/ask"; } catch (e) { toast(e.message, true); }
  }));
  $("#exportAudit").addEventListener("click", () => {
    downloadCsv("agent-audit-trail.csv", ["time", "type", "role", "run_id", "detail", "question"],
      events.map((e) => ({ ...e, time: new Date(e.ts * 1000).toISOString() })));
    toast(`Exported ${events.length} events`);
  });
}

// ------------------------------------------------------------------ router --
const VIEWS = { overview: renderOverview, cases: renderCases, ask: renderAsk, approvals: renderApprovals, policy: renderPolicy,
  evals: renderEvals, audit: renderAudit, schema: renderSchema };
const CRUMBS = { overview: ["Operations", "Overview"], cases: ["Operations", "Case worklist"], approvals: ["Operations", "SIU approvals"],
  ask: ["Agent", "Ask the data"], policy: ["Agent", "Policy navigator"], evals: ["Governance", "Evaluation"],
  audit: ["Governance", "Audit trail"], schema: ["Governance", "Data catalog"] };
async function route() {
  const name = (location.hash.match(/^#\/(\w+)/) || [])[1] || "overview";
  const fn = VIEWS[name] || renderOverview;
  const [grp, page] = CRUMBS[name] || CRUMBS.overview;
  $("#crumbGroup").textContent = grp; $("#crumbPage").textContent = page;
  document.title = `${page} · Payment Integrity Console`;
  document.querySelectorAll(".side-nav a").forEach((a) => (a.dataset.view === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  document.documentElement.removeAttribute("data-nav-open");
  tooltip.hidden = true;
  try { await fn(); } catch (e) { view.innerHTML = `<div class="card"><div class="empty">${ICON.error}<div>${esc(e.message)}</div></div></div>`; }
}
addEventListener("hashchange", () => { route(); view.focus({ preventScroll: true }); });
addEventListener("keydown", (e) => {
  if (e.key === "/" && !/^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName)) {
    const t = $("#q") || $("#pq"); if (t) { e.preventDefault(); t.focus(); }
  }
});

(async function init() {
  syncIdentity();
  initShell();
  try {
    state.meta = await api("/meta");
    const p = $("#provider");
    p.textContent = state.meta.offline ? "Offline · deterministic model" : `Live · ${state.meta.provider}`;
    p.title = state.meta.offline ? "No ANTHROPIC_API_KEY set: a scripted stand-in model drives the same pipeline" : "Claude via the Anthropic SDK";
  } catch (e) { toast("API unreachable: " + e.message, true); }
  await refreshQueue();
  refreshOpenCount();
  route();
  setInterval(refreshQueue, 15000);
})();
