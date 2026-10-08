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

function hBarChart({ labels, values, colLabel, valueCol, width = 720 }) {
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
  return `<div class="chart"><div class="chart-title">${esc(valueCol)} by ${esc(colLabel)}</div><svg viewBox="0 0 ${width} ${h}" role="img" aria-label="Bar chart of ${esc(valueCol)} by ${esc(colLabel)}">${g}</svg></div>`;
}

function lineChart({ labels, values, colLabel, valueCol, width = 720, height = 240 }) {
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
  return `<div class="chart"><div class="chart-title">${esc(valueCol)} by ${esc(colLabel)}</div><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Line chart of ${esc(valueCol)} over ${esc(colLabel)}">${g}</svg></div>`;
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
  document.querySelectorAll("#roleSwitch button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.role === role)));
  route();
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

// ------------------------------------------------------------------ router --
const VIEWS = { ask: renderAsk, approvals: renderApprovals, policy: renderPolicy, evals: renderEvals, schema: renderSchema };
async function route() {
  const name = (location.hash.match(/^#\/(\w+)/) || [])[1] || "ask";
  const fn = VIEWS[name] || renderAsk;
  document.querySelectorAll(".rail a").forEach((a) => (a.dataset.view === name ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
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
  document.querySelectorAll("#roleSwitch button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.role === state.role)));
  try {
    state.meta = await api("/meta");
    const p = $("#provider");
    p.textContent = state.meta.offline ? "offline · deterministic model" : state.meta.provider;
    p.title = state.meta.offline ? "No ANTHROPIC_API_KEY set: a scripted stand-in model drives the same pipeline" : "Claude via the Anthropic SDK";
  } catch (e) { toast("API unreachable: " + e.message, true); }
  await refreshQueue();
  route();
  setInterval(refreshQueue, 15000);
})();
