"""Runs the real pipeline over the real sample PDFs, starting from a rebuilt history."""
import shutil
from datetime import date
from pathlib import Path

import pytest

from engine import ai_reader, extract, pipeline, store

ROOT = Path(__file__).parent.parent
LIVE = ROOT / "samples" / "live"
HISTORY = ROOT / "samples" / "history"
TODAY = date(2026, 11, 3)
GOOD_READ = {
    "vendor_name": "Cedar Ridge Logistics LLC", "tax_id": "52-9038114", "invoice_number": "CR-5520", "invoice_date": "2026-10-22", "po_number": "PO-4810",
    "subtotal": "6200.00", "tax": "496.00", "total": "6696.00", "currency": "USD", "bank_last4": "7702", "document_type": "invoice",
}


def proposal(**over):
    values = {**GOOD_READ, **over}
    return {k: {"value": v, "printed_text": v, "page": 1, "legibility": "clear"} for k, v in values.items()}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "VAR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(pipeline, "UPLOADS", tmp_path / "runs")
    monkeypatch.setenv("READER_AI", "off")
    store.init()
    for pdf in sorted(HISTORY.glob("*.pdf")):
        run(pdf, store.REPLAY)


def run(path, workspace="w1"):
    run_id = pipeline.new_run(path, Path(path).name, workspace)
    pipeline.process(run_id, TODAY)
    return store.get_run(run_id)


def rerun(previous, today=TODAY, **values):
    run_id = pipeline.start_rerun(previous, values)
    pipeline.process(run_id, today)
    return store.get_run(run_id)


def test_history_replay():
    rows = {r["file_name"]: r for r in store.list_runs("nobody")}
    assert rows["MF-1101.pdf"]["decision"] == "APPROVE"
    assert rows["MF-1102.pdf"]["decision"] == "APPROVE"
    assert rows["AP-7781.pdf"]["decision"] == "APPROVE"
    assert rows["BK-300.pdf"]["decision"] == "REJECT"
    assert rows["BK-300.pdf"]["codes"][0] == "RJ-VENDOR"


@pytest.mark.parametrize("name,decision,code", [
    ("S1-happy", "APPROVE", None),
    ("S2-split", "HOLD", "H-AMOUNT"),
    ("S3-resend", "HOLD", "H-DUP-NEAR"),
    ("S4-scan", "HOLD", "H-READ-NONE"),
    ("S5-no-number", "HOLD", "H-FIELD"),
    ("S6-bank", "HOLD", "H-VENDOR-BANK"),
    ("S7-embedded", "APPROVE", None),
    ("S8-implied", "HOLD", "H-PO-NONE"),
])
def test_first_run_of_each_sample(name, decision, code):
    result = run(LIVE / f"{name}.pdf")
    assert result["status"] == "done"
    assert result["decision"] == decision, result["result"]["reasons"]
    if code:
        assert result["codes"][0] == code, result["codes"]
    else:
        assert result["codes"] == []
        assert result["result"]["checks"] == {"ran": 11, "of": 11, "not_run": []}


def test_happy_path_numbers():
    numbers = run(LIVE / "S1-happy.pdf")["result"]["numbers"]
    assert numbers["limit"] == "12648.00" and numbers["after_this_invoice"] == "12465.00" and numbers["over_po_by"] == "65.00"


def test_ec1_running_total():
    result = run(LIVE / "S2-split.pdf")["result"]
    assert result["numbers"]["after_this_invoice"] == "40600.00"
    assert result["numbers"]["over_limit_by"] == "350.00"
    assert "7,750.00" in result["next_step"]["action"] and "350.00" in result["next_step"]["action"]


def test_ec2_match_passes_and_duplicate_catches():
    result = run(LIVE / "S3-resend.pdf")["result"]
    checks = {c["name"]: c["status"] for c in result["check_results"]}
    assert checks["amount_tolerance"] == "PASS"
    sentence = result["reasons"][0]["sentence"]
    assert "7 days apart" in sentence and "same PO" in sentence and "related invoice number" in sentence


def test_same_invoice_twice_is_certain_reject_and_shows_file_notice():
    run(LIVE / "S1-happy.pdf")
    second = run(LIVE / "S1-happy.pdf")
    assert second["decision"] == "REJECT" and second["codes"] == ["RJ-DUP"]
    assert "exact file" in store.get_stages(second["id"])[0]["sentence"]


def test_workspaces_do_not_collide():
    assert run(LIVE / "S1-happy.pdf", "a")["decision"] == "APPROVE"
    assert run(LIVE / "S1-happy.pdf", "b")["decision"] == "APPROVE"
    store.reset_workspace("a")
    assert run(LIVE / "S1-happy.pdf", "a")["decision"] == "APPROVE"
    assert run(LIVE / "S1-happy.pdf", "b")["decision"] == "REJECT"


# --- the three layout traps
def fields_of(name):
    vendors, _, policy = store.load_master()
    return extract.extract(LIVE / f"{name}.pdf", vendors, policy)


def test_trap_a_total_column_is_not_the_total():
    fields = fields_of("S1-happy")
    assert fields["total"] == {**fields["total"], "state": "FOUND", "value": "13462.20"}
    assert fields["subtotal"]["value"] == "12465.00" and fields["tax"]["value"] == "997.20"
    assert fields["bank_last4"]["value"] == "4471"


def test_trap_b_each_value_under_its_own_label():
    fields = fields_of("S2-split")
    assert fields["invoice_number"]["value"] == "MF-1103"
    assert fields["po_number"]["value"] == "PO-4620"
    assert fields["invoice_date"]["value"] == "2026-10-26"
    assert fields["bank_last4"]["value"] == "2093"  # "Customer Account No: 88120" must be ignored


def test_trap_c_issuer_not_ship_to_or_buyer():
    fields = fields_of("S8-implied")
    assert fields["vendor"]["value"] == "V-1006"
    fields = fields_of("S7-embedded")
    assert fields["vendor"]["value"] == "V-1005"
    assert fields["bank_last4"]["value"] == "NONE"


# --- scans: the same first decision however they are read
def test_scan_ai_on_and_off_same_decision(monkeypatch):
    off = run(LIVE / "S4-scan.pdf")
    monkeypatch.setenv("READER_AI", "on")
    monkeypatch.setattr(ai_reader, "available", lambda: True)
    monkeypatch.setattr(ai_reader, "read_scan", lambda *a: proposal())
    on = run(LIVE / "S4-scan.pdf", "w2")
    assert off["decision"] == on["decision"] == "HOLD"
    assert off["codes"][0] == "H-READ-NONE" and on["codes"][0] == "H-READ-AI"
    assert store.ledger_rows("w1", None) == store.ledger_rows("w2", None)  # neither wrote a row


def test_text_pdf_never_calls_ai(monkeypatch):
    monkeypatch.setattr(ai_reader, "available", lambda: True)
    monkeypatch.setattr(ai_reader, "read_scan", lambda *a: pytest.fail("AI called on a text PDF"))
    for name in ["S1-happy", "S2-split", "S5-no-number", "S8-implied"]:
        run(LIVE / f"{name}.pdf")


def test_scan_ai_failure_gives_blank_form(monkeypatch):
    monkeypatch.setattr(ai_reader, "available", lambda: True)

    def fail(*a):
        raise RuntimeError("no answer within 90 seconds")

    monkeypatch.setattr(ai_reader, "read_scan", fail)
    result = run(LIVE / "S4-scan.pdf")
    assert result["codes"][0] == "H-READ-NONE"
    assert "no answer" in store.get_stages(result["id"])[2]["sentence"]


def test_ai_read_of_blocked_vendor_still_holds(monkeypatch):
    monkeypatch.setattr(ai_reader, "available", lambda: True)
    monkeypatch.setattr(ai_reader, "read_scan", lambda *a: proposal(vendor_name="Brightline Staffing Group LLC", tax_id="73-4410268", po_number="PO-4300"))
    result = run(LIVE / "S4-scan.pdf")
    assert result["decision"] == "HOLD" and result["codes"][0] == "H-READ-AI"
    assert any("Preview only" in n for n in result["result"]["notes"])


def test_ai_misread_flagged_not_fixed(monkeypatch):
    monkeypatch.setattr(ai_reader, "available", lambda: True)
    monkeypatch.setattr(ai_reader, "read_scan", lambda *a: proposal(total="6969.00"))
    result = run(LIVE / "S4-scan.pdf")
    assert result["decision"] == "HOLD"
    assert result["fields"]["total"]["value"] == "6969.00" and result["fields"]["total"]["flag"]


CLERK = dict(vendor="V-1004", invoice_number="CR-5520", invoice_date="2026-10-22", po_number="PO-4810", currency="USD",
             subtotal="6,200.00", tax="496.00", total="6,696.00", bank_last4="7702", document_type="invoice")


def test_scan_confirmed_by_clerk_approves():
    first = run(LIVE / "S4-scan.pdf")
    second = rerun(first, **CLERK)
    assert second["decision"] == "APPROVE" and second["read_source"] == "clerk_entered"
    assert second["case_id"] == first["case_id"]
    assert second["result"]["checks"]["ran"] == 11


def test_scan_form_refuses_blank_required_fields():
    first = run(LIVE / "S4-scan.pdf")
    with pytest.raises(pipeline.ClerkInputError):
        rerun(first, **{**CLERK, "bank_last4": ""})


@pytest.mark.parametrize("change,code", [(dict(bank_last4="9931"), "H-VENDOR-BANK"), (dict(currency="EUR"), "H-CURRENCY")])
def test_scan_with_different_page_gets_different_answer(change, code):
    second = rerun(run(LIVE / "S4-scan.pdf"), **{**CLERK, **change})
    assert second["decision"] == "HOLD" and code in second["codes"]


def test_clerk_entered_blocked_vendor_rejects():
    second = rerun(run(LIVE / "S4-scan.pdf"), **{**CLERK, "vendor": "V-1007", "po_number": "PO-4300"})
    assert second["decision"] == "REJECT" and "RJ-VENDOR" in second["codes"]


def test_scan_copy_of_approved_invoice_rejects():
    assert rerun(run(LIVE / "S4-scan.pdf"), **CLERK)["decision"] == "APPROVE"
    copy = rerun(run(LIVE / "S4-scan.pdf"), **CLERK)
    assert copy["decision"] == "REJECT" and copy["codes"] == ["RJ-DUP"]


# --- typing over a printed value never makes an APPROVE
def test_retyping_bank_gives_override_not_approve():
    first = run(LIVE / "S6-bank.pdf")
    second = rerun(first, bank_last4="4471")
    assert second["decision"] == "HOLD" and second["codes"] == ["H-OVERRIDE"]
    assert "9931" in second["result"]["summary"] and "4471" in second["result"]["summary"]


def test_retyping_amounts_gives_override():
    second = rerun(run(LIVE / "S2-split.pdf"), subtotal="7,750.00", tax="620.00", total="8,370.00")
    assert second["decision"] == "HOLD" and second["codes"] == ["H-OVERRIDE"]


def test_retyping_date_gives_override():
    second = rerun(run(LIVE / "S3-resend.pdf"), invoice_date="2026-10-30")
    assert second["decision"] == "HOLD" and "H-OVERRIDE" in second["codes"]


def test_rerun_with_no_change_refused():
    first = run(LIVE / "S2-split.pdf")
    with pytest.raises(pipeline.ClerkInputError):
        rerun(first, total="8,748.00")


def test_entering_a_missing_value_can_approve():
    assert rerun(run(LIVE / "S8-implied.pdf"), po_number="po-4950")["decision"] == "APPROVE"
    assert rerun(run(LIVE / "S5-no-number.pdf"), invoice_number="HOS-2300")["decision"] == "APPROVE"


# --- final decisions and the ledger
def test_final_approve_counts_toward_po_and_makes_resend_reject():
    first = run(LIVE / "S2-split.pdf")
    pipeline.final_decision(first, "approve", "Change order CO-77 confirmed by M. Okafor")
    assert store.get_run(first["id"])["final_status"] == "approved by clerk"
    resend = run(LIVE / "S2-split.pdf")
    assert resend["decision"] == "REJECT" and resend["codes"] == ["RJ-DUP"]
    rows = [r for r in store.ledger_rows("w1") if r["po_number"] == "PO-4620" and r["status"] == "APPROVED"]
    assert sum(float(r["net"]) for r in rows) == 40600.00


def test_final_approve_needs_a_note():
    with pytest.raises(pipeline.ClerkInputError):
        pipeline.final_decision(run(LIVE / "S2-split.pdf"), "approve", "   ")


def test_final_approve_refused_when_unread():
    with pytest.raises(pipeline.ClerkInputError):
        pipeline.final_decision(run(LIVE / "S4-scan.pdf"), "approve", "looks fine")


def test_final_approve_refused_for_other_currency():
    second = rerun(run(LIVE / "S4-scan.pdf"), **{**CLERK, "currency": "EUR"})
    with pytest.raises(pipeline.ClerkInputError):
        pipeline.final_decision(second, "approve", "looks fine")


def test_no_final_decision_on_a_reject():
    run(LIVE / "S1-happy.pdf")
    with pytest.raises(pipeline.ClerkInputError):
        pipeline.final_decision(run(LIVE / "S1-happy.pdf"), "approve", "override")


def test_corrected_case_does_not_match_itself():
    first = run(LIVE / "S8-implied.pdf")
    second = rerun(first, po_number="PO-4950")
    assert second["decision"] == "APPROVE"
    third_row = [r for r in store.ledger_rows("w1") if r["case_id"] == first["case_id"]]
    assert len(third_row) == 1 and third_row[0]["status"] == "APPROVED"


def test_unreadable_file(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf")
    result = run(bad)
    assert result["decision"] == "HOLD" and result["codes"] == ["H-READ-NONE"]
    assert result["status"] == "done"


def test_stages_all_recorded():
    result = run(LIVE / "S1-happy.pdf")
    stages = store.get_stages(result["id"])
    assert [s["stage"] for s in stages] == list(range(1, 10))
    assert all(s["status"] == "pass" for s in stages)
