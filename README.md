# Invoice Review

Takes a vendor invoice PDF and gives an AP clerk an approve, hold or reject decision, with the reason, the numbers and the next step. Every stage shows as it runs, and every run is saved.

**The rule behind it:** rules decide, AI only reads scans, and nothing is approved unless every required check actually ran and passed.

All vendors, POs and invoices are made up. No payment is ever made.

**Live version:** https://invoice-review-mpwa.onrender.com. It runs on Render's free tier, so the first visit after a quiet spell can take about a minute to wake up. Scans there skip the AI reading and go straight to the form for a person to fill in.

## Start it (Windows)

Needs Python 3.11 or later. Run these from this folder:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip uninstall -y onnxruntime
.venv\Scripts\python -m uvicorn app:app --port 8000
```

Open http://localhost:8000. History rebuilds itself each time the app starts, by running the real engine over `samples/history`.

The uninstall line is for Windows. PyMuPDF 2.0 pulls in onnxruntime for an optional layout feature this app doesn't use, and on Python 3.14 under Windows importing it crashed Python. PyMuPDF works without it.

On macOS or Linux, use `.venv/bin/python` in place of `.venv\Scripts\python`.

## Use it

1. On **New invoice**, upload a PDF or pick a sample.
2. Watch the nine stages run, then read the decision card.
3. If it's on hold, correct the fields and re-run, or make a final decision with a note.
4. **Dashboard** shows every run. Click a row to reopen it. **Reset demo data** clears your own runs only.

## Reading scans

Text PDFs are read by rules. A scan is sent to the local `claude -p` command if it is installed and logged in, and `READER_AI` is not `off`. The badge at the top right shows which applies. Without it, a scan gets a blank form for a person to fill in. Either way a scan holds until a person confirms the values, so the decision does not depend on which one read it.

## Tests

```
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest tests -q
```

## Deploying to Render

`render.yaml` describes the service. Connect the repository on Render as a Blueprint. It runs with `READER_AI=off`, and the free tier wipes its disk on restart, which is fine because history rebuilds at startup. If the build fails on Python 3.14, change `PYTHON_VERSION` in `render.yaml` to 3.12.8.

## Where things are

| Path | What it does |
|---|---|
| `engine/read.py` | Is this PDF text, a scan, or unreadable? |
| `engine/extract.py` | Reads fields from the text layer with label and position rules |
| `engine/ai_reader.py` | Asks Claude to read a scan. Proposes values only |
| `engine/rules.py` | The decision rules. No PDFs, no AI, no database |
| `engine/pipeline.py` | Runs the nine stages; clerk corrections and final decisions |
| `engine/store.py` | SQLite: runs, stages, changes, ledger |
| `data/` | Vendor master, POs, tolerance policy |
| `make_samples.py` | Rebuilds every sample PDF |
| `DEMO.md` | Five-minute walkthrough and interview checklist |
| `PS1-process-design.md` | The original full design. Some items were cut; see `DEMO.md` |
