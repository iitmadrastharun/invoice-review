"""Runs the real pipeline over the real sample PDFs, starting from a rebuilt history."""
import shutil
from datetime import date
from pathlib import Path

import pytest

from engine import ai_reader, drafts, extract, inbox, pipeline, store

ROOT = Path(__file__).parent.parent
LIVE = ROOT / "samples" / "live"
HISTORY = ROOT / "samples" / "history"
TODAY = date(2026, 11, 3)
GOOD_READ = {
    "vendor_name": "Godavari Logistics LLP", "tax_id": "36AALFG7702L1Z9", "invoice_number": "GL/26-27/5520", "invoice_date": "2026-10-22", "po_number": "PO-4810",
    "subtotal": "620000.00", "tax": "111600.00", "total": "731600.00", "currency": "INR", "bank_last4": "7702", "document_type": "invoice",
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


def run(path, workspace="w1", **meta):
    run_id = pipeline.new_run(path, Path(path).name, workspace, **meta)
    pipeline.process(run_id, TODAY)
    return store.get_run(run_id)


def rerun(previous, today=TODAY, **values):
    run_id = pipeline.start_rerun(previous, values)
    pipeline.process(run_id, today)
    return store.get_run(run_id)


def test_history_replay():
    rows = {r["file_name"]: r for r in store.list_runs("nobody")}
    assert rows["MFS-1101.pdf"]["decision"] == "APPROVE"
    assert rows["MFS-1102.pdf"]["decision"] == "APPROVE"
    assert rows["API-7781.pdf"]["decision"] == "APPROVE"
    assert rows["PMS-0300.pdf"]["decision"] == "REJECT"
    assert rows["PMS-0300.pdf"]["codes"][0] == "RJ-VENDOR"


@pytest.mark.parametrize("name,decision,code", [
    ("S1-happy", "APPROVE", None),
    ("S2-split", "HOLD", "H-AMOUNT"),
    ("S3-resend", "HOLD", "H-DUP-NEAR"),
    ("S4-scan", "HOLD", "H-READ-NONE"),
    ("S5-no-number", "HOLD", "H-FIELD"),
    ("S6-bank", "HOLD", "H-VENDOR-BANK"),
    ("S7-embedded", "APPROVE", None),
    ("S8-implied", "HOLD", "H-PO-NONE"),
    ("S9-receipt", "HOLD", "H-RECEIPT"),
])
def test_first_run_of_each_sample(name, decision, code):
    result = run(LIVE / f"{name}.pdf")
    assert result["status"] == "done"
    assert result["decision"] == decision, result["result"]["reasons"]
    if code:
        assert result["codes"][0] == code, result["codes"]
    else:
        assert result["codes"] == []
        assert result["result"]["checks"] == {"ran": 12, "of": 12, "not_run": []}


def test_happy_path_numbers():
    numbers = run(LIVE / "S1-happy.pdf")["result"]["numbers"]
    assert numbers["limit"] == "1264800.00" and numbers["after_this_invoice"] == "1246500.00" and numbers["over_po_by"] == "6500.00"


def test_ec1_running_total():
    result = run(LIVE / "S2-split.pdf")["result"]
    assert result["numbers"]["after_this_invoice"] == "4060000.00"
    assert result["numbers"]["over_limit_by"] == "35000.00"
    assert "₹7,75,000.00" in result["next_step"]["action"] and "₹35,000.00" in result["next_step"]["action"]


def test_ec2_match_passes_and_duplicate_catches():
    result = run(LIVE / "S3-resend.pdf")["result"]
    checks = {c["name"]: c["status"] for c in result["check_results"]}
    assert checks["amount_tolerance"] == "PASS"
    sentence = result["reasons"][0]["sentence"]
    assert "7 days apart" in sentence and "same PO" in sentence and "related invoice number" in sentence
    # receiving booked one shipment, so a second bill for it runs ahead of what arrived
    assert result["codes"] == ["H-DUP-NEAR", "H-RECEIPT"]


def test_ec4_billed_ahead_of_receipt():
    result = run(LIVE / "S9-receipt.pdf")["result"]
    assert result["codes"] == ["H-RECEIPT"]
    assert result["numbers"]["received_to_date"] == "320000.00" and result["numbers"]["billed_after_this_invoice"] == "480000.00"
    assert "K. Iyer" in result["next_step"]["action"]


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
    assert fields["total"] == {**fields["total"], "state": "FOUND", "value": "1470870.00"}
    assert fields["subtotal"]["value"] == "1246500.00" and fields["tax"]["value"] == "224370.00"
    assert fields["bank_last4"]["value"] == "4471"


def test_trap_b_each_value_under_its_own_label():
    fields = fields_of("S2-split")
    assert fields["invoice_number"]["value"] == "MFS/26-27/1103"
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


# --- Claude's independent second reading of text PDFs
S1_READ = {
    "vendor_name": "Kaveri Industrial Supplies Pvt Ltd", "tax_id": "33AABCK4471M1Z2", "invoice_number": "KIS/26-27/0417", "invoice_date": "2026-10-26", "po_number": "PO-4501",
    "subtotal": "1246500.00", "tax": "224370.00", "total": "1470870.00", "currency": "INR", "bank_last4": "4471", "document_type": "invoice",
}


def ai_on(monkeypatch, read):
    monkeypatch.setattr(ai_reader, "available", lambda: True)
    monkeypatch.setattr(ai_reader, "read_scan", read)


def as_proposal(values):
    return {k: {"value": v, "printed_text": v, "page": 1, "legibility": "clear"} for k, v in values.items()}


def test_cross_check_agreeing_keeps_approve(monkeypatch):
    ai_on(monkeypatch, lambda *a: as_proposal(S1_READ))
    result = run(LIVE / "S1-happy.pdf")
    assert result["decision"] == "APPROVE"
    stage = store.get_stages(result["id"])[3]
    assert stage["status"] == "pass" and "agrees on all 10 fields" in stage["sentence"]


def test_cross_check_disagreeing_holds(monkeypatch):
    ai_on(monkeypatch, lambda *a: as_proposal({**S1_READ, "total": "1470780.00"}))
    result = run(LIVE / "S1-happy.pdf")
    assert result["decision"] == "HOLD" and result["codes"] == ["H-READ-DISAGREE"]
    assert "1470780.00" in result["result"]["summary"]
    # the clerk checks the page, finds the rules were right, and approves with a note
    pipeline.final_decision(result, "approve", "Checked the page: total is 14,70,870.00 as printed.")
    assert store.get_run(result["id"])["final_status"] == "approved by clerk"


def test_cross_check_failure_does_not_hold(monkeypatch):
    def fail(*a):
        raise RuntimeError("no answer within 90 seconds")
    ai_on(monkeypatch, fail)
    result = run(LIVE / "S1-happy.pdf")
    assert result["decision"] == "APPROVE"
    assert "could not read" in store.get_stages(result["id"])[3]["sentence"]


def test_cross_check_only_suggests_what_rules_left_blank(monkeypatch):
    ai_on(monkeypatch, lambda *a: as_proposal({**S1_READ, "vendor_name": "Narmada Electricals Pvt Ltd", "tax_id": "24AADCN8816E1Z7",
                                               "invoice_number": "NE/26-27/8810", "invoice_date": "2026-10-27", "po_number": "PO-4950",
                                               "subtotal": "280000.00", "tax": "50400.00", "total": "330400.00", "bank_last4": None}))
    result = run(LIVE / "S8-implied.pdf")
    assert result["codes"][0] == "H-PO-NONE"  # a suggestion is never used as a value
    assert "PO-4950" in store.get_stages(result["id"])[3]["sentence"]


def test_cross_check_is_cached(monkeypatch):
    calls = []
    ai_on(monkeypatch, lambda *a: calls.append(1) or as_proposal(S1_READ))
    run(LIVE / "S1-happy.pdf", "a")
    run(LIVE / "S1-happy.pdf", "b")
    assert len(calls) == 1


def test_scan_does_not_get_a_second_reading(monkeypatch):
    calls = []
    ai_on(monkeypatch, lambda *a: calls.append(1) or proposal())
    run(LIVE / "S4-scan.pdf")
    assert len(calls) == 1  # the one reading a person confirms


# --- the inbox
def test_inbox_lists_every_email():
    messages = inbox.messages()
    assert len(messages) == 9 and all(m["attachments"] for m in messages)


def test_lookalike_sender_with_new_bank_details(tmp_path):
    saved, sender, subject = inbox.attachments("06-S6", tmp_path)
    result = run(saved[0][0], source="email", sender=sender, subject=subject)
    assert result["codes"][:2] == ["H-SENDER", "H-VENDOR-BANK"]


def test_genuine_sender_passes(tmp_path):
    saved, sender, subject = inbox.attachments("01-S1", tmp_path)
    result = run(saved[0][0], source="email", sender=sender, subject=subject)
    assert result["decision"] == "APPROVE"
    assert any(c["name"] == "sender" and c["status"] == "PASS" for c in result["result"]["check_results"])


def test_rerun_keeps_the_sender(tmp_path):
    saved, sender, subject = inbox.attachments("06-S6", tmp_path)
    first = run(saved[0][0], source="email", sender=sender, subject=subject)
    second = rerun(first, bank_last4="4471")
    assert "H-SENDER" in second["codes"]


# --- drafted messages (template; Claude is off in tests)
def test_drafts_go_to_the_right_person():
    vendors, pos, policy = store.load_master()
    s2 = run(LIVE / "S2-split.pdf")
    d = drafts.draft(s2, vendors["V-1002"], pos["PO-4620"], policy)
    assert d["kind"] == "buyer" and "A. Mehta" in d["to"] and "35,000.00" in d["body"] and d["source"] == "template"
    s6 = run(LIVE / "S6-bank.pdf")
    d = drafts.draft(s6, vendors["V-1001"], pos["PO-4502"], policy)
    assert d["kind"] == "call" and "+91 44 4012 0142" in d["to"] and "phone number on file" in d["body"]


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
    monkeypatch.setattr(ai_reader, "read_scan", lambda *a: proposal(vendor_name="Prabhat Manpower Services Pvt Ltd", tax_id="29AAGCP5527H1Z3", po_number="PO-4300"))
    result = run(LIVE / "S4-scan.pdf")
    assert result["decision"] == "HOLD" and result["codes"][0] == "H-READ-AI"
    assert any("Preview only" in n for n in result["result"]["notes"])


def test_ai_misread_flagged_not_fixed(monkeypatch):
    monkeypatch.setattr(ai_reader, "available", lambda: True)
    monkeypatch.setattr(ai_reader, "read_scan", lambda *a: proposal(total="713600.00"))
    result = run(LIVE / "S4-scan.pdf")
    assert result["decision"] == "HOLD"
    assert result["fields"]["total"]["value"] == "713600.00" and result["fields"]["total"]["flag"]


CLERK = dict(vendor="V-1004", invoice_number="GL/26-27/5520", invoice_date="2026-10-22", po_number="PO-4810", currency="INR",
             subtotal="6,20,000.00", tax="1,11,600.00", total="7,31,600.00", bank_last4="7702", document_type="invoice")


def test_scan_confirmed_by_clerk_approves():
    first = run(LIVE / "S4-scan.pdf")
    second = rerun(first, **CLERK)
    assert second["decision"] == "APPROVE" and second["read_source"] == "clerk_entered"
    assert second["case_id"] == first["case_id"]
    assert second["result"]["checks"]["ran"] == 12


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
    second = rerun(run(LIVE / "S2-split.pdf"), subtotal="7,75,000.00", tax="1,39,500.00", total="9,14,500.00")
    assert second["decision"] == "HOLD" and second["codes"] == ["H-OVERRIDE"]


def test_retyping_date_gives_override():
    second = rerun(run(LIVE / "S3-resend.pdf"), invoice_date="2026-10-30")
    assert second["decision"] == "HOLD" and "H-OVERRIDE" in second["codes"]


def test_rerun_with_no_change_refused():
    first = run(LIVE / "S2-split.pdf")
    with pytest.raises(pipeline.ClerkInputError):
        rerun(first, total="9,55,800.00")


def test_entering_a_missing_value_can_approve():
    assert rerun(run(LIVE / "S8-implied.pdf"), po_number="po-4950")["decision"] == "APPROVE"
    assert rerun(run(LIVE / "S5-no-number.pdf"), invoice_number="HOS-2300")["decision"] == "APPROVE"


# --- final decisions and the ledger
def test_final_approve_counts_toward_po_and_makes_resend_reject():
    first = run(LIVE / "S2-split.pdf")
    pipeline.final_decision(first, "approve", "Change order CO-77 confirmed by A. Mehta")
    assert store.get_run(first["id"])["final_status"] == "approved by clerk"
    resend = run(LIVE / "S2-split.pdf")
    assert resend["decision"] == "REJECT" and resend["codes"] == ["RJ-DUP"]
    rows = [r for r in store.ledger_rows("w1") if r["po_number"] == "PO-4620" and r["status"] == "APPROVED"]
    assert sum(float(r["net"]) for r in rows) == 4060000.00


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
    assert [s["stage"] for s in stages] == list(range(1, 11))
    assert all(s["status"] == "pass" for s in stages if s["stage"] != 4)
    assert stages[3]["status"] == "not_run" and "off" in stages[3]["sentence"]  # AI is off in tests
