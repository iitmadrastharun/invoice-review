"""Web app: upload or pull invoices from the inbox, watch the stages run, review history. Start with: uvicorn app:app"""
import csv
import io
import secrets
import shutil
import tempfile
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from engine import ai_reader, drafts, inbox, pipeline, store
from engine.rules import CODE_NAMES

ROOT = Path(__file__).parent
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_BATCH_FILES = 25
COOKIE = "workspace"

SAMPLES = {
    "S1-happy.pdf": "Northwind: clean invoice, small overage",
    "S2-split.pdf": "Meridian: third invoice on a split PO",
    "S3-resend.pdf": "Apex: similar to an invoice already approved",
    "S4-scan.pdf": "Cedar Ridge: low-quality scan",
    "S5-no-number.pdf": "Harbor: no invoice number printed",
    "S6-bank.pdf": "Northwind: check the bank details",
    "S7-embedded.pdf": "Harbor: one bundled line, tax inside the total",
    "S8-implied.pdf": "Lakeshore: no PO printed",
    "S9-receipt.pdf": "Apex: billed ahead of goods received",
}


def rebuild_history():
    """Earlier approvals come from real runs of the engine, not from seeded rows."""
    store.init()
    shutil.rmtree(pipeline.UPLOADS, ignore_errors=True)
    for pdf in sorted((ROOT / "samples" / "history").glob("*.pdf")):
        pipeline.process(pipeline.new_run(pdf, pdf.name, store.REPLAY))


@asynccontextmanager
async def lifespan(_app):
    rebuild_history()
    yield


app = FastAPI(title="Invoice Review", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/samples", StaticFiles(directory=ROOT / "samples" / "live"), name="samples")


@app.middleware("http")
async def workspace_cookie(request: Request, call_next):
    """Each browser gets its own workspace, so one viewer's runs cannot change another's results."""
    workspace = request.cookies.get(COOKIE)
    fresh = workspace is None
    request.state.workspace = workspace or secrets.token_hex(8)
    response = await call_next(request)
    if fresh:
        response.set_cookie(COOKIE, request.state.workspace, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


def _visible_run(request, run_id):
    run = store.get_run(run_id)
    if not run or run["workspace"] not in (store.REPLAY, request.state.workspace):
        raise HTTPException(404, "That run was not found. It may have been cleared by a reset or a restart.")
    return run


def _own_run(request, run_id):
    run = _visible_run(request, run_id)
    if run["workspace"] == store.REPLAY:
        raise HTTPException(403, "This is a history run shared by everyone, so it cannot be changed.")
    return run


def _start(run_id):
    pipeline.enqueue(run_id)


def _batch_id():
    return "b-" + secrets.token_hex(3)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    vendors, _, _ = store.load_master()
    return {
        "ai_reader": ai_reader.available(),
        "samples": [{"file": k, "label": v} for k, v in SAMPLES.items()],
        "code_names": CODE_NAMES,
        "vendors": [{"id": v["vendor_id"], "name": v["legal_name"]} for v in vendors.values()],
    }


@app.post("/api/runs")
async def create_run(request: Request, file: UploadFile | None = File(None), sample: str | None = Form(None)):
    workspace = request.state.workspace
    if sample:
        if sample not in SAMPLES:
            raise HTTPException(400, "That sample does not exist.")
        run_id = pipeline.new_run(ROOT / "samples" / "live" / sample, sample, workspace, source="sample")
    elif file is not None:
        data = await _read_upload(file)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "upload.pdf"
            path.write_bytes(data)
            run_id = pipeline.new_run(path, Path(file.filename or "invoice.pdf").name[:120], workspace)
    else:
        raise HTTPException(400, "Choose a PDF or a sample first.")
    _start(run_id)
    return {"run_id": run_id}


async def _read_upload(file):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{file.filename} is over the 10 MB limit.")
    if not data:
        raise HTTPException(400, f"{file.filename} is empty.")
    return data


@app.post("/api/batches")
async def create_batch(request: Request, files: list[UploadFile] = File(...)):
    """Several PDFs at once. They are checked one after another, in the order given."""
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(400, f"Up to {MAX_BATCH_FILES} files at a time.")
    batch = _batch_id()
    contents = [(f.filename, await _read_upload(f)) for f in files]
    run_ids = []
    with tempfile.TemporaryDirectory() as folder:
        for i, (name, data) in enumerate(contents):
            path = Path(folder) / f"{i}.pdf"
            path.write_bytes(data)
            run_ids.append(pipeline.new_run(path, Path(name or "invoice.pdf").name[:120], request.state.workspace, batch_id=batch))
    for run_id in run_ids:
        _start(run_id)
    return {"batch_id": batch, "run_ids": run_ids}


@app.get("/api/batches/{batch_id}")
def get_batch(request: Request, batch_id: str):
    runs = [r for r in store.list_runs(request.state.workspace) if r["batch_id"] == batch_id]
    if not runs:
        raise HTTPException(404, "That batch was not found.")
    latest = {r["case_id"]: r["id"] for r in reversed(runs)}
    rows = [{**_summary(r, latest), "queue_position": pipeline.queue_position(r["id"]), "stage": _current_stage(r["id"])} for r in sorted(runs, key=lambda r: r["seq"])]
    return {"batch_id": batch_id, "runs": rows}


def _current_stage(run_id):
    stages = store.get_stages(run_id)
    return {"number": stages[-1]["stage"], "name": stages[-1]["name"], "status": stages[-1]["status"]} if stages else None


# ---- the AP mailbox
@app.get("/api/inbox")
def list_inbox(request: Request):
    done = {}
    for run in store.list_runs(request.state.workspace):
        if run["source"] == "email" and run["workspace"] != store.REPLAY and run["batch_id"]:
            done.setdefault(run["subject"], run["batch_id"])
    return {"messages": [{**m, "batch_id": done.get(m["subject"])} for m in inbox.messages()]}


@app.post("/api/inbox/process")
async def process_inbox(request: Request):
    """Pulls the PDF attachments from the chosen emails (all of them if none are named) and checks them in arrival order."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    wanted = body.get("ids")
    messages = sorted(inbox.messages(), key=lambda m: m["date"] or "")
    if wanted:
        messages = [m for m in messages if m["id"] in wanted]
    if not messages:
        raise HTTPException(400, "There are no emails to process.")
    batch = _batch_id()
    run_ids = []
    for message in messages:
        with tempfile.TemporaryDirectory() as folder:
            saved, sender, subject = inbox.attachments(message["id"], folder)
            for path, name in saved:
                run_ids.append(pipeline.new_run(path, name, request.state.workspace, source="email", sender=sender, subject=subject, batch_id=batch))
    for run_id in run_ids:
        _start(run_id)
    return {"batch_id": batch, "run_ids": run_ids}


def _summary(run, latest_by_case):
    result = run["result"] or {}
    fields = run["fields"] or {}
    value = lambda name: (fields.get(name) or {}).get("value")
    vendors, _, _ = store.load_master()
    vendor = vendors.get(value("vendor") or "")
    return {
        "id": run["id"], "case_id": run["case_id"], "file_name": run["file_name"], "status": run["status"], "decision": run["decision"],
        "final_status": run["final_status"], "codes": run["codes"], "read_source": run["read_source"], "started_at": run["started_at"],
        "duration_ms": run["duration_ms"], "vendor": vendor["legal_name"] if vendor else None, "invoice_number": value("invoice_number"),
        "po_number": value("po_number"), "gross": result.get("gross"), "net": result.get("net"), "summary": result.get("summary"),
        "history": run["workspace"] == store.REPLAY, "superseded": latest_by_case[run["case_id"]] != run["id"], "parent_run_id": run["parent_run_id"],
        "source": run["source"], "sender": run["sender"], "subject": run["subject"], "batch_id": run["batch_id"],
        "next_step": (result.get("next_step") or {}), "cross_check": (run["cross_check"] or {}).get("differences") is not None,
    }


@app.get("/api/runs")
def list_runs(request: Request):
    runs = store.list_runs(request.state.workspace)
    latest = {}
    for run in reversed(runs):
        latest[run["case_id"]] = run["id"]
    rows = [_summary(r, latest) for r in runs]
    counts = {"approved": 0, "held": 0, "rejected": 0, "failed": 0}
    for row in rows:
        if row["superseded"] or row["status"] in ("running", "queued"):
            continue
        status = row["final_status"] or ""
        key = "failed" if row["status"] == "failed" else "approved" if status.startswith("approved") else "rejected" if status.startswith("rejected") else "held"
        counts[key] += 1
    return {"runs": rows, "counts": counts}


@app.get("/api/runs/{run_id}")
def get_run(request: Request, run_id: str):
    run = _visible_run(request, run_id)
    chain = store.case_runs(run["case_id"])
    page_size = None
    try:
        with pymupdf.open(run["file_path"]) as doc:
            if not doc.needs_pass and doc.page_count:
                page_size = {"width": doc[0].rect.width, "height": doc[0].rect.height, "pages": doc.page_count}
    except Exception:
        pass
    latest = chain[-1]["id"] == run["id"]
    overturned = run["final_status"] == "approved by clerk" and any(r["decision"] == "REJECT" for r in chain)
    return {
        "run": {k: v for k, v in run.items() if k not in ("file_path", "workspace")},
        "stages": store.get_stages(run_id),
        "changes": store.get_changes(run["case_id"]),
        "chain": [{"id": r["id"], "decision": r["decision"], "codes": r["codes"], "final_status": r["final_status"]} for r in chain],
        "page_size": page_size,
        "is_latest": latest,
        "is_history": run["workspace"] == store.REPLAY,
        "reject_overturned": overturned,
        "approve_blocker": pipeline.approve_blockers(run) if run["status"] == "done" and run["decision"] == "HOLD" else None,
        "queue_position": pipeline.queue_position(run_id),
        "draft": store.get_draft(run_id),
    }


@app.get("/api/runs/{run_id}/pages/{number}.png")
def page_image(request: Request, run_id: str, number: int):
    run = _visible_run(request, run_id)
    try:
        with pymupdf.open(run["file_path"]) as doc:
            if number < 1 or number > doc.page_count:
                raise HTTPException(404, "No such page.")
            png = doc[number - 1].get_pixmap(dpi=120).tobytes("png")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, "This file cannot be displayed.")
    return Response(png, media_type="image/png")


@app.get("/api/runs/{run_id}/download")
def download(request: Request, run_id: str):
    run = _visible_run(request, run_id)
    body = {
        "run_id": run["id"], "case_id": run["case_id"], "parent_run_id": run["parent_run_id"], "file": run["file_name"], "decision": run["decision"],
        "final_status": run["final_status"], "clerk_note": run["clerk_note"], "read_source": run["read_source"], "fields": run["fields"], "result": run["result"],
        "stages": store.get_stages(run_id), "changes": store.get_changes(run["case_id"]),
    }
    return JSONResponse(body, headers={"Content-Disposition": f'attachment; filename="{run["id"]}.json"'})


@app.post("/api/runs/{run_id}/rerun")
async def rerun(request: Request, run_id: str):
    run = _own_run(request, run_id)
    if store.case_runs(run["case_id"])[-1]["id"] != run_id:
        raise HTTPException(409, "A later run exists for this invoice. Correct that one.")
    if run["status"] != "done" or run["final_status"] not in ("awaiting clerk", "rejected"):
        raise HTTPException(409, "Only an invoice on hold or rejected by the process can be corrected.")
    body = await request.json()
    try:
        new_id = pipeline.start_rerun(run, body.get("values", {}))
    except pipeline.ClerkInputError as error:
        raise HTTPException(422, str(error))
    _start(new_id)
    return {"run_id": new_id}


@app.post("/api/runs/{run_id}/final")
async def final(request: Request, run_id: str):
    run = _own_run(request, run_id)
    body = await request.json()
    try:
        pipeline.final_decision(run, body.get("decision"), body.get("note"))
    except pipeline.ClerkInputError as error:
        raise HTTPException(422, str(error))
    return {"ok": True}


@app.post("/api/runs/{run_id}/draft")
def make_draft(request: Request, run_id: str):
    """Drafts the next message for a person to read and send. Nothing is sent from here."""
    run = _visible_run(request, run_id)
    if run["status"] != "done" or run["decision"] not in ("HOLD", "REJECT"):
        raise HTTPException(409, "Only a held or rejected invoice needs a message.")
    vendors, pos, policy = store.load_master()
    fields = run["fields"] or {}
    vendor = vendors.get((fields.get("vendor") or {}).get("value") or "")
    po = pos.get((fields.get("po_number") or {}).get("value") or "")
    draft = drafts.draft(run, vendor, po, policy)
    store.save_draft(run_id, draft)
    return draft


def _decided(request):
    """The latest run of each case, with its final status, for the export and the metrics."""
    runs = store.list_runs(request.state.workspace)
    latest = {}
    for run in reversed(runs):
        latest[run["case_id"]] = run
    return [r for r in latest.values() if r["status"] == "done"]


@app.get("/api/export/approved.csv")
def export_approved(request: Request):
    """A payment file for the ERP: approved invoices only, paid to the account on file, never the one printed."""
    vendors, _, _ = store.load_master()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["vendor_id", "vendor_name", "invoice_number", "invoice_date", "po_number", "currency", "net", "tax", "gross",
                     "pay_to_account_on_file_last4", "approved_by", "note", "run_id"])
    for run in _decided(request):
        if not (run["final_status"] or "").startswith("approved"):
            continue
        fields = run["fields"] or {}
        value = lambda n: (fields.get(n) or {}).get("value")
        vendor = vendors.get(value("vendor") or "") or {}
        result = run["result"] or {}
        tax = Decimal(result["gross"]) - Decimal(result["net"]) if result.get("gross") and result.get("net") else ""
        writer.writerow([vendor.get("vendor_id", ""), vendor.get("legal_name", ""), value("invoice_number"), value("invoice_date"), value("po_number"),
                         value("currency") or "USD", result.get("net"), tax, result.get("gross"), vendor.get("bank_last4", ""),
                         "person" if run["final_status"] == "approved by clerk" else "rules", run["clerk_note"] or "", run["id"]])
    name = f"approved-invoices-{date.today().isoformat()}.csv"
    return Response(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/metrics")
def metrics(request: Request):
    """Figures for the overview. Time saved is an estimate from the assumptions in data/policy.json, shown with them."""
    vendors, _, policy = store.load_master()
    decided = _decided(request)
    approved = [r for r in decided if (r["final_status"] or "").startswith("approved")]
    auto = [r for r in decided if r["final_status"] == "approved"]
    held = [r for r in decided if r["final_status"] == "awaiting clerk"]
    rejected = [r for r in decided if (r["final_status"] or "").startswith("rejected")]
    reasons = Counter()
    for run in decided:
        if run["decision"] in ("HOLD", "REJECT") and run["codes"]:
            reasons[run["codes"][0]] += 1
    value_by_vendor = Counter()
    for run in approved:
        vendor = vendors.get(((run["fields"] or {}).get("vendor") or {}).get("value") or "")
        if vendor and (run["result"] or {}).get("gross"):
            value_by_vendor[vendor["legal_name"]] += float(run["result"]["gross"])
    caught = Counter()
    for run in decided:
        for code in run["codes"]:
            if code in ("RJ-DUP", "H-DUP-NUMBER", "H-DUP-NEAR", "H-VENDOR-BANK", "H-SENDER", "RJ-VENDOR", "H-AMOUNT", "H-RECEIPT"):
                caught[code] += 1
    at_risk = sum(float((r["result"] or {}).get("gross") or 0) for r in decided
                  if set(r["codes"]) & {"RJ-DUP", "H-DUP-NUMBER", "H-DUP-NEAR", "H-VENDOR-BANK", "H-SENDER", "RJ-VENDOR"})
    assumptions = policy.get("assumptions", {})
    manual = float(assumptions.get("manual_minutes_per_invoice", 0))
    review = float(assumptions.get("review_minutes_per_exception", 0))
    minutes_saved = len(auto) * manual + (len(held) + len(rejected)) * max(manual - review, 0)
    durations = [r["duration_ms"] for r in decided if r["duration_ms"] is not None]
    return {
        "invoices": len(decided), "approved": len(approved), "auto_approved": len(auto), "held": len(held), "rejected": len(rejected),
        "value_approved": round(sum(float((r["result"] or {}).get("gross") or 0) for r in approved), 2),
        "value_held": round(sum(float((r["result"] or {}).get("gross") or 0) for r in held), 2),
        "at_risk_stopped": round(at_risk, 2),
        "reasons": [{"code": c, "name": CODE_NAMES.get(c, c), "count": n} for c, n in reasons.most_common()],
        "value_by_vendor": [{"vendor": v, "value": round(n, 2)} for v, n in value_by_vendor.most_common()],
        "caught": dict(caught),
        "median_seconds": round(sorted(durations)[len(durations) // 2] / 1000, 1) if durations else None,
        "minutes_saved": round(minutes_saved), "assumptions": assumptions,
    }


@app.post("/api/reset")
def reset(request: Request):
    for path in store.reset_workspace(request.state.workspace):
        if path:
            shutil.rmtree(Path(path).parent, ignore_errors=True)
    return {"ok": True}
