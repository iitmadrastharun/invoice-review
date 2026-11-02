"""Runs one invoice through the ten stages and records each as it happens."""
import json
import queue
import re
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from engine import ai_reader, extract, read, store
from engine.rules import FIELD_LABELS, STAGE_CHECKS, Evaluation, fmt, normalise_number, normalise_po

STAGES = {1: "Receive", 2: "Read route", 3: "Extract fields", 4: "AI cross-check", 5: "Check document", 6: "Check vendor",
          7: "Find PO", 8: "Check duplicates", 9: "Match PO and receipt", 10: "Decide"}
LAST = max(STAGES)
FORM_FIELDS = ["vendor", "invoice_number", "invoice_date", "po_number", "currency", "subtotal", "tax", "total", "bank_last4", "document_type"]
UPLOADS = store.VAR / "runs"

# One run at a time, so a ledger read and write can never interleave.
_run_lock = threading.Lock()
# Runs wait here in arrival order, so a batch is decided in the order it came in.
_queue = queue.Queue()
_worker = None
# Claude's independent readings start as soon as a file arrives, a few at a time, while earlier runs are decided.
_ai_pool = ThreadPoolExecutor(max_workers=3)
_cross_reads = {}


class ClerkInputError(ValueError):
    pass


def _timed(run_id, number, status_fn):
    start = time.perf_counter()
    store.stage(run_id, number, STAGES[number], "running", "Working...")
    status, sentence, detail = status_fn()
    store.stage(run_id, number, STAGES[number], status, sentence, detail, int((time.perf_counter() - start) * 1000))
    return status


def new_run(source_path, file_name, workspace, sha256=None, source="upload", sender=None, subject=None, batch_id=None):
    """Creates the run record and stores the file. Returns the run id; call process() or enqueue() next."""
    sha256 = sha256 or read.sha256_of(source_path)
    run_id, case_id = store.create_run(workspace, file_name, "", sha256)
    target = UPLOADS / run_id / "invoice.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target)
    store.update_run(run_id, file_path=str(target), source=source, sender=sender, subject=subject, batch_id=batch_id, status="queued")
    if workspace != store.REPLAY:
        _start_cross_read(run_id, target, sha256)
    return run_id


def enqueue(run_id):
    """Queues a run for the single worker. Used by the web app; tests call process() directly."""
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, daemon=True)
        _worker.start()
    _queue.put(run_id)


def _work():
    while True:
        run_id = _queue.get()
        try:
            process(run_id)
        finally:
            _queue.task_done()


def queue_position(run_id):
    with _queue.mutex:
        waiting = list(_queue.queue)
    return waiting.index(run_id) + 1 if run_id in waiting else 0


# ---- Claude's independent reading of text PDFs
def _start_cross_read(run_id, path, sha256):
    if ai_reader.available() and read.route(path)["route"] == "text":
        _cross_reads[run_id] = _ai_pool.submit(_cross_read, path, sha256)


def _cross_read(path, sha256):
    """Returns (proposal, cached_at or None, seconds Claude took). Raises RuntimeError with a reason."""
    key = f"{sha256}:{ai_reader.PROMPT_VERSION}"
    cached = store.cached_ai(key)
    if cached:
        return cached[0], cached[1], 0
    start = time.perf_counter()
    proposal = ai_reader.read_scan(path, Path(path).parent / "ai")
    store.cache_ai(key, proposal)
    return proposal, None, time.perf_counter() - start


def _cross_check_stage(run, fields, vendors):
    """Stage 4. Compares Claude's reading with the rules' reading. Returns the list of disagreements."""
    run_id = run["id"]
    future = _cross_reads.pop(run_id, None)
    if run["workspace"] == store.REPLAY:
        store.stage(run_id, 4, STAGES[4], "not_run", "Shared history is rebuilt by the rules alone at start-up.", {"ai": "skipped"}, 0)
        return []
    if future is None and not ai_reader.available():
        store.stage(run_id, 4, STAGES[4], "not_run", "AI cross-check is off on this deployment. The rules' reading is used on its own.", {"ai": "off"}, 0)
        return []
    start = time.perf_counter()
    store.stage(run_id, 4, STAGES[4], "running", "Claude is reading the page independently, to catch a misread before it moves money...")
    try:
        proposal, cached_at, took = future.result(timeout=ai_reader.TIMEOUT_SECONDS + 10) if future else _cross_read(run["file_path"], run["sha256"])
    except Exception as error:
        reason = str(error) or "no answer"
        ms = int((time.perf_counter() - start) * 1000)
        store.stage(run_id, 4, STAGES[4], "not_run", f"Claude could not read the page ({reason}). The rules' reading is used on its own.", {"ai": "failed"}, ms)
        return []
    ai_fields, _ = _proposal_to_fields(proposal, vendors)
    differences, extra, agreed = compare_readings(fields, ai_fields)
    waited = time.perf_counter() - start
    ms = int(waited * 1000)
    if cached_at:
        lead = f"Cached reading from {cached_at}. "
    elif took - waited > 2:
        lead = f"Claude took {took:.0f} s, reading in the background while earlier steps ran. "
    else:
        lead = f"Claude took {took:.0f} s. "
    detail = {"ai": "cached" if cached_at else "live", "differences": differences, "extra": extra, "agreed": agreed}
    store.update_run(run_id, cross_check=detail)
    hint = ""
    if extra:
        hint = " Claude also read " + "; ".join(f"{_label(e['field'])} {e['ai']}" for e in extra) + ", which the rules left blank. Not used."
    if differences:
        parts = "; ".join(f"{_label(d['field'])}: rules {d['rules']}, Claude {d['ai']}" for d in differences)
        store.stage(run_id, 4, STAGES[4], "warn", f"{lead}The two readings disagree. {parts}.{hint}", detail, ms)
    else:
        store.stage(run_id, 4, STAGES[4], "pass", f"{lead}Claude read the page independently and agrees on all {agreed} fields both could read.{hint}", detail, ms)
    return differences


def _label(name):
    return FIELD_LABELS.get(name, name).lower()


def compare_readings(fields, ai_fields):
    """Field by field. Only values both readers settled on are compared; a blank on either side is not a disagreement."""
    differences, extra, agreed = [], [], 0
    for name in FORM_FIELDS:
        mine = fields.get(name) or {}
        theirs = ai_fields.get(name) or {}
        a, b = mine.get("value"), theirs.get("value")
        settled = mine.get("state") in ("FOUND", "ENTERED", "OVERRIDDEN") and a not in (None, "", "NONE")
        if b in (None, ""):
            continue
        if not settled:
            if mine.get("state") in ("MISSING", "CONFLICT") and name != "bank_last4":
                extra.append({"field": name, "ai": theirs.get("printed") or b})
            continue
        if _same(name, a, b):
            agreed += 1
        else:
            vendor = name == "vendor"
            differences.append({"field": name, "rules": mine.get("printed") if vendor else a, "ai": theirs.get("printed") if vendor else b})
    return differences, extra, agreed


def _same(name, a, b):
    if name == "invoice_number":
        return normalise_number(a) == normalise_number(b)
    if name == "po_number":
        return normalise_po(a) == normalise_po(b)
    return _comparable(a) == _comparable(b)


def process(run_id, today=None):
    today = today or extract.today()
    started = time.perf_counter()
    with _run_lock:
        run = store.get_run(run_id)
        try:
            _process(run, today)
        except Exception as error:
            traceback.print_exc()
            store.stage(run_id, LAST, STAGES[LAST], "fail", "This run stopped because of an internal error. Nothing was decided. Run it again, or process this invoice by hand.", {"error": str(error)})
            store.update_run(run_id, status="failed", error=str(error), final_status="error")
        store.update_run(run_id, duration_ms=int((time.perf_counter() - started) * 1000))


def _process(run, today):
    run_id, workspace = run["id"], run["workspace"]
    path = Path(run["file_path"])
    vendors, pos, policy = store.load_master()
    is_rerun = run["parent_run_id"] is not None

    def receive():
        info = read.route(path)
        size = path.stat().st_size
        sentence = f"{info['pages']}-page PDF received, {size // 1024 or 1} KB."
        earlier = store.same_file(run["sha256"], workspace, run_id) if not is_rerun else None
        if is_rerun:
            sentence = f"Corrected re-run of {run['parent_run_id']}, using the same file."
        elif earlier:
            sentence += f" This exact file was uploaded in run {earlier['id']} (final status: {earlier['final_status']})."
        return "pass", sentence, {"sha256": run["sha256"], "pages": info["pages"], "bytes": size, "earlier_run": earlier["id"] if earlier else None}

    store.update_run(run_id, status="running")
    _timed(run_id, 1, receive)
    info = read.route(path)

    def route_stage():
        if is_rerun:
            parent = store.get_run(run["parent_run_id"])
            source = "text_layer" if parent["read_source"] == "text_layer" else "clerk_entered"
            return "pass", "Values from the page, with the clerk's corrections." if source == "text_layer" else "A scan. Using the values a person entered.", {"route": info["route"]}
        status = {"text": "pass", "scan": "warn", "unreadable": "fail"}[info["route"]]
        return status, info["reason"], {"route": info["route"]}

    _timed(run_id, 2, route_stage)

    if is_rerun:
        fields = run["fields"]
        parent = store.get_run(run["parent_run_id"])
        read_source = "text_layer" if parent["read_source"] == "text_layer" else "clerk_entered"
        changed = [c for c in store.get_changes(run["case_id"]) if c["run_id"] == run_id]
        _timed(run_id, 3, lambda: ("pass", f"{len(changed)} value(s) changed by the clerk. Everything else is as read before.", {"fields": fields}))
    elif info["route"] == "text":
        read_source = "text_layer"
        result = {}

        def read_text():
            result["fields"] = extract.extract(path, vendors, policy)
            return _extract_summary(result["fields"]), _extract_sentence(result["fields"]), {"fields": result["fields"]}

        _timed(run_id, 3, read_text)
        fields = result["fields"]
    elif info["route"] == "scan":
        fields, read_source = _read_scan(run, vendors)
    else:
        read_source = "none"
        fields = _blank_fields()
        _timed(run_id, 3, lambda: ("fail", info["reason"] + " Ask the vendor to resend it, or type the values by hand.", {"fields": fields}))

    store.update_run(run_id, fields=fields, read_source=read_source)

    if read_source == "text_layer":
        differences = _cross_check_stage(run, fields, vendors)
    else:
        why = {"ai": "Not needed: Claude's reading of this scan is checked by a person before anything is decided.",
               "clerk_entered": "Not needed: a person typed these values from the page.",
               "none": "Not run because there is no reading yet."}[read_source]
        store.stage(run_id, 4, STAGES[4], "not_run", why, None, 0)
        _cross_reads.pop(run_id, None)
        differences = []

    other_rows = store.ledger_rows(workspace, exclude_case=run["case_id"])
    evaluation = Evaluation(fields, vendors, pos, other_rows, policy, today, read_source, store.load_receipts(), run.get("sender"))
    evaluation.apply_cross_check(differences)
    no_reading = read_source == "none"
    preview = read_source == "ai"
    for number in sorted(STAGE_CHECKS):
        if no_reading:
            store.stage(run_id, number, STAGES[number], "not_run", "Not run because there is no reading yet.", None, 0)
            for name in STAGE_CHECKS[number]:
                evaluation._not_run(name, "there is no reading yet", "H-READ-NONE")
            continue
        _timed(run_id, number, lambda: _stage_outcome(evaluation, number, preview))

    decision = {}

    def decide():
        decision.update(evaluation.decide())
        return _decision_status(decision["decision"]), decision["summary"], {"decision": decision["decision"], "codes": decision["codes"], "next_step": decision["next_step"], "checks": decision["checks"]}

    _timed(run_id, LAST, decide)

    if read_source in ("ai", "clerk_entered") and run.get("ai_proposal"):
        decision["ai_differences"] = _ai_differences(fields, run["ai_proposal"])
    final_status = {"APPROVE": "approved", "REJECT": "rejected", "HOLD": "awaiting clerk"}[decision["decision"]]
    store.update_run(run_id, status="done", decision=decision["decision"], final_status=final_status, codes=json.dumps(decision["codes"]), result=decision)

    if read_source in ("text_layer", "clerk_entered"):
        ledger_status = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "HOLD": "HELD"}[decision["decision"]]
        store.write_ledger(run["case_id"], workspace, run_id, evaluation.value("vendor"), evaluation.value("invoice_number"), evaluation.value("invoice_date"),
                           evaluation.value("po_number"), decision["net"], decision["gross"], ledger_status)


def _decision_status(decision):
    return {"APPROVE": "pass", "HOLD": "warn", "REJECT": "fail"}[decision]


def _stage_outcome(evaluation, number, preview):
    results = evaluation.run_stage(number)
    statuses = [r["status"] for r in results]
    if "REJECT" in statuses:
        status = "fail"
    elif "HOLD" in statuses:
        status = "warn"
    elif all(s == "NOT_RUN" for s in statuses):
        status = "not_run"
    elif any(s == "NOT_RUN" and r["name"] != "sender" for s, r in zip(statuses, results)):
        status = "warn"
    else:
        status = "pass"
    problems = [r["sentence"] for r in results if r["status"] != "PASS"]
    sentence = " ".join(problems) if problems else " ".join(r["sentence"] for r in results if r["status"] == "PASS")
    if number == 5 and not problems:
        sentence = next((r["sentence"] for r in results if r["name"] == "arithmetic"), sentence)
    if preview:
        sentence = "Preview only, on AI-read values: " + sentence
    return status, sentence, {"checks": results, "preview": preview}


def _extract_summary(fields):
    needed = ["invoice_number", "invoice_date", "total", "vendor"]
    return "warn" if any(fields[n]["state"] != "FOUND" for n in needed) or any(f["state"] == "CONFLICT" for f in fields.values()) else "pass"


def _extract_sentence(fields):
    found = sum(1 for n in FORM_FIELDS if fields[n]["state"] == "FOUND")
    bad = [n.replace("_", " ") for n in FORM_FIELDS if fields[n]["state"] != "FOUND"]
    total = fields["total"]
    lead = f"Total {fmt(total['value'])} found next to '{total['label'].title()}', page {total['page'] + 1}. " if total["state"] == "FOUND" else ""
    tail = f"Not settled: {', '.join(bad)}." if bad else ""
    return f"{lead}{found} of {len(FORM_FIELDS)} fields read from the page. {tail}".strip()


def _blank_fields():
    return {name: {"state": "MISSING", "value": None} for name in FORM_FIELDS + ["tax_included_rate"]}


def _read_scan(run, vendors):
    """Laptop: Claude proposes values for a clerk to confirm. Hosted, or on failure: a blank form."""
    run_id = run["id"]
    fields = _blank_fields()
    if not ai_reader.available():
        _timed(run_id, 3, lambda: ("warn", "AI reader is not available on this deployment. A person needs to type the values.", {"fields": fields, "ai": "off"}))
        return fields, "none"
    key = f"{run['sha256']}:{ai_reader.PROMPT_VERSION}"
    cached = store.cached_ai(key)
    start = time.perf_counter()
    store.stage(run_id, 3, STAGES[3], "running", "Asking Claude to read this scan...")
    if cached:
        proposal, at = cached
        lead = f"Cached AI read from {at}. "
    else:
        try:
            proposal = ai_reader.read_scan(run["file_path"], Path(run["file_path"]).parent / "ai")
        except RuntimeError as error:
            ms = int((time.perf_counter() - start) * 1000)
            store.stage(run_id, 3, STAGES[3], "warn", f"Claude could not read this scan: {error}. A person needs to type the values.", {"fields": fields, "ai": "failed", "error": str(error)}, ms)
            return fields, "none"
        store.cache_ai(key, proposal)
        lead = f"Claude read this scan in {time.perf_counter() - start:.0f} seconds. "
    fields, flags = _proposal_to_fields(proposal, vendors)
    store.update_run(run_id, ai_proposal=proposal)
    run["ai_proposal"] = proposal
    ms = int((time.perf_counter() - start) * 1000)
    tail = f" Flagged for the clerk: {' '.join(flags)}" if flags else ""
    store.stage(run_id, 3, STAGES[3], "warn", f"{lead}These values are proposals only, until a person confirms them.{tail}", {"fields": fields, "ai": "cached" if cached else "live", "flags": flags}, ms)
    return fields, "ai"


def _proposal_to_fields(proposal, vendors):
    """Clear values only pre-fill the form. Null or unclear stays blank. Failed checks are flagged, never fixed."""
    fields = _blank_fields()
    flags = []

    def take(name):
        item = proposal.get(name) or {}
        value = item.get("value")
        if value in (None, "") or item.get("legibility") != "clear":
            return None
        return str(value).strip()

    def propose(name, value, printed=None, flag=None):
        fields[name] = {"state": "PROPOSED", "value": value, "printed": printed or value, "label": "AI read"}
        if flag:
            fields[name]["flag"] = flag
            flags.append(flag)

    vendor_name, tax_id = take("vendor_name"), take("tax_id")
    match = None
    for vendor in vendors.values():
        if tax_id and re.sub(r"\D", "", vendor["tax_id"]) == re.sub(r"\D", "", tax_id):
            match = vendor
        elif vendor_name and not tax_id and vendor["legal_name"].lower() == vendor_name.lower():
            match = vendor
    if match:
        propose("vendor", match["vendor_id"], match["legal_name"])
    elif vendor_name:
        fields["vendor"] = {"state": "MISSING", "value": None, "printed": vendor_name, "flag": f"{vendor_name} is not in the vendor master."}
        flags.append(fields["vendor"]["flag"])

    for name in ("invoice_number", "po_number"):
        value = take(name)
        if value:
            propose(name, value.upper())
    invoice_date = take("invoice_date")
    if invoice_date:
        meanings = extract.parse_date(invoice_date)
        if len(meanings) == 1:
            propose("invoice_date", meanings[0], invoice_date)
    amounts = {}
    for name in ("subtotal", "tax", "total"):
        value = take(name)
        parsed = extract.parse_amount(value) if value else None
        if parsed:
            amounts[name] = parsed
            propose(name, parsed)
    if len(amounts) == 3 and abs(Decimal(amounts["subtotal"]) + Decimal(amounts["tax"]) - Decimal(amounts["total"])) > Decimal("0.02"):
        fields["total"]["flag"] = "Subtotal + tax does not equal the total on these AI-read values."
        flags.append(fields["total"]["flag"])
    currency = take("currency")
    if currency:
        propose("currency", currency.upper())
    bank = take("bank_last4")
    if bank:
        digits = re.sub(r"\D", "", bank)
        if len(digits) >= 4:
            propose("bank_last4", digits[-4:])
    kind = take("document_type")
    if kind:
        propose("document_type", kind)
    return fields, flags


def _ai_differences(fields, proposal):
    out = []
    pairs = {"invoice_number": "invoice_number", "invoice_date": "invoice_date", "po_number": "po_number", "subtotal": "subtotal", "tax": "tax", "total": "total",
             "currency": "currency", "bank_last4": "bank_last4", "document_type": "document_type"}
    for field, key in pairs.items():
        proposed = (proposal.get(key) or {}).get("value")
        value = (fields.get(field) or {}).get("value")
        if proposed not in (None, "") and value is not None and _comparable(proposed) != _comparable(value):
            out.append({"field": field, "ai": str(proposed), "clerk": str(value)})
    return out


def _comparable(value):
    text = str(value).strip().upper().replace(",", "")
    try:
        return str(Decimal(text).quantize(Decimal("0.01")))
    except InvalidOperation:
        return text


def _clean_value(name, value, vendors):
    """Checks one clerk-entered value is well formed. Raises ClerkInputError with a plain message."""
    value = value.strip()
    if name == "vendor":
        if value not in vendors:
            raise ClerkInputError("Choose a vendor from the list.")
    elif name == "invoice_date":
        meanings = extract.parse_date(value)
        if len(meanings) != 1:
            raise ClerkInputError("Enter the invoice date as YYYY-MM-DD.")
        date.fromisoformat(meanings[0])
        return meanings[0]
    elif name in ("subtotal", "tax", "total"):
        parsed = extract.parse_amount(value)
        if parsed is None:
            raise ClerkInputError(f"{name.title()} must be an amount such as 12,400.00.")
        return parsed
    elif name == "bank_last4":
        if value.upper() == "NONE":
            return "NONE"
        if not re.fullmatch(r"\d{4}", value):
            raise ClerkInputError("Bank details must be the last 4 digits, or tick 'No bank details printed'.")
    elif name == "currency":
        return value.upper()
    elif name == "po_number":
        return normalise_po(value)
    elif name == "invoice_number":
        return value.upper()
    return value


def apply_clerk_values(run, values, who="AP clerk"):
    """Works out each field's new state from the previous run. The server decides, not the page.

    A FOUND value the clerk changes is OVERRIDDEN, which always ends in a hold. That is what stops
    someone clearing a control by retyping what the invoice prints.
    """
    vendors, _, _ = store.load_master()
    fields = json.loads(json.dumps(run["fields"]))
    scan = run["read_source"] != "text_layer"
    if scan:
        for required, message in [("document_type", "Choose the document type."), ("currency", "Enter the currency."), ("bank_last4", "Enter the bank last 4, or tick 'No bank details printed'.")]:
            if not (values.get(required) or "").strip():
                raise ClerkInputError(message)
    changes = []
    for name in FORM_FIELDS:
        raw = values.get(name)
        previous = fields.get(name) or {"state": "MISSING", "value": None}
        old_value, old_state = previous.get("value"), previous["state"]
        if raw is None or raw.strip() == "":
            if scan and old_state == "PROPOSED":
                fields[name] = {"state": "MISSING", "value": None}
            continue
        value = _clean_value(name, raw, vendors)

        if scan:
            state = "ENTERED"
        elif old_state == "FOUND":
            if value == old_value:
                continue
            state = "OVERRIDDEN"
        elif old_state == "OVERRIDDEN":
            if value == old_value:
                continue
            state = "OVERRIDDEN"
            old_value = previous.get("old_value", old_value)
        elif old_state == "ENTERED":
            if value == old_value:
                continue
            state = "ENTERED"
        elif old_state == "CONFLICT":
            state = "ENTERED" if value in previous.get("readings", []) else "OVERRIDDEN"
        elif name == "bank_last4" and value != "NONE":
            state = "ENTERED" if extract.page_has_text(run["file_path"], value) else "OVERRIDDEN"
        elif name == "bank_last4":
            state = "OVERRIDDEN"
        else:
            state = "ENTERED"

        fields[name] = {**previous, "state": state, "value": value, "entered_by": who, "flag": None}
        if state == "OVERRIDDEN":
            fields[name]["old_value"] = old_value if old_value is not None else previous.get("printed")
        if not (scan and old_state == "PROPOSED" and value == old_value):
            changes.append((name, old_value, old_state, value, state))
    if not scan and not changes:
        raise ClerkInputError("Nothing was changed, so re-running would give the same result.")
    return fields, changes


def start_rerun(run, values, who="AP clerk"):
    fields, changes = apply_clerk_values(run, values, who)
    run_id, _ = store.create_run(run["workspace"], run["file_name"], run["file_path"], run["sha256"], run["case_id"], run["id"])
    store.update_run(run_id, fields=fields, ai_proposal=run["ai_proposal"], source=run["source"], sender=run["sender"], subject=run["subject"], status="queued")
    if run["read_source"] == "text_layer":
        _start_cross_read(run_id, run["file_path"], run["sha256"])
    for name, old_value, old_state, value, state in changes:
        store.add_change(run_id, name, old_value, old_state, value, state, who)
    return run_id


def final_decision(run, decision, note, who="AP clerk"):
    """A person's decision on a held invoice. The process's own decision stays on record beside it."""
    note = (note or "").strip()
    if not note:
        raise ClerkInputError("A note is required for a final decision.")
    if run["decision"] != "HOLD" or run["final_status"] != "awaiting clerk":
        raise ClerkInputError("Only an invoice on hold can take a final decision.")
    latest = store.case_runs(run["case_id"])[-1]
    if latest["id"] != run["id"]:
        raise ClerkInputError("A later run exists for this invoice. Decide on that one.")
    if decision == "approve":
        blocked = approve_blockers(run)
        if blocked:
            raise ClerkInputError(blocked)
        store.update_run(run["id"], final_status="approved by clerk", clerk_note=note, final_by=who)
        store.set_ledger_status(run["case_id"], "APPROVED")
    elif decision == "reject":
        store.update_run(run["id"], final_status="rejected by clerk", clerk_note=note, final_by=who)
        store.set_ledger_status(run["case_id"], "REJECTED")
    else:
        raise ClerkInputError("The decision must be approve or reject.")


def approve_blockers(run):
    """Why Final Approve is not allowed, or None. Empty or non-USD invoices must not enter the ledger."""
    if run["read_source"] not in ("text_layer", "clerk_entered"):
        return "Enter the missing fields first. An invoice nobody has read cannot be approved."
    if {"H-CURRENCY", "H-DOC"} & set(run["codes"]):
        return "A non-USD amount or a non-invoice must not enter a USD running total. Correct it or reject it."
    vendors, _, _ = store.load_master()
    result = run["result"] or {}
    fields = run["fields"] or {}
    usable = lambda name: fields.get(name, {}).get("state") in ("FOUND", "ENTERED", "OVERRIDDEN") and fields[name].get("value")
    if not (usable("vendor") and fields["vendor"]["value"] in vendors and usable("invoice_number") and usable("invoice_date") and usable("po_number") and result.get("net") and result.get("gross")):
        return "Enter the missing fields first."
    return None
