"use strict";
// Single page: new invoice, run view, dashboard. Everything from an invoice goes through esc().

const view = document.getElementById("view");
const STAGE_NAMES = ["Receive", "Read route", "Extract fields", "Check document", "Check vendor", "Find PO", "Check duplicates", "Match and tolerance", "Decide"];
const FIELD_LABELS = {
  vendor: "Vendor", invoice_number: "Invoice number", invoice_date: "Invoice date", po_number: "PO number", currency: "Currency",
  subtotal: "Subtotal", tax: "Tax", total: "Total", bank_last4: "Bank last 4", document_type: "Document type",
};
const FORM_FIELDS = Object.keys(FIELD_LABELS);
const DOC_TYPES = ["invoice", "credit_note", "statement", "proforma", "quotation", "reminder"];
const MIN_STAGE_MS = 300; // rule stages take milliseconds; each shows at least this long. Timings shown are real.
const ICON = { pass: "✓", warn: "!", fail: "×", not_run: "–", pending: "" };
let config = { samples: [], vendors: [], ai_reader: false };
let timer = null;

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null ? "" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const titled = (s) => String(s || "").replace(/_/g, " ");
const READ_SOURCE = { text_layer: "PDF text", ai: "AI read, not confirmed", clerk_entered: "typed by a person", none: "not read" };

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `The server answered ${res.status}.`);
  return body;
}
const postJson = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function stopTimer() { if (timer) { clearTimeout(timer); timer = null; } }

// ---- routing
function route() {
  stopTimer();
  const hash = location.hash || "#/new";
  document.getElementById("nav-new").classList.toggle("active", hash === "#/new");
  document.getElementById("nav-dashboard").classList.toggle("active", hash === "#/dashboard");
  window.scrollTo(0, 0);
  const live = hash.match(/^#\/run\/([\w-]+)(\/live)?$/);
  if (live) return showRun(live[1], Boolean(live[2]));
  if (hash === "#/dashboard") return showDashboard();
  return showNew();
}
window.addEventListener("hashchange", route);

// ---- new invoice
function showNew() {
  view.innerHTML = `
    <div class="intro">
      <h1>Check an invoice</h1>
      <p class="muted">Upload a vendor invoice. It is compared with the approved vendors, the POs and earlier invoices, and you get approve, hold or reject with the reason and the next step.</p>
    </div>
    <div class="new-grid">
      <section class="card">
        <h2>Upload a PDF</h2>
        <label class="drop" id="drop">
          <input type="file" id="file" accept="application/pdf,.pdf">
          <strong>Choose a PDF</strong> <span class="muted">or drop it here</span>
          <div class="muted small">Up to 10 MB and 20 pages</div>
        </label>
        <div id="new-error"></div>
      </section>
      <section class="card">
        <h2>Or use a sample invoice</h2>
        <div>${config.samples.map((s) => `<button class="sample" data-sample="${esc(s.file)}"><span>${esc(s.label)}</span><code>${esc(s.file.split("-")[0])}</code></button>`).join("")}</div>
      </section>
    </div>`;
  const send = async (form) => {
    document.getElementById("new-error").innerHTML = "";
    try {
      const { run_id } = await api("/api/runs", { method: "POST", body: form });
      location.hash = `#/run/${run_id}/live`;
    } catch (e) {
      document.getElementById("new-error").innerHTML = `<div class="error">${esc(e.message)}</div>`;
    }
  };
  const sendFile = (file) => { const f = new FormData(); f.append("file", file); send(f); };
  document.getElementById("file").addEventListener("change", (e) => e.target.files[0] && sendFile(e.target.files[0]));
  const drop = document.getElementById("drop");
  ["dragover", "dragenter"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files[0]) sendFile(e.dataTransfer.files[0]); });
  view.querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", () => { const f = new FormData(); f.append("sample", b.dataset.sample); send(f); }));
}

// ---- run view
async function showRun(runId, live) {
  view.innerHTML = `<p class="muted">Loading...</p>`;
  let shown = live ? 0 : 9;
  let lastReveal = 0;
  let drawnFinal = false;
  let pageNumber = 1;

  const tick = async () => {
    let data;
    try {
      data = await api(`/api/runs/${runId}`);
    } catch (e) {
      view.innerHTML = `<div class="card"><h2>This run cannot be shown</h2><p>${esc(e.message)}</p><a class="btn" href="#/new">Check another invoice</a></div>`;
      return;
    }
    if (location.hash.indexOf(runId) === -1) return;
    const stages = {};
    data.stages.forEach((s) => { stages[s.stage] = s; });
    const isFinished = (n) => stages[n] && stages[n].status !== "running";
    const now = Date.now();
    while (shown < 9 && isFinished(shown + 1) && now - lastReveal >= MIN_STAGE_MS) {
      shown += 1;
      lastReveal = now;
      break;
    }
    const finished = data.run.status !== "running" && shown >= 9;
    render(data, stages, shown, finished);
    if (finished && !drawnFinal) { drawnFinal = true; renderPage(data, pageNumber); return; }
    if (!finished) timer = setTimeout(tick, Math.min(MIN_STAGE_MS, 400));
  };

  const render = (data, stages, count, finished) => {
    const run = data.run;
    if (!document.getElementById("run-root")) {
      view.innerHTML = `
        <div class="run-head">
          <div><h1>${esc(run.file_name)}</h1><div class="muted small">Run ${esc(run.id)}${run.parent_run_id ? `, a corrected re-run of ${esc(run.parent_run_id)}` : ""}${data.is_history ? ", from the history everyone shares" : ""}</div></div>
          <div class="actions"><a class="btn" href="#/new">Check another</a><a class="btn" href="#/dashboard">Dashboard</a></div>
        </div>
        <div class="run-grid" id="run-root">
          <div class="doc-col"><div id="page"></div></div>
          <div><div id="decision"></div><section class="card"><h2>What happened</h2><ol class="stages" id="stages"></ol></section><div id="clerk"></div></div>
        </div>`;
      renderPage(data, pageNumber);
    }
    document.getElementById("stages").innerHTML = STAGE_NAMES.map((name, i) => stageHtml(name, i + 1, stages[i + 1], count)).join("");
    if (finished) {
      document.getElementById("decision").innerHTML = decisionHtml(data);
      if (!document.getElementById("clerk").dataset.done) { document.getElementById("clerk").dataset.done = "1"; renderClerk(data); }
    } else {
      document.getElementById("decision").innerHTML = `<div class="decision running"><div class="verdict">Running...</div><div class="muted small">Stage ${Math.min(count + 1, 9)} of 9: ${esc(STAGE_NAMES[Math.min(count, 8)])}</div></div>`;
    }
  };

  const renderPage = (data, number) => {
    const holder = document.getElementById("page");
    if (!holder) return;
    if (!data.page_size) { holder.innerHTML = `<div class="card muted">This file cannot be displayed.</div>`; return; }
    const { width, height, pages } = data.page_size;
    const marks = data.run.status === "done" && data.run.fields ? Object.entries(data.run.fields).filter(([, f]) => f.rect && (f.page || 0) === number - 1) : [];
    holder.innerHTML = `
      <div class="doc"><img alt="Page ${number} of the invoice" src="/api/runs/${esc(runId)}/pages/${number}.png">
        ${marks.map(([name, f]) => `<div class="mark ${f.state === "CONFLICT" ? "warn" : ""}" title="${esc(FIELD_LABELS[name] || name)}" style="left:${(f.rect[0] - 2) / width * 100}%;top:${(f.rect[1] - 1) / height * 100}%;width:${(f.rect[2] - f.rect[0] + 4) / width * 100}%;height:${(f.rect[3] - f.rect[1] + 2) / height * 100}%"></div>`).join("")}
      </div>
      ${pages > 1 ? `<div class="pager"><button class="btn" id="prev" ${number <= 1 ? "disabled" : ""}>Previous</button><span>Page ${number} of ${pages}</span><button class="btn" id="next" ${number >= pages ? "disabled" : ""}>Next</button></div>` : ""}
      ${marks.length ? `<p class="muted small" style="margin-top:8px">Highlights show where each value was read.</p>` : ""}`;
    const dup = ((data.run.result || {}).check_results || []).find((c) => c.name === "duplicates" && c.other_run);
    if (dup) {
      holder.insertAdjacentHTML("beforeend", `<div class="compare"><div class="label">THE EARLIER INVOICE, FOR COMPARISON (<a href="#/run/${esc(dup.other_run)}">RUN ${esc(dup.other_run)}</a>)</div>
        <div class="doc"><img alt="Page 1 of the earlier invoice" src="/api/runs/${esc(dup.other_run)}/pages/1.png"></div></div>`);
    }
    const go = (n) => { pageNumber = n; renderPage(data, n); };
    const prev = document.getElementById("prev"); if (prev) prev.onclick = () => go(number - 1);
    const next = document.getElementById("next"); if (next) next.onclick = () => go(number + 1);
  };

  tick();
}

function stageHtml(name, number, stage, count) {
  const revealed = number <= count;
  let status = "pending";
  if (revealed && stage) status = stage.status;
  else if (number === count + 1 && stage) status = "running";
  else if (number === count + 1) status = "running";
  const sentence = revealed && stage ? stage.sentence : status === "running" && stage && stage.status === "running" ? stage.sentence : "";
  const ms = revealed && stage && stage.ms != null ? `${stage.ms >= 1000 ? (stage.ms / 1000).toFixed(1) + " s" : stage.ms + " ms"}` : "";
  return `<li class="stage ${status === "pending" ? "pending" : ""}">
    <div class="dot ${esc(status)}" aria-label="${esc(status)}">${ICON[status] || ""}</div>
    <div><div class="stage-title"><span>${number}. ${esc(name)}</span><span class="ms">${esc(ms)}</span></div>
      <div class="stage-sentence">${esc(sentence)}</div>${revealed && stage ? stageDetail(stage) : ""}</div></li>`;
}

function stageDetail(stage) {
  const d = stage.detail;
  if (!d) return "";
  if (d.fields) {
    const rows = FORM_FIELDS.map((n) => {
      const f = d.fields[n] || { state: "MISSING" };
      const value = f.state === "CONFLICT" ? (f.readings || []).join(" or ") : n === "vendor" ? f.printed || f.value : f.value === "NONE" ? "None printed" : f.value;
      return `<tr><td>${esc(FIELD_LABELS[n])}</td><td>${esc(value)}</td><td><span class="st ${esc(f.state)}">${esc(f.state === "PROPOSED" ? "AI READ" : f.state)}</span></td></tr>`;
    }).join("");
    return `<details class="more"><summary>Fields read</summary><table class="mini"><tbody>${rows}</tbody></table></details>`;
  }
  if (d.checks && Array.isArray(d.checks)) {
    const table = d.checks.find((c) => c.table);
    const rows = d.checks.map((c) => `<tr><td>${esc(c.label)}</td><td><span class="st ${esc(c.status)}">${esc(titled(c.status))}</span></td><td>${esc(c.sentence)}</td></tr>`).join("");
    const sums = table ? `<table class="mini"><tbody>
        <tr><td>PO net</td><td class="num">${money(table.table.po_net)}</td></tr>
        <tr><td>Tolerance (lesser of 2% and 250.00)</td><td class="num">${money(table.table.tolerance)}</td></tr>
        <tr><td><strong>Limit</strong></td><td class="num"><strong>${money(table.table.limit)}</strong></td></tr>
        <tr><td>Approved so far on this PO</td><td class="num">${money(table.table.approved_to_date)}</td></tr>
        <tr><td>This invoice, net</td><td class="num">${money(table.table.this_invoice_net)}</td></tr>
        <tr><td><strong>After this invoice</strong></td><td class="num"><strong>${money(table.table.after_this_invoice)}</strong></td></tr>
      </tbody></table>` : "";
    return `${sums}${d.checks.length > 1 ? `<details class="more"><summary>${d.checks.length} checks</summary><table class="mini"><tbody>${rows}</tbody></table></details>` : ""}`;
  }
  return "";
}

function decisionHtml(data) {
  const run = data.run;
  if (run.status === "failed") {
    return `<div class="decision REJECT"><div class="verdict">Could not finish</div><p class="summary">This run stopped because of an internal error, and nothing was decided.</p><div class="next"><div class="label">NEXT STEP</div><div>Run it again. If it fails again, process this invoice by hand.</div></div></div>`;
  }
  const r = run.result;
  const human = run.final_status && run.final_status.includes("by clerk");
  const differences = r.ai_differences && r.ai_differences.length
    ? `<div class="next small"><div class="label">WHERE THE CLERK DIFFERED FROM THE AI</div>${r.ai_differences.map((d) => `<div>${esc(FIELD_LABELS[d.field] || d.field)}: AI read ${esc(d.ai)}, clerk entered ${esc(d.clerk)}</div>`).join("")}</div>` : "";
  const history = data.chain.length > 1
    ? `<div class="next small"><div class="label">THIS INVOICE'S RUNS</div>${data.chain.map((c) => `<div><a href="#/run/${esc(c.id)}">${esc(c.id)}</a>: ${esc(c.decision || "running")}${c.codes.length ? ` (${esc(c.codes.join(", "))})` : ""}</div>`).join("")}</div>` : "";
  const changes = data.changes.length
    ? `<div class="next small"><div class="label">CHANGED BY A PERSON</div>${data.changes.map((c) => `<div>${esc(FIELD_LABELS[c.field] || c.field)}: ${esc(c.old_value ?? "blank")} to ${esc(c.new_value)} (${esc(c.who)}, ${esc(c.new_state.toLowerCase())})</div>`).join("")}</div>` : "";
  return `<div class="decision ${esc(r.decision)}">
    <div class="verdict-row"><div class="verdict">${esc(r.decision)}</div>
      <div class="chips">${r.codes.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div></div>
    <p class="summary">${esc(r.summary)}</p>
    ${human ? `<div class="next"><div class="label">FINAL DECISION BY A PERSON</div><div><strong>${esc(run.final_status)}</strong>${data.reject_overturned ? " (Reject overturned)" : ""}</div><div class="small">${esc(run.clerk_note)}</div></div>` : ""}
    <div class="next"><div class="label">NEXT STEP, ${esc((r.next_step.owner || "").toUpperCase())}</div><div>${esc(human ? "Decided. Nothing further here." : r.next_step.action)}</div></div>
    ${r.reasons.length > 1 ? `<div class="next"><div class="label">ALL REASONS</div><ul class="reasons">${r.reasons.map((x) => `<li><span class="chip">${esc(x.code)}</span> ${esc(x.sentence)}</li>`).join("")}</ul></div>` : ""}
    ${r.notes.length ? `<ul class="notes">${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    ${changes}${differences}${history}
    <div class="next small muted">${run.read_source === "ai" ? "Checks ran as a preview only, on values nobody has confirmed" : `${r.checks.ran} of ${r.checks.of} checks ran${r.checks.not_run.length ? `. Not run: ${esc(r.checks.not_run.map(titled).join(", "))}` : ""}`}. Read from: ${esc(READ_SOURCE[run.read_source] || "")}.
      <a href="/api/runs/${esc(run.id)}/download">Download JSON</a></div>
  </div>`;
}

// ---- clerk panels
function renderClerk(data) {
  const holder = document.getElementById("clerk");
  const run = data.run;
  const canAct = run.status === "done" && data.is_latest && !data.is_history && (run.final_status === "awaiting clerk" || run.final_status === "rejected");
  if (!canAct) { holder.innerHTML = ""; return; }
  const isHold = run.final_status === "awaiting clerk";
  const isScan = run.read_source !== "text_layer";
  holder.innerHTML = `
    <section class="card">
      <h2>${isScan ? "Confirm the values against the page" : isHold ? "Correct or fill in the fields" : "If this reading is wrong"}</h2>
      <p class="muted small">${isScan ? "Check each value against the page on the left, then re-run the checks." : "Blank or unclear fields can be filled in. A value printed on the invoice is locked. Changing one always ends in a hold that needs a note."}</p>
      <form id="clerk-form"><div class="form-grid">${FORM_FIELDS.map((n) => fieldHtml(n, run.fields[n] || { state: "MISSING" }, isScan)).join("")}</div>
        <div id="form-error"></div>
        <div class="actions" style="margin-top:16px"><button class="btn primary" type="submit">Re-run the checks</button></div>
      </form>
    </section>
    ${isHold ? finalHtml(data) : ""}`;
  holder.querySelectorAll("[data-unlock]").forEach((b) => b.addEventListener("click", () => {
    const input = holder.querySelector(`[name="${b.dataset.unlock}"]`);
    input.disabled = false; input.focus();
    b.textContent = "Unlocked: a change here will hold for a note";
    b.disabled = true;
  }));
  const none = holder.querySelector("#bank-none");
  if (none) none.addEventListener("change", () => { const i = holder.querySelector('[name="bank_last4"]'); i.disabled = none.checked; if (none.checked) i.value = ""; });
  holder.querySelector("#clerk-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const values = {};
    FORM_FIELDS.forEach((n) => {
      const el = holder.querySelector(`[name="${n}"]`);
      if (el && !el.disabled && el.value.trim()) values[n] = el.value;
    });
    if (none && none.checked) values.bank_last4 = "NONE";
    if (!isScan && (run.fields.bank_last4 || {}).value === "NONE") delete values.bank_last4;
    try {
      const { run_id } = await postJson(`/api/runs/${run.id}/rerun`, { values });
      location.hash = `#/run/${run_id}/live`;
    } catch (err) {
      holder.querySelector("#form-error").innerHTML = `<div class="error">${esc(err.message)}</div>`;
    }
  });
  holder.querySelectorAll("[data-final]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await postJson(`/api/runs/${run.id}/final`, { decision: b.dataset.final, note: holder.querySelector("#final-note").value });
      if (location.hash.endsWith("/live")) location.hash = `#/run/${run.id}`;
      else route();
    } catch (err) {
      holder.querySelector("#final-error").innerHTML = `<div class="error">${esc(err.message)}</div>`;
    }
  }));
}

function fieldHtml(name, field, isScan) {
  const label = FIELD_LABELS[name];
  const locked = !isScan && (field.state === "FOUND" || field.state === "OVERRIDDEN");
  const value = field.value === "NONE" ? "" : field.value ?? "";
  const attrs = `name="${esc(name)}" id="f-${esc(name)}" ${locked ? "disabled" : ""}`;
  let control;
  if (name === "vendor") {
    control = `<select ${attrs}><option value="">Choose...</option>${config.vendors.map((v) => `<option value="${esc(v.id)}" ${v.id === field.value ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select>`;
  } else if (name === "document_type") {
    control = `<select ${attrs}><option value="">Choose...</option>${DOC_TYPES.map((d) => `<option value="${d}" ${d === field.value ? "selected" : ""}>${esc(titled(d))}</option>`).join("")}</select>`;
  } else if (field.state === "CONFLICT" && (field.readings || []).length) {
    control = `<select ${attrs}><option value="">Choose one...</option>${field.readings.map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join("")}</select>`;
  } else {
    const hint = name === "invoice_date" ? "YYYY-MM-DD" : name === "currency" ? "USD" : name === "bank_last4" ? "Last 4 digits" : "";
    control = `<input type="text" ${attrs} value="${esc(value)}" placeholder="${hint}" autocomplete="off">`;
  }
  const stateText = { FOUND: "read from the page", MISSING: "not found", CONFLICT: "unclear", PROPOSED: "AI read", ENTERED: "entered", OVERRIDDEN: "overridden" }[field.state] || "";
  return `<div class="fld ${field.flag ? "flagged" : ""}">
    <label for="f-${esc(name)}"><span>${esc(label)}</span><span class="st ${esc(field.state)}">${esc(stateText)}</span></label>
    ${control}
    ${field.flag ? `<div class="flag">${esc(field.flag)}</div>` : ""}
    ${field.reason ? `<div class="hint">${esc(field.reason)}</div>` : ""}
    ${locked && field.value === "NONE" ? `<div class="hint">No bank details printed</div>` : ""}
    ${locked ? `<button type="button" class="linkish" data-unlock="${esc(name)}">This reading is wrong</button>` : ""}
    ${name === "bank_last4" && (isScan || field.state === "MISSING") ? `<label class="check"><input type="checkbox" id="bank-none"> No bank details printed</label>` : ""}
  </div>`;
}

function finalHtml(data) {
  const blocker = data.approve_blocker;
  const numbers = (data.run.result || {}).numbers || {};
  const running = numbers.after_this_invoice ? `Approving takes the PO to ${money(numbers.after_this_invoice)} (limit ${money(numbers.limit)}).` : "";
  return `<section class="card">
    <h2>Final decision</h2>
    <p class="muted small">After you've spoken to the buyer or vendor. The process's own decision stays on record beside yours. ${esc(running)}</p>
    <div class="fld"><label for="final-note">Note (required): who confirmed what</label><textarea id="final-note" rows="2"></textarea></div>
    ${blocker ? `<p class="small" style="margin-top:8px;color:var(--warn)">${esc(blocker)}</p>` : ""}
    <div id="final-error"></div>
    <div class="actions" style="margin-top:16px">
      <button class="btn" data-final="approve" ${blocker ? "disabled" : ""}>Approve</button>
      <button class="btn" data-final="reject">Reject</button>
    </div>
  </section>`;
}

// ---- dashboard
async function showDashboard() {
  view.innerHTML = `<p class="muted">Loading...</p>`;
  let data;
  try { data = await api("/api/runs"); } catch (e) { view.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  const c = data.counts;
  view.innerHTML = `
    <h1>Dashboard</h1>
    <p class="muted" style="margin-bottom:24px">Every invoice run, with its status and output. Your runs, plus the history everyone shares.</p>
    <div class="stats">
      ${[["Approved", c.approved], ["Awaiting a person", c.held], ["Rejected", c.rejected], ["All runs", data.runs.length]].map(([t, n]) => `<div class="stat"><div class="n">${n}</div><div class="t">${t}</div></div>`).join("")}
    </div>
    <section class="card">
      <div class="bar">
        <select id="filter" aria-label="Filter by decision"><option value="">All decisions</option><option value="APPROVE">Approved</option><option value="HOLD">On hold</option><option value="REJECT">Rejected</option></select>
        <button class="btn" id="reset">Reset demo data</button>
      </div>
      <div class="scroll"><table><thead><tr><th>Run</th><th>Time</th><th>File</th><th>Vendor</th><th>Invoice</th><th>PO</th><th class="num">Gross</th><th>Process</th><th>Final status</th><th>Codes</th><th>Read from</th><th class="num">Took</th></tr></thead><tbody id="rows"></tbody></table></div>
    </section>`;
  const draw = (filter) => {
    const rows = data.runs.filter((r) => !filter || r.decision === filter);
    document.getElementById("rows").innerHTML = rows.length ? rows.map((r) => `
      <tr tabindex="0" data-run="${esc(r.id)}" class="${r.superseded ? "super" : ""}">
        <td>${esc(r.id)}${r.history ? `<div class="muted small">history</div>` : ""}</td>
        <td style="white-space:nowrap">${esc((r.started_at || "").replace("T", " ").slice(5, 16))}</td><td>${esc(r.file_name)}</td><td>${esc(r.vendor)}</td><td>${esc(r.invoice_number)}</td><td style="white-space:nowrap">${esc(r.po_number)}</td>
        <td class="num">${money(r.gross)}</td>
        <td><span class="pill ${esc(r.decision || "none")}">${esc(r.decision || r.status)}</span></td>
        <td>${r.superseded ? "corrected by a later run" : esc(r.final_status)}</td>
        <td><div class="chips">${r.codes.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div></td>
        <td>${esc(READ_SOURCE[r.read_source] || "")}</td>
        <td class="num">${r.duration_ms != null ? (r.duration_ms / 1000).toFixed(1) + " s" : ""}</td>
      </tr>`).join("") : `<tr><td colspan="12" class="muted">No runs match this filter.</td></tr>`;
    document.querySelectorAll("#rows [data-run]").forEach((tr) => {
      const open = () => { location.hash = `#/run/${tr.dataset.run}`; };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    });
  };
  draw("");
  document.getElementById("filter").addEventListener("change", (e) => draw(e.target.value));
  document.getElementById("reset").addEventListener("click", async () => { await api("/api/reset", { method: "POST" }); showDashboard(); });
}

// ---- start
(async () => {
  try { config = await api("/api/config"); } catch (e) { view.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  const badge = document.getElementById("ai-badge");
  badge.textContent = config.ai_reader ? "AI reader: on" : "AI reader: off";
  badge.classList.toggle("on", config.ai_reader);
  badge.title = config.ai_reader ? "Scans are read by Claude, then confirmed by a person." : "Scans need a person to type the values.";
  route();
})();
