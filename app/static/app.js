"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (n, d = 0) => "₹" + Number(n).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (x) => (x == null ? "–" : Math.round(x * 100) + "%");
const THRESHOLD = 0.75;
const EXC = { bank_change: "Bank account change", duplicate_suspect: "Possible duplicate", tax_mismatch: "GST type mismatch",
  quantity_mismatch: "Billed > received", price_variance: "Price variance", unplanned_charge: "Charge not on PO",
  uom_mismatch: "Unit-of-measure mismatch", payment_terms: "Early-payment discount" };

const state = { inbox: [], filter: "pending", current: null, actions: {}, status: null };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3800);
}

/* ---------------- tabs ---------------- */
$$(".bar nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  $$(".bar nav button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".tab").forEach((t) => (t.hidden = t.id !== "tab-" + name));
  if (name === "curve") loadCurve();
  if (name === "vendors") loadVendors();
  if (name === "memory") loadCases();
}

/* ---------------- status & replay ---------------- */
async function loadStatus() {
  const s = await api("/api/status");
  state.status = s;
  $("#company").textContent = `${s.company.name}, ${s.company.city}`;
  if (s.setup_error) {
    $("#status").innerHTML = `<b>Not connected</b><br>v${esc(s.version)}`;
    $("#case").innerHTML = `<div class="empty"><h2>Precedent can't reach its memory</h2><p>${esc(s.setup_error)}</p></div>`;
    return s;
  }
  const mem = s.memory_backend === "hindsight" ? `Hindsight bank <b>${esc(s.bank_id)}</b>` : `Memory: <b>${esc(s.memory_backend)}</b> (offline)`;
  $("#status").innerHTML = `${mem}<br>${s.llm ? "LLM <b>" + esc(s.llm) + "</b>" : "LLM off: evidence-only decisions"} · v${esc(s.version)}`;
  return s;
}
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-action=replay]");
  if (!btn) return;
  if (!confirm("Rebuild memory from history?\n\nThis DELETES the Hindsight bank and replays 11 weeks of AP decisions into it (about 4 minutes). Only do this if the learning curve is empty or wrong.")) return;
  $$("[data-action=replay]").forEach((b) => (b.disabled = true));
  try {
    await api("/api/replay", { method: "POST", body: JSON.stringify({ confirm: "RESET" }) });
    showTab("curve");
    pollReplay();
  } catch (err) { toast(err.message); $$("[data-action=replay]").forEach((b) => (b.disabled = false)); }
});
async function pollReplay() {
  const p = $("#progress"); p.hidden = false;
  const s = await loadStatus();
  const pr = s.progress;
  $(".fill", p).style.width = pr.total ? (100 * pr.done) / pr.total + "%" : "4%";
  $("span", p).textContent = pr.total ? `${pr.done} of ${pr.total} invoices: ${pr.message}` : pr.message;
  if (pr.done % 5 === 0) loadCurve();
  if (pr.running) return setTimeout(pollReplay, 900);
  $$("[data-action=replay]").forEach((b) => (b.disabled = false));
  loadCurve(); loadInbox(); toast(pr.message);
}

/* ---------------- inbox ---------------- */
$$(".filters .chip").forEach((c) => c.addEventListener("click", () => {
  $$(".filters .chip").forEach((x) => x.classList.toggle("on", x === c));
  state.filter = c.dataset.filter; renderQueue();
}));
async function loadInbox() {
  $("#queue").innerHTML = `<li class="q-loading"><button disabled>Reading memory…</button></li>`;
  try { state.inbox = await api("/api/inbox"); }
  catch (err) {
    state.inbox = [];
    $("#queue").innerHTML = `<li><button disabled>${esc(err.message)}</button></li>`;
    return;
  }
  renderQueue();
  const firstPending = state.inbox.find((r) => r.status === "pending" && r.exception_type);
  if (!state.current && firstPending && state.status?.resolved) openCase(firstPending.invoice_id);
}
function verdictKind(r) {
  if (r.status === "resolved") return ["t-done", "Decided"];
  if (!r.exception_type) return ["t-auto", "Clean match"];
  if (r.auto_resolved) return ["t-auto", "Agent can clear"];
  if (r.requires_human || r.action === "escalate") return ["t-human", "Person required"];
  return ["t-review", "Confirm"];
}
function renderQueue() {
  const rows = state.inbox.filter((r) =>
    state.filter === "all" ? true :
    state.filter === "auto" ? r.status === "pending" && r.auto_resolved && r.exception_type :
    r.status === "pending" && r.exception_type && !r.auto_resolved);
  if (!rows.length) {
    $("#queue").innerHTML = `<li><button disabled>${state.status?.resolved ? "Nothing in this view." : "No history yet. Run: python scripts/replay.py"}</button></li>`;
    return;
  }
  $("#queue").innerHTML = rows.map((r) => {
    const [cls, label] = verdictKind(r);
    return `<li><button data-id="${r.invoice_id}" ${state.current === r.invoice_id ? 'aria-current="true"' : ""}>
      <span class="q-vendor">${esc(r.vendor_name)}</span><span class="q-amt">${inr(r.total)}</span>
      <span class="q-exc">${esc(r.exception_label)} · ${esc(r.vendor_invoice_no)}</span><span class="q-tag tag ${cls}">${label}</span>
    </button></li>`;
  }).join("");
  $$("#queue button[data-id]").forEach((b) => b.addEventListener("click", () => openCase(b.dataset.id)));
}

async function openCase(id) {
  state.current = id;
  renderQueue();
  const el = $("#case");
  el.innerHTML = `<div class="empty"><p>Recalling precedents from memory…</p></div>`;
  try {
    const d = await api(`/api/invoice/${id}`);
    state.actions = d.actions;
    el.innerHTML = renderCase(d);
    bindCase(d);
  } catch (err) { el.innerHTML = `<div class="empty"><h2>Couldn't load ${esc(id)}</h2><p>${esc(err.message)}</p></div>`; }
}

function renderCase(d) {
  const inv = d.invoice, v = d.vendor, dec = d.decision;
  const po = d.po || { lines: [] };
  const poBy = Object.fromEntries(po.lines.map((l) => [l.sku, l]));
  const lineRows = inv.lines.map((l) => {
    const p = poBy[l.sku] || {};
    const rec = d.received[l.sku] ?? 0;
    const badU = p.uom && p.uom !== l.uom;
    const badP = !badU && p.unit_price && (l.unit_price - p.unit_price) / p.unit_price > 0.01;
    return `<tr><td>${esc(l.description)}<br><small>${esc(l.sku)}</small></td>
      <td class="num">${p.qty?.toLocaleString("en-IN") ?? "–"} ${esc(p.uom ?? "")}</td>
      <td class="num">${rec.toLocaleString("en-IN")} ${esc(p.uom ?? "")}</td>
      <td class="num ${badU ? "bad" : ""}">${l.qty.toLocaleString("en-IN")} ${esc(l.uom)}</td>
      <td class="num">${p.unit_price ? inr(p.unit_price, 2) : "–"}</td>
      <td class="num ${badP ? "bad" : ""}">${inr(l.unit_price, 2)}</td></tr>`;
  }).join("");
  const extras = (inv.extra_charges || []).map((c) => `<tr><td colspan="5">${esc(c.description)} <small>(not on PO)</small></td><td class="num bad">${inr(c.amount)}</td></tr>`).join("");
  const flags = d.exceptions.length ? d.exceptions.map((e) => `<li><b>${esc(e.label)}</b><span>${esc(e.summary)}</span></li>`).join("") : `<li><b>Clean match</b><span>Price, quantity, GST and bank details agree.</span></li>`;

  return `
  <div class="case-head">
    <div>
      <h2>${esc(v.name)}</h2>
      <div class="meta">Invoice ${esc(inv.vendor_invoice_no)} (${esc(inv.invoice_id)}), ${esc(inv.po_id)}, received ${esc(inv.received_at)}<br>${esc(v.category)}, ${esc(v.city)}. Terms ${esc(inv.payment_terms)}. Pays to ${esc(inv.bank_account)}</div>
    </div>
    <div class="total">${inr(inv.total)}<small>incl. ${esc(inv.tax.type)} ${inv.tax.rate}% ${inr(inv.tax.amount)}</small></div>
  </div>
  <div class="case-grid">
    <div>
      <div class="panel">
        <h3>Three-way match <small>PO · goods received · invoice</small></h3>
        <table><thead><tr><th>Item</th><th class="num">Ordered</th><th class="num">Received</th><th class="num">Billed</th><th class="num">PO ₹/unit</th><th class="num">Billed ₹/unit</th></tr></thead>
        <tbody>${lineRows}${extras}</tbody></table>
        ${inv.remarks ? `<p style="margin-top:.75rem;color:var(--ink-2)">Vendor remark: “${esc(inv.remarks)}”</p>` : ""}
      </div>
      <div class="panel"><h3>What didn't match</h3><ul class="flags">${flags}</ul></div>
    </div>
    <div>${renderVerdict(d)}</div>
  </div>`;
}

function renderVerdict(d) {
  const dec = d.decision;
  if (d.case) {
    return `<div class="resolved"><h3>Decided: ${esc(d.actions[d.case.final_action])}</h3>
      <p>By ${esc(d.case.decided_by)}. ${esc(d.case.reason)}</p>
      <p><small>Retained to memory as <code>${esc(d.case.memory_doc || "")}</code>. The agent ${d.case.correct ? "had recommended the same." : d.case.agent_action === "escalate" ? "had no precedent; this decision is now the precedent." : "had recommended “" + esc(d.actions[d.case.agent_action]) + "”; this override is now a lesson it will use."}</small></p></div>`;
  }
  const kind = dec.auto_resolved ? "go" : (dec.requires_human || dec.action === "escalate") ? "human" : "review";
  const stamp = dec.action === "verify_bank_change" ? "Payment blocked" : kind === "go" ? "Agent can clear this" : kind === "human" ? "Person required" : "Recommended, please confirm";
  const cites = dec.precedents.length ? `<ol class="cites">${dec.precedents.slice(0, 5).map((p) => `
      <li><div class="act">${esc(state.actions[p.action] || p.action)}${p.magnitude ? ` <small>(${p.exception_type === "price_variance" ? p.magnitude + "%" : inr(p.magnitude)})</small>` : ""}</div>
      <div class="why">${esc(p.reason || p.text)}</div>
      <div class="who">${esc(p.date)}, ${esc(p.case_id)}${p.vendor_id !== d.invoice.vendor_id ? ", another vendor (" + esc(p.vendor_id) + ")" : ""}${p.decided_by ? ", " + esc(p.decided_by) : ""}${p.outcome === "overridden" ? ", <b>override</b>" : ""}</div></li>`).join("")}</ol>`
    : `<p class="lede">Memory has no precedent for this yet. Whatever you decide becomes the first one.</p>`;
  const notes = dec.notes?.length ? `<div class="panel notes"><h3>Also in memory</h3>${dec.notes.slice(0, 3).map((n) => `<p>${esc(n)}</p>`).join("")}</div>` : "";
  const warns = (dec.warnings || []).map((w) => `<div class="warn">${esc(w)}</div>`).join("");
  return `
  <div class="verdict ${kind}">
    <span class="stamp">${stamp}</span>
    <div class="action">${esc(dec.action_label)}</div>
    <div class="rationale">${esc(dec.rationale)}</div>
    ${warns}
    <div class="meter" aria-label="Confidence ${pct(dec.confidence)}">
      <div class="track"><div class="fill" style="width:${Math.round(dec.confidence * 100)}%"></div><div class="line" style="left:${THRESHOLD * 100}%" title="Auto-clear threshold"></div></div>
      <div class="legend"><span>Confidence from memory: <b>${pct(dec.confidence)}</b></span><span>Auto-clear at ${pct(THRESHOLD)}</span></div>
    </div>
    <div class="actions">
      ${d.exceptions.length ? `<button class="primary" id="accept">${dec.action === "escalate" ? "Decide myself" : "Accept recommendation"}</button><button id="change">Decide differently</button>` : `<button class="primary" id="accept">Approve</button>`}
    </div>
    <details class="compare" id="compare"><summary>Same invoice with memory off</summary><div class="off">Loading…</div></details>
  </div>
  <div class="panel" style="margin-top:1rem"><h3>Precedents recalled <small>${dec.precedents.length} from ${esc(dec.memory)}</small></h3>${cites}</div>
  ${notes}
  <p style="font-size:.8rem;color:var(--ink-3);margin-top:.75rem">Engine: ${esc(dec.engine)}${dec.trace?.length ? ", " + dec.trace.length + " LLM steps" : ""}</p>`;
}

function bindCase(d) {
  const dec = d.decision;
  $("#accept")?.addEventListener("click", () => {
    if (dec.action === "escalate" && d.exceptions.length) return openOverride(dec.action);
    resolve(d.invoice.invoice_id, dec.action, "Divya Reddy (AP Executive)", d.exceptions.length ? "Accepted the agent's recommendation." : "Clean match.");
  });
  $("#change")?.addEventListener("click", () => openOverride(dec.action));
  $("#compare")?.addEventListener("toggle", async (e) => {
    if (!e.target.open || e.target.dataset.loaded) return;
    e.target.dataset.loaded = 1;
    const off = $(".off", e.target);
    try {
      const b = await api(`/api/invoice/${d.invoice.invoice_id}/baseline`);
      off.innerHTML = `<div class="action">${esc(b.action_label)} (confidence ${pct(b.confidence)})</div><p>${esc(b.rationale)}</p><small>No precedents, no vendor history: every exception like this lands on a person's desk.</small>`;
    } catch (err) { off.textContent = err.message; }
  });
}

function openOverride(current) {
  const sel = $("#override-action");
  sel.innerHTML = Object.entries(state.actions).filter(([k]) => k !== "escalate").map(([k, v]) => `<option value="${k}" ${k === current ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#override-form").reset(); sel.value = current === "escalate" ? "approve" : current;
  $("#override").showModal();
}
$("#override").addEventListener("close", () => {
  if ($("#override").returnValue !== "save") return;
  const f = new FormData($("#override-form"));
  resolve(state.current, f.get("action"), f.get("decided_by"), f.get("reason"));
});

async function resolve(id, action, by, reason) {
  try {
    const c = await api(`/api/invoice/${id}/resolve`, { method: "POST", body: JSON.stringify({ action, decided_by: by, reason }) });
    toast(c.correct ? "Saved. Decision retained in memory." : "Saved. The override is retained; similar invoices will follow it.");
    const next = state.inbox.find((r) => r.status === "pending" && r.exception_type && r.invoice_id !== id);
    state.current = null;
    await loadInbox();
    openCase(next ? next.invoice_id : id);
  } catch (err) { toast(err.message); }
}

/* ---------------- learning curve ---------------- */
async function loadCurve() {
  const m = await api("/api/metrics");
  const t = m.totals;
  $("#kpis").innerHTML = [
    [pct(t.auto_rate_last4), "of exceptions cleared by the agent in the last 4 weeks"],
    [pct(t.auto_precision), "of agent-cleared exceptions matched the human call"],
    [t.hours_saved + " h", "of AP time saved across " + t.exceptions + " exceptions"],
    [String(t.bank_changes_caught), "bank-change requests stopped for call-back"],
    [inr(t.duplicates_blocked), "in duplicate invoices rejected"],
    [inr(t.discounts_captured), "early-payment discounts taken"],
  ].map(([b, s]) => `<div class="kpi"><b>${b}</b><span>${s}</span></div>`).join("");
  drawChart(m.weeks);
}
function drawChart(weeks) {
  const W = 960, H = 300, L = 44, R = 16, T = 16, B = 34;
  if (!weeks.length) { $("#chart").innerHTML = `<p class="lede" style="padding:2rem 0">No history yet. Run <code>python scripts/replay.py</code>, then refresh.</p>`; return; }
  const n = weeks.length, bw = (W - L - R) / n, maxE = Math.max(...weeks.map((w) => w.exceptions), 1);
  const x = (i) => L + bw * i + bw / 2, yP = (v) => T + (H - T - B) * (1 - v), yE = (v) => T + (H - T - B) * (1 - v / (maxE * 1.15));
  const grid = [0, .25, .5, .75, 1].map((g) => `<line x1="${L}" x2="${W - R}" y1="${yP(g)}" y2="${yP(g)}" stroke="#edf0f3"/><text x="${L - 8}" y="${yP(g) + 4}" text-anchor="end">${g * 100}%</text>`).join("");
  const bars = weeks.map((w, i) => `<rect x="${x(i) - bw * .32}" y="${yE(w.exceptions)}" width="${bw * .64}" height="${H - B - yE(w.exceptions)}" fill="#dfe4ec" rx="3"><title>Week ${w.week}: ${w.exceptions} exceptions</title></rect><text x="${x(i)}" y="${H - B + 18}" text-anchor="middle">W${w.week}</text>`).join("");
  const line = (key, color) => {
    const pts = weeks.map((w, i) => w[key] == null ? null : [x(i), yP(w[key])]).filter(Boolean);
    return `<polyline fill="none" stroke="${color}" stroke-width="2.5" points="${pts.map((p) => p.join(",")).join(" ")}"/>` +
      pts.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="${color}"><title>${key === "accuracy" ? "Matched human" : "Auto-cleared"}: ${Math.round(weeks[i][key] * 100)}%</title></circle>`).join("");
  };
  $("#chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly exceptions, recommendation accuracy and auto-clear rate">${grid}${bars}${line("accuracy", "#2b4acb")}${line("auto_rate", "#1f7a4d")}</svg>`;
}

/* ---------------- vendors ---------------- */
async function loadVendors() {
  const vs = await api("/api/vendors");
  $("#vendor-list").innerHTML = vs.map((v) => `<li><button data-v="${v.id}">${esc(v.name)}<small>${esc(v.category)}, ${v.cases} exception${v.cases === 1 ? "" : "s"} remembered</small></button></li>`).join("");
  $$("#vendor-list button").forEach((b) => b.addEventListener("click", () => loadPlaybook(b.dataset.v, b)));
}
async function loadPlaybook(id, btn) {
  $$("#vendor-list button").forEach((x) => x.removeAttribute("aria-current")); btn.setAttribute("aria-current", "true");
  const el = $("#playbook");
  el.innerHTML = `<p class="lede">Reflecting over everything remembered about this vendor…</p>`;
  try {
    const p = await api(`/api/vendor/${id}/playbook`);
    const s = p.structured || {};
    const rows = (s.patterns || []).map((r) => `<tr><td>${esc(EXC[r.exception_type] || r.exception_type)}</td><td>${esc(state.actions[r.usual_resolution] || r.usual_resolution)}</td><td>${esc(r.tolerance_or_rule || r.detail || "")}</td><td class="num">${esc(r.evidence_count ?? r.occurrences ?? "")}</td></tr>`).join("");
    el.innerHTML = `<h2>${esc(p.vendor.name)}</h2><p class="lede">${esc(p.vendor.category)}, ${esc(p.vendor.city)}. Terms ${esc(p.vendor.terms)}.</p>
      ${s.summary ? `<p class="md">${esc(s.summary)}</p>` : ""}
      ${rows ? `<table class="pattern-table"><thead><tr><th>Exception</th><th>Usual resolution</th><th>Rule or tolerance</th><th class="num">Cases</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
      ${(s.watch_outs || []).length ? `<h3>Watch out for</h3><ul>${s.watch_outs.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
      <h3>Reflection</h3><div class="md">${md(p.text)}</div>`;
  } catch (err) { el.innerHTML = `<p>${esc(err.message)}</p>`; }
}
function md(t) {
  return esc(t).replace(/^### (.*)$/gm, "<h3>$1</h3>").replace(/^## (.*)$/gm, "<h3>$1</h3>").replace(/^# (.*)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/^\s*[-*] (.*)$/gm, "<li>$1</li>").replace(/(<li>.*<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`)
    .replace(/\n{2,}/g, "<br><br>");
}

/* ---------------- memory ---------------- */
async function loadCases() {
  const cs = await api("/api/cases");
  $("#cases").innerHTML = cs.length ? cs.map((c) => `<tr><td>${esc(c.received_at)}</td><td>${esc(c.vendor_name)}</td><td>${esc(EXC[c.exception_type] || c.exception_type)}</td>
    <td><span class="tag ${c.correct ? "t-auto" : c.agent_action === "escalate" ? "t-done" : "t-human"}">${esc(c.agent_action === "escalate" ? "No precedent yet" : c.agent_action.replaceAll("_", " "))}</span></td>
    <td>${esc(c.final_action.replaceAll("_", " "))}</td><td>${esc(c.reason)}<br><small>${esc(c.decided_by)}</small></td></tr>`).join("")
    : `<tr><td colspan="6">No cases yet. Run <code>python scripts/replay.py</code>, then refresh.</td></tr>`;
}

/* ---------------- boot ---------------- */
(async () => {
  try {
    const s = await loadStatus();
    if (s.progress?.running) { showTab("curve"); pollReplay(); }
    loadInbox();
  } catch (err) { toast(err.message); }
})();
