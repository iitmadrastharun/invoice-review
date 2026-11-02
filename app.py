"""Web app: upload an invoice, watch the stages run, review history. Start with: uvicorn app:app"""
import secrets
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from engine import ai_reader, pipeline, store

ROOT = Path(__file__).parent
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
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
    threading.Thread(target=pipeline.process, args=(run_id,), daemon=True).start()


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
        "vendors": [{"id": v["vendor_id"], "name": v["legal_name"]} for v in vendors.values()],
    }


@app.post("/api/runs")
async def create_run(request: Request, file: UploadFile | None = File(None), sample: str | None = Form(None)):
    workspace = request.state.workspace
    if sample:
        if sample not in SAMPLES:
            raise HTTPException(400, "That sample does not exist.")
        run_id = pipeline.new_run(ROOT / "samples" / "live" / sample, sample, workspace)
    elif file is not None:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "This file is over the 10 MB limit.")
        if not data:
            raise HTTPException(400, "This file is empty.")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "upload.pdf"
            path.write_bytes(data)
            run_id = pipeline.new_run(path, Path(file.filename or "invoice.pdf").name[:120], workspace)
    else:
        raise HTTPException(400, "Choose a PDF or a sample first.")
    _start(run_id)
    return {"run_id": run_id}


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
        "po_number": value("po_number"), "gross": result.get("gross"), "summary": result.get("summary"), "history": run["workspace"] == store.REPLAY,
        "superseded": latest_by_case[run["case_id"]] != run["id"], "parent_run_id": run["parent_run_id"],
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
        if row["superseded"] or row["status"] == "running":
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


@app.post("/api/reset")
def reset(request: Request):
    for path in store.reset_workspace(request.state.workspace):
        if path:
            shutil.rmtree(Path(path).parent, ignore_errors=True)
    return {"ok": True}
