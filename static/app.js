"use strict";
// Single page: overview, inbox, check invoices, batch board, run view, all runs. Everything from an invoice goes through esc().

const view = document.getElementById("view");
const STAGE_NAMES = ["Receive", "Read route", "Extract fields", "AI cross-check", "Check document", "Check vendor", "Find PO", "Check duplicates", "Match PO and receipt", "Decide"];
const STAGE_COUNT = STAGE_NAMES.length;
const AI_STAGES = { 3: "scan", 4: "always" };
const FIELD_LABELS = {
  vendor: "Vendor", invoice_number: "Invoice number", invoice_date: "Invoice date", po_number: "PO number", currency: "Currency",
  subtotal: "Subtotal", tax: "Tax", total: "Total", bank_last4: "Bank last 4", document_type: "Document type",
};
const FORM_FIELDS = Object.keys(FIELD_LABELS);
const DOC_TYPES = ["invoice", "credit_note", "statement", "proforma", "quotation", "reminder"];
const MIN_STAGE_MS = 300; // rule stages take milliseconds; each shows at least this long. Timings shown are real.
const READ_SOURCE = { text_layer: "PDF text", ai: "AI read, not confirmed", clerk_entered: "typed by a person", none: "not read" };
const SOURCE = { email: "Email", upload: "Upload", sample: "Sample" };
const WORD = { APPROVE: "Approved", HOLD: "On hold", REJECT: "Rejected" };
const ICONS = {
  pass: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 6v8M12 18.5v.5"/></svg>',
  fail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M7 7l10 10M17 7L7 17"/></svg>',
  not_run: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M7 12h10"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 15v5h16v-5"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5"/><path d="M4 19h16"/></svg>',
  mail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
  clip: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 11l-8.5 8.5a5 5 0 01-7-7L14 4a3.5 3.5 0 015 5l-8.5 8.5a2 2 0 01-3-3L15 7"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/></svg>',
};
const DECISION_ICON = { APPROVE: ICONS.pass, HOLD: ICONS.warn, REJECT: ICONS.fail };
let config = { samples: [], vendors: [], ai_reader: false, code_names: {} };
let timer = null;

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null || v === "" ? "" : Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const dollars = (v) => "$" + Number(v || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
const titled = (s) => String(s || "").replace(/_/g, " ");
const codeName = (c) => config.code_names[c] || c;
const pill = (decision, text) => decision ? `<span class="pill ${esc(decision)}">${DECISION_ICON[decision] || ""}${esc(text || WORD[decision] || decision)}</span>` : `<span class="pill">${esc(text || "Waiting")}</span>`;
const when = (iso) => { if (!iso) return ""; const d = new Date(iso); return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); };

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `The server answered ${res.status}.`);
  return body;
}
const postJson = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

function stopTimer() { if (timer) { clearTimeout(timer); timer = null; } }

// ---- tooltips for chart marks: any element with data-tip
const tip = document.getElementById("tip");
document.addEventListener("mousemove", (e) => {
  const target = e.target.closest && e.target.closest("[data-tip]");
  if (!target) { tip.hidden = true; return; }
  tip.textContent = target.dataset.tip;
  tip.hidden = false;
  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = (e.clientY + 14) + "px";
});

// ---- routing
function route() {
  stopTimer();
  const hash = location.hash || "#/overview";
  const section = (hash.split("/")[1] || "overview").split("?")[0];
  const active = section === "run" || section === "batch" ? "runs" : section;
  ["overview", "inbox", "new", "runs"].forEach((n) => document.getElementById(`nav-${n}`).classList.toggle("active", n === active));
  window.scrollTo(0, 0);
  refreshHoldCount();
  const run = hash.match(/^#\/run\/([\w-]+)(\/live)?$/);
  if (run) return showRun(run[1], Boolean(run[2]));
  const batch = hash.match(/^#\/batch\/([\w-]+)$/);
  if (batch) return showBatch(batch[1]);
  if (section === "inbox") return showInbox();
  if (section === "new") return showNew();
  if (section === "runs") return showRuns();
  return showOverview();
}
window.addEventListener("hashchange", route);

async function refreshHoldCount() {
  try {
    const data = await api("/api/runs");
    const badge = document.getElementById("hold-count");
    badge.hidden = !data.counts.held;
    badge.textContent = data.counts.held;
    badge.title = `${data.counts.held} waiting for a person`;
  } catch (e) { /* the page itself shows the error */ }
}

// ---- overview
async function showOverview() {
  view.innerHTML = `<p class="muted">Loading...</p>`;
  let m, runs;
  try { [m, runs] = await Promise.all([api("/api/metrics"), api("/api/runs")]); } catch (e) { view.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  const straight = m.invoices ? Math.round((m.auto_approved / m.invoices) * 100) : 0;
  const waiting = runs.runs.filter((r) => !r.superseded && r.status === "done" && r.final_status === "awaiting clerk");
  const hours = (m.minutes_saved / 60).toFixed(1);
  const a = m.assumptions || {};
  view.innerHTML = `
    <div class="page-head">
      <div><div class="eyebrow">Accounts payable, Halden Manufacturing</div><h1>Overview</h1>
        <p>Every invoice is checked against the vendor master, the PO, goods received and earlier invoices before anyone pays it. Rules decide; AI reads and double-checks; a person settles anything unclear.</p></div>
      <div class="actions"><a class="btn" href="#/inbox">${ICONS.mail}Open inbox</a><a class="btn primary" href="#/new">${ICONS.upload}Check invoices</a></div>
    </div>
    <div class="kpis">
      ${kpi("Invoices checked", m.invoices, m.median_seconds != null ? `Median <strong>${m.median_seconds} s</strong> each` : "")}
      ${kpi("Approved straight through", m.auto_approved, `<strong>${straight}%</strong> with no one touching it`)}
      ${kpi("Waiting for a person", m.held, `<strong>${dollars(m.value_held)}</strong> on hold`)}
      ${kpi("Risky payments stopped", dollars(m.at_risk_stopped), "Duplicates, bank changes, blocked vendors")}
    </div>
    <div class="grid-3" style="margin-bottom:20px">
      <section class="card">
        <div class="card-head"><div><h2>Needs attention</h2><p>Held invoices, with who acts next and what to ask for.</p></div><a class="btn small" href="#/runs?filter=HOLD">All holds</a></div>
        ${waiting.length ? `<ul class="attention">${waiting.slice(0, 6).map((r) => `
          <li tabindex="0" data-run="${esc(r.id)}">
            <div class="what">${esc(r.vendor || r.file_name)} <span class="muted small">${esc(r.invoice_number || "")}</span></div>
            <div class="num small" style="text-align:right">${r.gross ? "$" + money(r.gross) : ""}</div>
            <div class="why"><span class="code">${esc(r.codes[0] || "")}</span> ${esc(r.summary || "")}</div>
            <div class="who">Next: ${esc(r.next_step.owner || "AP clerk")}. ${esc(r.next_step.action || "")}</div>
          </li>`).join("")}</ul>` : `<div class="empty">Nothing is waiting. Every invoice checked so far was decided by the rules.</div>`}
      </section>
      <section class="card card-pad impact">
        <h2>Time given back</h2>
        <div class="big num">${hours} h</div>
        <p class="small muted" style="margin:0">Estimated from ${m.invoices} invoices: ${esc(a.manual_minutes_per_invoice)} min each by hand, ${esc(a.review_minutes_per_exception)} min to settle a hold. ${esc(a.note || "")}</p>
        <div class="sect"><div class="label">Payment file</div>
          <p class="small" style="margin-bottom:10px">${m.approved} approved invoice${m.approved === 1 ? "" : "s"}, ${dollars(m.value_approved)}, paid only to the bank account on file.</p>
          <a class="btn" href="/api/export/approved.csv">${ICONS.download}Export for the ERP (CSV)</a></div>
      </section>
    </div>
    <div class="grid-2">
      <section class="card"><div class="card-head"><div><h2>Outcomes</h2><p>Latest decision on each invoice.</p></div></div><div class="card-body">${mixChart(m)}</div></section>
      <section class="card"><div class="card-head"><div><h2>Why invoices stop</h2><p>The first reason on each hold or reject.</p></div></div><div class="card-body">${barChart(m.reasons.map((r) => ({ name: r.name, value: r.count, label: String(r.count), tip: `${r.name} (${r.code}): ${r.count}` })), "No holds or rejects yet.")}</div></section>
      <section class="card"><div class="card-head"><div><h2>Approved value by vendor</h2><p>Gross, approved by the rules or by a person.</p></div></div><div class="card-body">${barChart(m.value_by_vendor.map((v) => ({ name: v.vendor, value: v.value, label: dollars(v.value), tip: `${v.vendor}: $${money(v.value)}` })), "Nothing approved yet.")}</div></section>
      <section class="card"><div class="card-head"><div><h2>Recent activity</h2><p>Your runs and the shared history.</p></div><a class="btn small" href="#/runs">All runs</a></div>
        <div class="table-wrap"><table><tbody>${runs.runs.filter((r) => !r.superseded).slice(0, 6).map((r) => `
          <tr tabindex="0" data-run="${esc(r.id)}"><td>${esc(r.vendor || r.file_name)}<div class="muted small">${esc(r.invoice_number || "")} ${r.history ? "· history" : ""}</div></td>
          <td>${pill(r.decision, r.status !== "done" ? "Checking" : null)}</td><td class="num">${r.gross ? "$" + money(r.gross) : ""}</td></tr>`).join("")}</tbody></table></div></section>
    </div>`;
  bindRows();
}

function kpi(label, value, sub) {
  return `<section class="card kpi"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div><div class="sub">${sub}</div></section>`;
}

function mixChart(m) {
  const parts = [["APPROVE", m.approved], ["HOLD", m.held], ["REJECT", m.rejected]];
  const total = parts.reduce((s, [, n]) => s + n, 0);
  if (!total) return `<div class="empty">No invoices checked yet.</div>`;
  return `<div class="mix" role="img" aria-label="${parts.map(([d, n]) => `${WORD[d]} ${n}`).join(", ")}">
      ${parts.filter(([, n]) => n).map(([d, n]) => `<span class="${d}" style="flex:${n}" data-tip="${WORD[d]}: ${n} of ${total}"></span>`).join("")}</div>
    <div class="legend">${parts.map(([d, n]) => `<span><i class="mix-key" style="background:var(--${d === "APPROVE" ? "good" : d === "HOLD" ? "warning" : "critical"})"></i>${WORD[d]} <strong class="num">${n}</strong></span>`).join("")}</div>`;
}

function barChart(rows, empty) {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  const max = Math.max(...rows.map((r) => r.value));
  return `<div class="bars">${rows.slice(0, 8).map((r) => `
    <div class="bar-row" data-tip="${esc(r.tip)}"><span class="name">${esc(r.name)}</span>
      <span class="track"><span class="fill" style="display:block;width:${Math.max(2, (r.value / max) * 100)}%"></span></span>
      <span class="val">${esc(r.label)}</span></div>`).join("")}</div>`;
}

function bindRows() {
  view.querySelectorAll("[data-run]").forEach((el) => {
    const open = () => { location.hash = `#/run/${el.dataset.run}`; };
    el.addEventListener("click", open);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  });
}

// ---- inbox
async function showInbox() {
  view.innerHTML = `<p class="muted">Loading...</p>`;
  let data;
  try { data = await api("/api/inbox"); } catch (e) { view.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  const fresh = data.messages.filter((m) => !m.batch_id);
  view.innerHTML = `
    <div class="page-head">
      <div><div class="eyebrow">ap@halden-mfg.example</div><h1>Inbox</h1>
        <p>Invoices arrive here by email. Each PDF attachment is checked in the order it arrived, and the sender's address is compared with the vendor's domain on file.</p></div>
      <div class="actions"><button class="btn primary" id="process-all" ${fresh.length ? "" : "disabled"}>${ICONS.spark}Check ${fresh.length ? `all ${fresh.length} new` : "all"} emails</button></div>
    </div>
    <div id="inbox-error"></div>
    <section class="card mailbox">
      <div class="mails" role="listbox" aria-label="Emails">${data.messages.map((m, i) => `
        <button class="mail ${i === 0 ? "sel" : ""}" data-mail="${esc(m.id)}" role="option">
          <div class="top"><span class="from">${esc(m.from_name || m.from)}</span><span class="when">${esc(when(m.date))}</span></div>
          <div class="subj">${esc(m.subject)}</div>
          <div class="meta">${m.attachments.map((a) => `<span class="tag">${ICONS.clip} ${esc(a)}</span>`).join("")}${m.batch_id ? `<span class="pill APPROVE" style="background:var(--accent-soft);color:var(--accent-strong)">Checked</span>` : ""}</div>
        </button>`).join("")}</div>
      <div class="reader" id="reader"></div>
    </section>`;
  const show = (id) => {
    const m = data.messages.find((x) => x.id === id);
    view.querySelectorAll(".mail").forEach((b) => b.classList.toggle("sel", b.dataset.mail === id));
    document.getElementById("reader").innerHTML = `
      <h2>${esc(m.subject)}</h2>
      <div class="addr">From <strong>${esc(m.from_name)}</strong> &lt;${esc(m.from)}&gt; · ${esc(when(m.date))}</div>
      <pre>${esc(m.body)}</pre>
      <div class="chips">${m.attachments.map((a) => `<span class="tag">${ICONS.clip} ${esc(a)}</span>`).join("")}</div>
      <div class="actions">${m.batch_id ? `<a class="btn" href="#/batch/${esc(m.batch_id)}">See the result</a>` : ""}
        <button class="btn ${m.batch_id ? "" : "primary"}" id="process-one">${m.batch_id ? "Check again" : "Check this invoice"}</button></div>`;
    document.getElementById("process-one").onclick = () => processInbox([id]);
  };
  view.querySelectorAll(".mail").forEach((b) => b.addEventListener("click", () => show(b.dataset.mail)));
  if (data.messages.length) show(data.messages[0].id);
  document.getElementById("process-all").onclick = () => processInbox(fresh.map((m) => m.id));
}

async function processInbox(ids) {
  try {
    const { batch_id, run_ids } = await postJson("/api/inbox/process", { ids });
    location.hash = run_ids.length === 1 ? `#/run/${run_ids[0]}/live` : `#/batch/${batch_id}`;
  } catch (e) {
    document.getElementById("inbox-error").innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

// ---- check invoices
function showNew() {
  view.innerHTML = `
    <div class="page-head"><div><h1>Check invoices</h1>
      <p>Drop one invoice to watch every check run, or several to check them as a batch. Each is compared with the approved vendors, the POs, goods received and earlier invoices.</p></div></div>
    <div class="stack">
      <section class="card card-pad">
        <label class="drop" id="drop">
          <input type="file" id="file" accept="application/pdf,.pdf" multiple>
          ${ICONS.upload}
          <strong>Choose PDFs or drop them here</strong>
          <span class="muted small">Up to 25 files, 10 MB and 20 pages each</span>
        </label>
        <div id="new-error"></div>
      </section>
      <section class="card">
        <div class="card-head"><div><h2>Sample invoices</h2><p>Each one shows a different check at work. All made up.</p></div><a class="btn small" href="#/inbox">${ICONS.mail}Or check them from the inbox</a></div>
        <div class="card-body"><div class="samples">${config.samples.map((s) => {
          const [who, what] = s.label.split(": ");
          return `<button class="sample" data-sample="${esc(s.file)}"><span class="k">${esc(s.file.split("-")[0])}</span><span class="t">${esc(what || s.label)}</span><span class="d">${esc(who)}</span></button>`;
        }).join("")}</div></div>
      </section>
    </div>`;
  const fail = (e) => { document.getElementById("new-error").innerHTML = `<div class="error">${esc(e.message)}</div>`; };
  const sendFiles = async (files) => {
    document.getElementById("new-error").innerHTML = "";
    if (!files.length) return;
    const form = new FormData();
    try {
      if (files.length === 1) {
        form.append("file", files[0]);
        const { run_id } = await api("/api/runs", { method: "POST", body: form });
        location.hash = `#/run/${run_id}/live`;
      } else {
        files.forEach((f) => form.append("files", f));
        const { batch_id } = await api("/api/batches", { method: "POST", body: form });
        location.hash = `#/batch/${batch_id}`;
      }
    } catch (e) { fail(e); }
  };
  document.getElementById("file").addEventListener("change", (e) => sendFiles([...e.target.files]));
  const drop = document.getElementById("drop");
  ["dragover", "dragenter"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); sendFiles([...e.dataTransfer.files]); });
  view.querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", async () => {
    const f = new FormData(); f.append("sample", b.dataset.sample);
    try { const { run_id } = await api("/api/runs", { method: "POST", body: f }); location.hash = `#/run/${run_id}/live`; } catch (e) { fail(e); }
  }));
}

// ---- batch board
async function showBatch(batchId) {
  view.innerHTML = `<p class="muted">Loading...</p>`;
  const tick = async () => {
    let data;
    try { data = await api(`/api/batches/${batchId}`); } catch (e) { view.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
    if (location.hash !== `#/batch/${batchId}`) return;
    const rows = data.runs;
    const done = rows.filter((r) => r.status === "done" || r.status === "failed");
    const count = (d) => done.filter((r) => r.decision === d).length;
    const fromEmail = rows.some((r) => r.source === "email");
    if (!document.getElementById("batch-body")) {
      view.innerHTML = `
        <div class="page-head"><div><div class="eyebrow">Batch ${esc(batchId)}</div><h1>${fromEmail ? "Inbox batch" : "Upload batch"}: ${rows.length} invoice${rows.length === 1 ? "" : "s"}</h1>
          <p>Checked one after another in the order they arrived, so each one sees the decisions made before it.</p></div>
          <div class="actions"><a class="btn" href="#/overview">Overview</a><a class="btn" href="#/runs">All runs</a></div></div>
        <div id="batch-body"></div>`;
    }
    document.getElementById("batch-body").innerHTML = `
      <section class="card card-pad" style="margin-bottom:20px">
        <div class="batch-sum"><strong class="num">${done.length} of ${rows.length} checked</strong>
          ${pill("APPROVE", `${count("APPROVE")} approved`)} ${pill("HOLD", `${count("HOLD")} on hold`)} ${pill("REJECT", `${count("REJECT")} rejected`)}</div>
        <div class="progress" style="margin-top:12px"><span style="width:${(done.length / rows.length) * 100}%"></span></div>
      </section>
      <section class="card"><div class="table-wrap"><table>
        <thead><tr><th>Invoice</th><th>${fromEmail ? "Sender" : "File"}</th><th>Status</th><th>Why</th><th class="num">Total</th></tr></thead>
        <tbody>${rows.map((r) => `<tr tabindex="0" data-run="${esc(r.id)}">
          <td><strong>${esc(r.vendor || r.file_name)}</strong><div class="muted small">${esc(r.invoice_number || r.file_name)}</div></td>
          <td class="small">${esc(fromEmail ? r.sender : r.file_name)}</td>
          <td>${r.status === "done" ? pill(r.decision) : r.status === "failed" ? pill("REJECT", "Failed") : r.status === "queued" ? `<span class="muted small">Queued${r.queue_position ? `, ${r.queue_position} in line` : ""}</span>` : `<span class="small">Stage ${r.stage ? r.stage.number : 1} of ${STAGE_COUNT}: ${esc(r.stage ? r.stage.name : "Receive")}</span>`}</td>
          <td class="small">${r.status === "done" ? `<div class="chips">${r.codes.map((c) => `<span class="code" title="${esc(codeName(c))}">${esc(c)}</span>`).join("")}</div>${r.codes.length ? `<div class="muted" style="margin-top:2px">${esc(codeName(r.codes[0]))}</div>` : ""}` : ""}</td>
          <td class="num">${r.gross ? "$" + money(r.gross) : ""}</td></tr>`).join("")}</tbody></table></div></section>`;
    bindRows();
    if (done.length < rows.length) timer = setTimeout(tick, 700);
  };
  tick();
}

// ---- all runs
async function showRuns() {
  view.innerHTML = `<p class="muted">Loading...</p>`;
  let data;
  try { data = await api("/api/runs"); } catch (e) { view.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  const initial = (location.hash.split("filter=")[1] || "").toUpperCase();
  const c = data.counts;
  view.innerHTML = `
    <div class="page-head"><div><h1>All runs</h1><p>Every check, with its decision and output. Your runs plus the shared history. Corrected runs stay on record beside the run that replaced them.</p></div>
      <div class="actions"><a class="btn" href="/api/export/approved.csv">${ICONS.download}Export approved (CSV)</a><button class="btn" id="reset">Reset demo data</button></div></div>
    <section class="card">
      <div class="toolbar">
        <div class="seg" role="group" aria-label="Filter by decision">
          ${[["", `All ${data.runs.length}`], ["APPROVE", `Approved ${c.approved}`], ["HOLD", `On hold ${c.held}`], ["REJECT", `Rejected ${c.rejected}`]].map(([v, t]) => `<button data-f="${v}" class="${v === initial ? "on" : ""}">${t}</button>`).join("")}
        </div>
        <input type="search" id="q" placeholder="Search vendor, invoice, PO, code" aria-label="Search runs">
      </div>
      <div class="table-wrap"><table><thead><tr><th>Run</th><th>Received</th><th>Vendor</th><th>Invoice</th><th>PO</th><th class="num">Total</th><th>Decision</th><th>Final status</th><th>Codes</th><th>Came in by</th><th class="num">Took</th></tr></thead><tbody id="rows"></tbody></table></div>
    </section>`;
  let filter = initial;
  const draw = () => {
    const q = document.getElementById("q").value.trim().toLowerCase();
    const rows = data.runs.filter((r) => (!filter || r.decision === filter) && (!q || [r.vendor, r.invoice_number, r.po_number, r.file_name, r.sender, ...r.codes].join(" ").toLowerCase().includes(q)));
    document.getElementById("rows").innerHTML = rows.length ? rows.map((r) => `
      <tr tabindex="0" data-run="${esc(r.id)}" class="${r.superseded ? "super" : ""}">
        <td class="mono">${esc(r.id)}${r.history ? `<div class="muted small">history</div>` : ""}</td>
        <td style="white-space:nowrap">${esc(when(r.started_at))}</td><td>${esc(r.vendor || r.file_name)}</td><td>${esc(r.invoice_number)}</td><td style="white-space:nowrap">${esc(r.po_number)}</td>
        <td class="num">${money(r.gross)}</td>
        <td>${r.status === "done" ? pill(r.decision) : pill(null, r.status === "failed" ? "Failed" : "Checking")}</td>
        <td class="small">${r.superseded ? "corrected by a later run" : esc(r.final_status)}</td>
        <td><div class="chips">${r.codes.map((x) => `<span class="code" title="${esc(codeName(x))}">${esc(x)}</span>`).join("")}</div></td>
        <td class="small">${esc(r.history ? "History" : SOURCE[r.source] || "Upload")}${r.sender ? `<div class="muted">${esc(r.sender)}</div>` : ""}</td>
        <td class="num">${r.duration_ms != null ? (r.duration_ms / 1000).toFixed(1) + " s" : ""}</td>
      </tr>`).join("") : `<tr><td colspan="11" class="empty">No runs match.</td></tr>`;
    bindRows();
  };
  view.querySelectorAll("[data-f]").forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.f;
    view.querySelectorAll("[data-f]").forEach((x) => x.classList.toggle("on", x === b));
    draw();
  }));
  document.getElementById("q").addEventListener("input", draw);
  document.getElementById("reset").addEventListener("click", async () => {
    if (!confirm("Delete your runs and start again? The shared history stays.")) return;
    await api("/api/reset", { method: "POST" });
    location.hash = "#/runs"; showRuns(); refreshHoldCount();
  });
  draw();
}

// ---- run view
async function showRun(runId, live) {
  view.innerHTML = `<p class="muted">Loading...</p>`;
  let shown = live ? 0 : STAGE_COUNT;
  let lastReveal = 0;
  let drawnFinal = false;
  let pageNumber = 1;

  const tick = async () => {
    let data;
    try {
      data = await api(`/api/runs/${runId}`);
    } catch (e) {
      view.innerHTML = `<div class="card card-pad"><h2>This run cannot be shown</h2><p>${esc(e.message)}</p><a class="btn" href="#/new">Check another invoice</a></div>`;
      return;
    }
    if (location.hash.indexOf(runId) === -1) return;
    const stages = {};
    data.stages.forEach((s) => { stages[s.stage] = s; });
    const isFinished = (n) => stages[n] && stages[n].status !== "running";
    const now = Date.now();
    let fresh = 0;
    if (shown < STAGE_COUNT && isFinished(shown + 1) && now - lastReveal >= MIN_STAGE_MS) {
      shown += 1;
      lastReveal = now;
      fresh = shown;
    }
    const finished = !["running", "queued"].includes(data.run.status) && shown >= STAGE_COUNT;
    render(data, stages, shown, finished, fresh);
    if (finished && !drawnFinal) { drawnFinal = true; renderPage(data, pageNumber); refreshHoldCount(); return; }
    if (!finished) timer = setTimeout(tick, Math.min(MIN_STAGE_MS, 400));
  };

  const render = (data, stages, count, finished, fresh) => {
    const run = data.run;
    if (!document.getElementById("run-root")) {
      view.innerHTML = `
        <div class="page-head">
          <div><div class="eyebrow">${esc(SOURCE[run.source] || "Upload")} · Run ${esc(run.id)}${run.parent_run_id ? `, corrected re-run of ${esc(run.parent_run_id)}` : ""}${data.is_history ? " · shared history" : ""}</div>
            <h1 id="run-title">${esc(run.file_name)}</h1><div class="facts" id="facts"></div></div>
          <div class="actions">${run.batch_id ? `<a class="btn" href="#/batch/${esc(run.batch_id)}">Back to batch</a>` : ""}<a class="btn" href="#/new">Check another</a></div>
        </div>
        <div class="run-grid" id="run-root">
          <div class="doc-col"><div id="page"></div></div>
          <div class="stack"><div id="decision"></div><div id="draft"></div>
            <section class="card card-pad"><div class="timeline-head"><h2>What happened</h2><span class="muted small" id="stage-count"></span></div>
              <div class="progress" style="margin:10px 0 6px"><span id="stage-bar"></span></div><ol class="stages" id="stages"></ol></section>
            <div id="clerk"></div></div>
        </div>`;
      renderPage(data, pageNumber);
    }
    document.getElementById("facts").innerHTML = factsHtml(data);
    document.getElementById("stage-count").textContent = `${Math.min(count, STAGE_COUNT)} of ${STAGE_COUNT} stages`;
    document.getElementById("stage-bar").style.width = `${(Math.min(count, STAGE_COUNT) / STAGE_COUNT) * 100}%`;
    document.getElementById("stages").innerHTML = STAGE_NAMES.map((name, i) => stageHtml(name, i + 1, stages[i + 1], count, run, fresh)).join("");
    if (finished) {
      document.getElementById("decision").innerHTML = decisionHtml(data);
      if (!document.getElementById("clerk").dataset.done) {
        document.getElementById("clerk").dataset.done = "1";
        renderDraft(data);
        renderClerk(data);
      }
    } else {
      const waiting = run.status === "queued" ? (data.queue_position ? `Waiting for ${data.queue_position - 1 ? `${data.queue_position - 1} invoice${data.queue_position - 1 === 1 ? "" : "s"} ahead` : "the invoice ahead"}` : "Starting") : `Stage ${Math.min(count + 1, STAGE_COUNT)} of ${STAGE_COUNT}: ${STAGE_NAMES[Math.min(count, STAGE_COUNT - 1)]}`;
      document.getElementById("decision").innerHTML = `<div class="decision"><div class="verdict-row"><span class="sdot running"></span><span class="verdict" style="color:var(--ink)">Checking...</span></div><div class="muted small">${esc(waiting)}</div></div>`;
    }
  };

  const renderPage = (data, number) => {
    const holder = document.getElementById("page");
    if (!holder) return;
    if (!data.page_size) { holder.innerHTML = `<div class="card card-pad muted">This file cannot be displayed.</div>`; return; }
    const { width, height, pages } = data.page_size;
    const disagree = new Set(((data.run.cross_check || {}).differences || []).map((d) => d.field));
    const marks = data.run.status === "done" && data.run.fields ? Object.entries(data.run.fields).filter(([, f]) => f.rect && (f.page || 0) === number - 1) : [];
    holder.innerHTML = `
      <div class="doc"><img alt="Page ${number} of the invoice" src="/api/runs/${esc(runId)}/pages/${number}.png">
        ${marks.map(([name, f]) => `<div class="mark ${f.state === "CONFLICT" || disagree.has(name) ? "warn" : ""}" data-tip="${esc(FIELD_LABELS[name] || name)}${disagree.has(name) ? ": the two readings disagree" : ""}" style="left:${(f.rect[0] - 2) / width * 100}%;top:${(f.rect[1] - 1) / height * 100}%;width:${(f.rect[2] - f.rect[0] + 4) / width * 100}%;height:${(f.rect[3] - f.rect[1] + 2) / height * 100}%"></div>`).join("")}
      </div>
      ${pages > 1 ? `<div class="pager"><button class="btn small" id="prev" ${number <= 1 ? "disabled" : ""}>Previous</button><span>Page ${number} of ${pages}</span><button class="btn small" id="next" ${number >= pages ? "disabled" : ""}>Next</button></div>` : ""}
      ${marks.length ? `<p class="muted small" style="margin-top:8px">Boxes show where each value was read. Hover one to see which.</p>` : ""}`;
    const dup = ((data.run.result || {}).check_results || []).find((c) => c.name === "duplicates" && c.other_run);
    if (dup) {
      holder.insertAdjacentHTML("beforeend", `<div class="compare"><div class="eyebrow">The earlier invoice, for comparison (<a href="#/run/${esc(dup.other_run)}">run ${esc(dup.other_run)}</a>)</div>
        <div class="doc"><img alt="Page 1 of the earlier invoice" src="/api/runs/${esc(dup.other_run)}/pages/1.png"></div></div>`);
    }
    const go = (n) => { pageNumber = n; renderPage(data, n); };
    const prev = document.getElementById("prev"); if (prev) prev.onclick = () => go(number - 1);
    const next = document.getElementById("next"); if (next) next.onclick = () => go(number + 1);
  };

  tick();
}

function factsHtml(data) {
  const run = data.run;
  const f = run.fields || {};
  const v = (n) => (f[n] || {}).value;
  const vendor = config.vendors.find((x) => x.id === v("vendor"));
  const total = (run.result || {}).gross || v("total");
  const parts = [];
  if (vendor) parts.push(`<span><strong>${esc(vendor.name)}</strong></span>`);
  if (v("invoice_number")) parts.push(`<span>Invoice <strong>${esc(v("invoice_number"))}</strong></span>`);
  if (v("po_number")) parts.push(`<span>PO <strong>${esc(v("po_number"))}</strong></span>`);
  if (total) parts.push(`<span>Total <strong>$${money(total)}</strong></span>`);
  if (run.sender) parts.push(`<span>From <strong>${esc(run.sender)}</strong></span>`);
  return parts.join("");
}

function stageHtml(name, number, stage, count, run, fresh) {
  const revealed = number <= count;
  let status = "pending";
  if (revealed && stage) status = stage.status;
  else if (number === count + 1 && !["queued"].includes(run.status)) status = "running";
  const sentence = revealed && stage ? stage.sentence : status === "running" && stage && stage.status === "running" ? stage.sentence : "";
  const ms = revealed && stage && stage.ms != null ? `${stage.ms >= 1000 ? (stage.ms / 1000).toFixed(1) + " s" : stage.ms + " ms"}` : "";
  const ai = AI_STAGES[number] === "always" || (AI_STAGES[number] === "scan" && run.read_source === "ai");
  return `<li class="stage ${status === "pending" ? "pending" : ""} ${fresh === number ? "fresh" : ""}">
    <div class="sdot ${esc(status)}" role="img" aria-label="${esc(titled(status))}">${status === "running" || status === "pending" ? "" : ICONS[status] || ""}</div>
    <div><div class="stage-title"><span>${esc(name)}${ai ? `<span class="ai">AI</span>` : ""}</span><span class="ms">${esc(ms)}</span></div>
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
  if (d.differences && d.differences.length) {
    return `<table class="mini"><tbody><tr><th></th><th>Rules read</th><th>Claude read</th></tr>${d.differences.map((x) => `<tr><td>${esc(FIELD_LABELS[x.field] || x.field)}</td><td>${esc(x.rules)}</td><td>${esc(x.ai)}</td></tr>`).join("")}</tbody></table>`;
  }
  if (d.checks && Array.isArray(d.checks)) {
    const table = d.checks.find((c) => c.table);
    const receipt = d.checks.find((c) => c.receipt);
    const rows = d.checks.map((c) => `<tr><td>${esc(c.label)}</td><td><span class="st ${esc(c.status)}">${esc(titled(c.status))}</span></td><td>${esc(c.sentence)}</td></tr>`).join("");
    const sums = table ? `<table class="mini"><tbody>
        <tr><td>PO net</td><td class="num">${money(table.table.po_net)}</td></tr>
        <tr><td>Tolerance (lesser of 2% and 250.00)</td><td class="num">${money(table.table.tolerance)}</td></tr>
        <tr><td><strong>Limit</strong></td><td class="num"><strong>${money(table.table.limit)}</strong></td></tr>
        <tr><td>Approved so far on this PO</td><td class="num">${money(table.table.approved_to_date)}</td></tr>
        <tr><td>This invoice, net</td><td class="num">${money(table.table.this_invoice_net)}</td></tr>
        <tr><td><strong>After this invoice</strong></td><td class="num"><strong>${money(table.table.after_this_invoice)}</strong></td></tr>
        ${receipt ? `<tr><td>Goods received (${receipt.receipt.receipts} receipt${receipt.receipt.receipts === 1 ? "" : "s"})</td><td class="num">${money(receipt.receipt.received)}</td></tr>` : ""}
      </tbody></table>` : "";
    return `${sums}${d.checks.length > 1 ? `<details class="more"><summary>${d.checks.length} checks</summary><table class="mini"><tbody>${rows}</tbody></table></details>` : ""}`;
  }
  return "";
}

function decisionHtml(data) {
  const run = data.run;
  if (run.status === "failed") {
    return `<div class="decision REJECT"><div class="verdict">Could not finish</div><p class="summary">This run stopped because of an internal error, and nothing was decided.</p><div class="next"><div class="who">Next step</div><div>Run it again. If it fails again, process this invoice by hand.</div></div></div>`;
  }
  const r = run.result;
  const human = run.final_status && run.final_status.includes("by clerk");
  const section = (label, body) => `<div class="sect"><div class="label">${label}</div>${body}</div>`;
  const differences = r.ai_differences && r.ai_differences.length
    ? section("Where the clerk differed from the AI", r.ai_differences.map((d) => `<div>${esc(FIELD_LABELS[d.field] || d.field)}: AI read ${esc(d.ai)}, clerk entered ${esc(d.clerk)}</div>`).join("")) : "";
  const history = data.chain.length > 1
    ? section("This invoice's runs", data.chain.map((c) => `<div><a href="#/run/${esc(c.id)}">${esc(c.id)}</a>: ${esc(WORD[c.decision] || "checking")}${c.codes.length ? ` (${esc(c.codes.join(", "))})` : ""}</div>`).join("")) : "";
  const changes = data.changes.length
    ? section("Changed by a person", data.changes.map((c) => `<div>${esc(FIELD_LABELS[c.field] || c.field)}: ${esc(c.old_value ?? "blank")} to ${esc(c.new_value)} (${esc(c.who)}, ${esc(c.new_state.toLowerCase())})</div>`).join("")) : "";
  const checks = run.read_source === "ai" ? "Checks ran as a preview only, on values nobody has confirmed" : `${r.checks.ran} of ${r.checks.of} required checks ran${r.checks.not_run.length ? `. Not run: ${esc(r.checks.not_run.map(titled).join(", "))}` : ""}`;
  return `<div class="decision ${esc(r.decision)}">
    <div class="verdict-row"><span class="verdict">${DECISION_ICON[r.decision]}${esc(WORD[r.decision])}</span>
      <div class="chips">${r.codes.map((c) => `<span class="code" title="${esc(codeName(c))}">${esc(c)}</span>`).join("")}</div></div>
    <p class="summary">${esc(r.summary)}</p>
    ${human ? `<div class="next"><div class="who">Final decision by a person</div><div><strong>${esc(run.final_status)}</strong>${data.reject_overturned ? " (reject overturned)" : ""}</div><div class="small">${esc(run.clerk_note)}</div></div>` : ""}
    <div class="next"><div class="who">Next step · ${esc(r.next_step.owner || "")}</div><div>${esc(human ? "Decided. Nothing further here." : r.next_step.action)}</div></div>
    ${r.reasons.length > 1 ? section("All reasons", `<ul class="reasons">${r.reasons.map((x) => `<li><span class="code">${esc(x.code)}</span><span>${esc(x.sentence)}</span></li>`).join("")}</ul>`) : ""}
    ${r.notes.length ? section("Notes", `<ul class="notes">${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`) : ""}
    ${changes}${differences}${history}
    <div class="runline">${checks}. Read from: ${esc(READ_SOURCE[run.read_source] || "")}. <a href="/api/runs/${esc(run.id)}/download">Download the full record (JSON)</a></div>
  </div>`;
}

// ---- drafted message
function renderDraft(data) {
  const holder = document.getElementById("draft");
  const run = data.run;
  const human = run.final_status && run.final_status.includes("by clerk");
  if (run.status !== "done" || !["HOLD", "REJECT"].includes(run.decision) || human || !data.is_latest) { holder.innerHTML = ""; return; }
  const show = (d) => {
    holder.innerHTML = `<section class="card card-pad draft-box">
      <div class="timeline-head"><h2>${d.kind === "call" ? "Call note" : "Message to send"}</h2><span class="source">${d.source === "claude" ? "Written by Claude from the facts above" : "Standard template"}</span></div>
      <div class="to">${d.kind === "call" ? "" : "To: "}${esc(d.to)}</div>
      ${d.note ? `<div class="notice">${esc(d.note)}</div>` : ""}
      <input type="text" id="draft-subject" value="${esc(d.subject)}" aria-label="Subject">
      <textarea id="draft-body" aria-label="Message">${esc(d.body)}</textarea>
      <div class="actions"><button class="btn primary" id="copy-draft">Copy</button><button class="btn ghost" id="redo-draft">Write it again</button>
        <span class="muted small">Read it before sending. Nothing is sent from here.</span></div></section>`;
    document.getElementById("copy-draft").onclick = async (e) => {
      const text = `${document.getElementById("draft-subject").value}\n\n${document.getElementById("draft-body").value}`;
      try { await navigator.clipboard.writeText(text); e.target.textContent = "Copied"; } catch (err) { e.target.textContent = "Select and copy by hand"; }
    };
    document.getElementById("redo-draft").onclick = make;
  };
  const make = async () => {
    holder.innerHTML = `<section class="card card-pad"><div class="verdict-row"><span class="sdot running"></span><span>${config.ai_reader ? "Claude is writing the message from the facts above..." : "Filling in the template..."}</span></div></section>`;
    try { show(await postJson(`/api/runs/${run.id}/draft`, {})); } catch (e) { holder.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
  };
  if (data.draft) return show(data.draft);
  holder.innerHTML = `<section class="card card-pad"><div class="timeline-head"><div><h2>Write the follow-up</h2>
      <p class="muted small" style="margin:4px 0 0">${run.decision === "REJECT" ? "The rejection, for the vendor." : "The message or call that gets this invoice moving, to the right person, with the exact figures."}</p></div>
      <button class="btn" id="make-draft">${ICONS.spark}Draft it</button></div></section>`;
  document.getElementById("make-draft").onclick = make;
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
    <div class="stack">
    <section class="card card-pad">
      <h2>${isScan ? "Confirm the values against the page" : isHold ? "Correct or fill in the fields" : "If this reading is wrong"}</h2>
      <p class="muted small" style="margin:4px 0 14px">${isScan ? "Check each value against the page, then re-run the checks." : "Blank or unclear fields can be filled in. A value printed on the invoice is locked; changing one always ends in a hold that needs a note."}</p>
      <form id="clerk-form"><div class="form-grid">${FORM_FIELDS.map((n) => fieldHtml(n, run.fields[n] || { state: "MISSING" }, isScan)).join("")}</div>
        <div id="form-error"></div>
        <div class="actions" style="margin-top:16px"><button class="btn primary" type="submit">Re-run the checks</button></div>
      </form>
    </section>
    ${isHold ? finalHtml(data) : ""}</div>`;
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
  return `<section class="card card-pad">
    <h2>Final decision</h2>
    <p class="muted small" style="margin:4px 0 14px">After you've spoken to the buyer or vendor. The process's own decision stays on record beside yours. ${esc(running)}</p>
    <div class="fld"><label for="final-note">Note (required): who confirmed what</label><textarea id="final-note" rows="2"></textarea></div>
    ${blocker ? `<p class="small" style="margin-top:8px;color:var(--warning-ink)">${esc(blocker)}</p>` : ""}
    <div id="final-error"></div>
    <div class="actions" style="margin-top:14px">
      <button class="btn" data-final="approve" ${blocker ? "disabled" : ""}>Approve for payment</button>
      <button class="btn" data-final="reject">Reject</button>
    </div>
  </section>`;
}

// ---- start
(async () => {
  try { config = await api("/api/config"); } catch (e) { view.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  const badge = document.getElementById("ai-badge");
  badge.querySelector(".t").textContent = config.ai_reader ? "AI reading and cross-check on" : "AI off: rules only";
  badge.classList.toggle("on", config.ai_reader);
  badge.title = config.ai_reader ? "Claude reads scans for a person to confirm, and double-checks every text PDF." : "Scans need a person to type the values; text PDFs are read by the rules alone.";
  route();
})();
