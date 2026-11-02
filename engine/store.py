"""SQLite storage: run history, stage events, clerk changes, and the ledger the rules read."""
import csv
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from engine.rules import normalise_number, normalise_po

ROOT = Path(__file__).parent.parent
VAR = ROOT / "var"
DB_PATH = VAR / "app.db"
REPLAY = "replay"

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE runs (
  id TEXT PRIMARY KEY, seq INTEGER, case_id TEXT, parent_run_id TEXT, workspace TEXT, file_name TEXT, file_path TEXT,
  sha256 TEXT, read_source TEXT, status TEXT, decision TEXT, final_status TEXT, codes TEXT, clerk_note TEXT, final_by TEXT,
  fields TEXT, result TEXT, ai_proposal TEXT, error TEXT, started_at TEXT, duration_ms INTEGER
);
CREATE TABLE stage_events (
  run_id TEXT, stage INTEGER, name TEXT, status TEXT, sentence TEXT, detail TEXT, ms INTEGER, PRIMARY KEY (run_id, stage)
);
CREATE TABLE field_changes (
  run_id TEXT, field TEXT, old_value TEXT, old_state TEXT, new_value TEXT, new_state TEXT, who TEXT, at TEXT
);
CREATE TABLE ledger (
  case_id TEXT PRIMARY KEY, workspace TEXT, last_run_id TEXT, vendor_id TEXT, invoice_number TEXT, number_norm TEXT,
  invoice_date TEXT, po_number TEXT, net TEXT, gross TEXT, status TEXT
);
CREATE TABLE ai_cache (key TEXT PRIMARY KEY, proposal TEXT, at TEXT);
"""


@contextmanager
def _connect():
    # The database is rebuilt on every start, so it does not need to survive a power cut.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous = OFF")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    """Starts empty: history is rebuilt by running the real engine over samples/history."""
    VAR.mkdir(exist_ok=True)
    with _lock:
        if DB_PATH.exists():
            DB_PATH.unlink()
        with _connect() as conn:
            conn.executescript(SCHEMA)


def load_master():
    data = ROOT / "data"
    with open(data / "vendors.csv", newline="", encoding="utf-8") as f:
        vendors = {r["vendor_id"]: r for r in csv.DictReader(f)}
    with open(data / "pos.csv", newline="", encoding="utf-8") as f:
        pos = {r["po_number"]: r for r in csv.DictReader(f)}
    with open(data / "policy.json", encoding="utf-8") as f:
        policy = json.load(f)
    return vendors, pos, policy


def now():
    return datetime.now().isoformat(timespec="seconds")


def create_run(workspace, file_name, file_path, sha256, case_id=None, parent_run_id=None):
    with _lock, _connect() as conn:
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM runs").fetchone()[0]
        run_id = f"r-{seq:04d}"
        case_id = case_id or f"c-{seq:04d}"
        conn.execute("INSERT INTO runs (id, seq, case_id, parent_run_id, workspace, file_name, file_path, sha256, status, started_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (run_id, seq, case_id, parent_run_id, workspace, file_name, str(file_path), sha256, "running", now()))
    return run_id, case_id


def stage(run_id, number, name, status, sentence, detail=None, ms=None):
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO stage_events VALUES (?,?,?,?,?,?,?)",
                     (run_id, number, name, status, sentence, json.dumps(detail) if detail is not None else None, ms))


def update_run(run_id, **columns):
    for key in ("fields", "result", "ai_proposal"):
        if key in columns and columns[key] is not None:
            columns[key] = json.dumps(columns[key])
    sql = ", ".join(f"{k} = ?" for k in columns)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE runs SET {sql} WHERE id = ?", (*columns.values(), run_id))


def _run_dict(row):
    run = dict(row)
    for key in ("fields", "result", "ai_proposal"):
        run[key] = json.loads(run[key]) if run.get(key) else None
    run["codes"] = json.loads(run["codes"]) if run.get("codes") else []
    return run


def get_run(run_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run_dict(row) if row else None


def get_stages(run_id):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM stage_events WHERE run_id = ? ORDER BY stage", (run_id,)).fetchall()
    return [{**dict(r), "detail": json.loads(r["detail"]) if r["detail"] else None} for r in rows]


def get_changes(case_id):
    with _connect() as conn:
        rows = conn.execute("SELECT c.* FROM field_changes c JOIN runs r ON r.id = c.run_id WHERE r.case_id = ? ORDER BY c.at", (case_id,)).fetchall()
    return [dict(r) for r in rows]


def case_runs(case_id):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM runs WHERE case_id = ? ORDER BY seq", (case_id,)).fetchall()
    return [_run_dict(r) for r in rows]


def list_runs(workspace):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM runs WHERE workspace IN (?, ?) ORDER BY seq DESC", (REPLAY, workspace)).fetchall()
    return [_run_dict(r) for r in rows]


def same_file(sha256, workspace, exclude):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE sha256 = ? AND workspace IN (?, ?) AND id != ? AND status = 'done' AND parent_run_id IS NULL ORDER BY seq DESC LIMIT 1",
                           (sha256, REPLAY, workspace, exclude)).fetchone()
    return _run_dict(row) if row else None


def ledger_rows(workspace, exclude_case=None):
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM ledger WHERE workspace IN (?, ?) AND case_id != ?", (REPLAY, workspace, exclude_case or "")).fetchall()
    return [dict(r) for r in rows]


def write_ledger(case_id, workspace, run_id, vendor_id, invoice_number, invoice_date, po_number, net, gross, status):
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO ledger VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (case_id, workspace, run_id, vendor_id, invoice_number, normalise_number(invoice_number), invoice_date, normalise_po(po_number), net, gross, status))


def set_ledger_status(case_id, status):
    with _lock, _connect() as conn:
        conn.execute("UPDATE ledger SET status = ? WHERE case_id = ?", (status, case_id))


def add_change(run_id, field, old_value, old_state, new_value, new_state, who):
    with _lock, _connect() as conn:
        conn.execute("INSERT INTO field_changes VALUES (?,?,?,?,?,?,?,?)", (run_id, field, old_value, old_state, new_value, new_state, who, now()))


def cached_ai(key):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM ai_cache WHERE key = ?", (key,)).fetchone()
    return (json.loads(row["proposal"]), row["at"]) if row else None


def cache_ai(key, proposal):
    with _lock, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO ai_cache VALUES (?,?,?)", (key, json.dumps(proposal), now()))


def reset_workspace(workspace):
    """Deletes only this browser's runs, so one viewer cannot wipe another's."""
    with _lock, _connect() as conn:
        paths = [r["file_path"] for r in conn.execute("SELECT file_path FROM runs WHERE workspace = ?", (workspace,))]
        conn.execute("DELETE FROM stage_events WHERE run_id IN (SELECT id FROM runs WHERE workspace = ?)", (workspace,))
        conn.execute("DELETE FROM field_changes WHERE run_id IN (SELECT id FROM runs WHERE workspace = ?)", (workspace,))
        conn.execute("DELETE FROM runs WHERE workspace = ?", (workspace,))
        conn.execute("DELETE FROM ledger WHERE workspace = ?", (workspace,))
    return paths
