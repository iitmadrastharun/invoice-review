# Invoice Review

Checks vendor invoices before they are paid. Each invoice comes in by email or upload. It is compared with the vendor master, the PO, goods received and earlier invoices, and the AP clerk gets approve, hold or reject with the reason, the numbers, who acts next and a drafted message. Every stage shows as it runs, and every run is saved.

**The rule behind it:** rules decide, AI reads and double-checks, and nothing is approved unless every required check actually ran and passed. AI can stop a payment; it can never make one.

The buyer is a made-up Pune auto-parts maker; vendors are made-up Indian suppliers with made-up GSTINs, and amounts are in rupees with 18% GST. All vendors, POs, emails and invoices are made up. No payment is ever made.

**Live version:** https://invoice-review-mpwa.onrender.com. It runs on Render's free tier, so the first visit after a quiet spell can take about a minute to wake up. The hosted version has no AI: scans go straight to a form for a person to fill in, and text PDFs are read by the rules alone.

## What it checks

| Stage | Checks |
|---|---|
| Receive, read route | Is this a text PDF, a scan or unreadable? Has this exact file been seen before? |
| Extract fields | Vendor, invoice number, date, PO, subtotal, tax, total, currency, bank account and document type, read by label and position rules |
| AI cross-check | Claude reads the page independently. If the two readings disagree, the invoice holds |
| Check document | Is it a payable invoice? Do the sums add up? Is the tax rate the PO's? Is the date sensible? Currency |
| Check vendor | Approved, blocked or onboarding? Bank account matches the one on file? For emailed invoices, sender's domain matches the vendor's |
| Find PO | Printed, open and the same vendor's. If none is printed, a PO is suggested, never assumed |
| Check duplicates | Same number, or the same amount on the same PO close in time |
| Match PO and receipt | Running total per PO within tolerance (applied once per PO), and billing not ahead of goods received |
| Decide | Reject only when certain; hold anything a person must settle; approve only when all 12 required checks ran and passed |

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

1. **Inbox:** nine vendor emails with PDFs attached. **Check all new emails** runs them as a batch, in arrival order.
2. **Check invoices:** upload one PDF to watch it run, several to check them as a batch, or pick a sample.
3. **Run view:** the ten stages, the decision, who acts next, and **Draft it** for the follow-up message or call note. On a hold, correct fields and re-run, or make a final decision with a note.
4. **Overview:** the five steps (receive, read, check, decide, pay) with live counts, what's waiting for a person and why, and the payment file export.
5. **All runs:** every run, with search and filters. **Reset demo data** clears your own runs only.

## Where AI is used

All through the local `claude -p` command, when it is installed and logged in and `READER_AI` is not `off`. The top bar shows which applies.

- **Scans:** Claude proposes values, and a person confirms them before any decision counts.
- **Text PDFs:** Claude reads the page independently, and a disagreement holds the invoice. It starts as soon as a file arrives, a few at a time, so a batch doesn't wait for each one. Readings are cached by file, and the cache survives restarts.
- **Drafted messages:** a smaller model words the message from facts the rules already settled. A fixed template is used when Claude isn't available. Nothing is ever sent from the app.

## Tests

```
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest tests -q
```

## Deploying to Render

`render.yaml` describes the service. Connect the repository on Render as a Blueprint. It runs with `READER_AI=off`, and the free tier wipes its disk on restart, which is fine because history rebuilds at startup.

## Where things are

| Path | What it does |
|---|---|
| `engine/read.py` | Is this PDF text, a scan, or unreadable? |
| `engine/extract.py` | Reads fields from the text layer with label and position rules |
| `engine/ai_reader.py` | Asks Claude to read a page. Proposes values only |
| `engine/rules.py` | The decision rules. No PDFs, no AI, no database |
| `engine/pipeline.py` | Runs the ten stages, the batch queue and the AI cross-check; clerk corrections and final decisions |
| `engine/inbox.py` | Reads the `.eml` files that stand in for the AP mailbox |
| `engine/drafts.py` | The follow-up message: Claude, or a fixed template |
| `engine/store.py` | SQLite: runs, stages, changes, ledger, drafts, AI cache |
| `data/` | Vendor master (with GSTINs), POs, goods receipts and the tolerance policy |
| `samples/` | History invoices, live samples and the inbox emails |
| `make_samples.py` | Rebuilds every sample PDF and email |
| `DEMO.md` | Five-minute walkthrough and interview checklist |
| `BUSINESS_CASE.md` | Before and after, what to measure, how to roll it out |
| `PS1-process-design.md` | The original design |
