"""Rule tests on structured records, no PDFs. At least one row per decision code."""
from datetime import date
from pathlib import Path

import pymupdf
import pytest

from engine import extract, store
from engine.rules import CHECKS, Evaluation, evaluate, normalise_number

VENDORS, POS, POLICY = store.load_master()
TODAY = date(2026, 11, 3)


def f(value, state="FOUND", **extra):
    return {"state": state, "value": value, **extra}


def clean(**over):
    """A clean Northwind invoice on PO-4501."""
    fields = {
        "vendor": f("V-1001"), "invoice_number": f("INV-1"), "invoice_date": f("2026-10-26"), "po_number": f("PO-4501"),
        "subtotal": f("10000.00"), "tax": f("800.00"), "total": f("10800.00"), "currency": f("USD"), "bank_last4": f("4471"),
        "document_type": f("invoice"), "tax_included_rate": f(None, "MISSING"),
    }
    fields.update(over)
    return fields


def row(vendor="V-1001", number="1", date_="2026-10-26", po="PO-4501", net="10000.00", gross="10800.00", status="APPROVED", printed="INV-1"):
    return {"case_id": "c-x", "workspace": "w", "last_run_id": "r-9", "vendor_id": vendor, "invoice_number": printed, "number_norm": number,
            "invoice_date": date_, "po_number": po, "net": net, "gross": gross, "status": status}


def decide(fields=None, ledger=(), read_source="text_layer"):
    return evaluate(fields or clean(), VENDORS, POS, list(ledger), POLICY, TODAY, read_source)


def test_clean_invoice_approves_with_all_checks():
    out = decide()
    assert out["decision"] == "APPROVE" and out["checks"]["ran"] == 11


MISSING = f(None, "MISSING")


@pytest.mark.parametrize("fields,ledger,read_source,decision,code", [
    (clean(), [], "none", "HOLD", "H-READ-NONE"),
    (clean(), [], "ai", "HOLD", "H-READ-AI"),
    (clean(vendor=f("V-1007"), po_number=f("PO-4300"), bank_last4=f("5527")), [], "text_layer", "REJECT", "RJ-VENDOR"),
    (clean(vendor=f("V-1007"), po_number=MISSING, invoice_number=MISSING), [], "text_layer", "REJECT", "RJ-VENDOR"),
    (clean(vendor=f("V-1003"), po_number=f("PO-4200"), bank_last4=f("6158")), [], "text_layer", "REJECT", "RJ-PO"),
    (clean(po_number=f("PO-4200")), [], "text_layer", "HOLD", "H-PO-INVALID"),  # cancelled, but another vendor's
    (clean(), [row()], "text_layer", "REJECT", "RJ-DUP"),
    (clean(), [row(date_="2025-10-26")], "text_layer", "HOLD", "H-DUP-NUMBER"),  # same number, a year apart
    (clean(), [row(gross="10000.00")], "text_layer", "HOLD", "H-DUP-NUMBER"),
    (clean(), [row(status="HELD")], "text_layer", "HOLD", "H-DUP-NUMBER"),
    (clean(), [row(number="99999", net="500.00", gross="10800.50", po="PO-4502")], "text_layer", "HOLD", "H-DUP-NEAR"),  # close in time
    (clean(), [row(number="99999", date_="2026-05-01", net="500.00")], "text_layer", "HOLD", "H-DUP-NEAR"),  # same PO, any date
    (clean(invoice_number=f("AP7781R1")), [row(number="AP7781", date_="2026-05-01", po="PO-4502", net="500.00")], "text_layer", "HOLD", "H-DUP-NEAR"),  # related number
    (clean(invoice_number=MISSING), [], "text_layer", "HOLD", "H-FIELD"),
    (clean(invoice_date=f(None, "CONFLICT", readings=["2026-11-03", "2026-03-11"])), [], "text_layer", "HOLD", "H-FIELD"),
    (clean(total=f(None, "CONFLICT", readings=["12.400,00"])), [], "text_layer", "HOLD", "H-FIELD"),
    (clean(bank_last4=f("4471", "OVERRIDDEN", old_value="9931")), [], "text_layer", "HOLD", "H-OVERRIDE"),
    (clean(bank_last4=MISSING), [], "text_layer", "HOLD", "H-CHECK-NOT-RUN"),
    (clean(currency=MISSING), [], "text_layer", "HOLD", "H-CHECK-NOT-RUN"),
    (clean(document_type=f("credit_note")), [], "text_layer", "HOLD", "H-DOC"),
    (clean(document_type=MISSING), [], "text_layer", "HOLD", "H-DOC"),
    (clean(subtotal=f("-10000.00"), tax=f("-800.00"), total=f("-10800.00")), [], "text_layer", "HOLD", "H-DOC"),
    (clean(invoice_date=f("2026-11-20")), [], "text_layer", "HOLD", "H-DATE"),
    (clean(invoice_date=f("2026-09-01")), [], "text_layer", "HOLD", "H-DATE"),  # before the PO
    (clean(total=f("10900.00")), [], "text_layer", "HOLD", "H-ARITH"),
    (clean(subtotal=MISSING, tax=MISSING), [], "text_layer", "HOLD", "H-TAX"),  # total only
    (clean(tax=f("1000.00"), total=f("11000.00")), [], "text_layer", "HOLD", "H-TAX"),  # wrong rate
    (clean(subtotal=f("12700.00"), tax=f("1016.00"), total=f("13716.00")), [], "text_layer", "HOLD", "H-AMOUNT"),
    (clean(), [row(number="5", net="2000.00", gross="2160.00")], "text_layer", "APPROVE", None),
    (clean(), [row(number="5", net="3000.00", gross="3240.00", status="HELD")], "text_layer", "APPROVE", None),  # held does not count
    (clean(), [row(number="5", net="2000.00", gross="2160.00"), row(number="6", net="700.00", gross="756.00")], "text_layer", "HOLD", "H-AMOUNT"),
    (clean(vendor=MISSING), [], "text_layer", "HOLD", "H-VENDOR"),
    (clean(vendor=f("V-1008")), [], "text_layer", "HOLD", "H-VENDOR"),
    (clean(vendor=f(None, "CONFLICT", readings=["V-1001", "V-1002"])), [], "text_layer", "HOLD", "H-VENDOR"),
    (clean(bank_last4=f("9931")), [], "text_layer", "HOLD", "H-VENDOR-BANK"),
    (clean(currency=f("EUR")), [], "text_layer", "HOLD", "H-CURRENCY"),
    (clean(po_number=f("PO-9999")), [], "text_layer", "HOLD", "H-PO-INVALID"),
    (clean(po_number=f("PO-4620")), [], "text_layer", "HOLD", "H-PO-INVALID"),  # another vendor's
    (clean(po_number=f("PO-4100")), [], "text_layer", "HOLD", "H-PO-INVALID"),  # closed
    (clean(po_number=MISSING), [], "text_layer", "HOLD", "H-PO-NONE"),
])
def test_decision_table(fields, ledger, read_source, decision, code):
    out = decide(fields, ledger, read_source)
    assert out["decision"] == decision, out["reasons"]
    if code:
        assert code in out["codes"], out["codes"]


def test_every_code_has_a_table_row():
    source = Path(__file__).read_text(encoding="utf-8")
    for code in ["RJ-VENDOR", "RJ-PO", "RJ-DUP", "H-READ-NONE", "H-READ-AI", "H-FIELD", "H-OVERRIDE", "H-CHECK-NOT-RUN", "H-DOC", "H-DATE", "H-ARITH", "H-TAX",
                 "H-AMOUNT", "H-VENDOR", "H-VENDOR-BANK", "H-CURRENCY", "H-PO-INVALID", "H-PO-NONE", "H-DUP-NUMBER", "H-DUP-NEAR"]:
        assert f'"{code}"' in source


@pytest.mark.parametrize("check", CHECKS)
def test_a_check_that_did_not_run_blocks_approval(check, monkeypatch):
    def not_run(self):
        self._not_run(check, "it was stubbed out")

    monkeypatch.setattr(Evaluation, f"check_{check}", not_run)
    out = decide()
    assert out["decision"] == "HOLD" and "H-CHECK-NOT-RUN" in out["codes"]
    assert check in out["checks"]["not_run"]


def test_reject_is_listed_with_holds():
    out = decide(clean(vendor=f("V-1007"), po_number=f("PO-4300"), bank_last4=f("9999")))
    assert out["decision"] == "REJECT" and "H-VENDOR-BANK" in out["codes"]


def test_tolerance_uses_lesser_of_pct_and_cap():
    assert decide()["numbers"]["tolerance"] == "248.00"  # 2% of 12,400 is under the 250 cap
    big = clean(vendor=f("V-1002"), po_number=f("PO-4620"), bank_last4=f("2093"))
    assert decide(big)["numbers"]["tolerance"] == "250.00"  # 2% of 40,000 is over the cap


def test_tolerance_applies_once_per_po_not_per_invoice():
    earlier = [row(vendor="V-1002", number="7", po="PO-4620", net="39000.00", gross="42120.00", date_="2026-09-01")]
    fields = clean(vendor=f("V-1002"), po_number=f("PO-4620"), bank_last4=f("2093"), subtotal=f("1300.00"), tax=f("104.00"), total=f("1404.00"))
    assert decide(fields, earlier)["codes"] == ["H-AMOUNT"]  # 40,300 is over 40,250, though 1,300 alone is nothing


def test_normalising_invoice_numbers():
    assert normalise_number("INV-00417") == normalise_number("417") == normalise_number("inv 417")
    assert normalise_number("AP7781-R1") != normalise_number("AP-7781")


# --- field reading on small generated pages
def page(tmp_path, lines, name="t.pdf"):
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        p.insert_text((x, y), text, fontsize=10)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


HEADER = [(48, 50, "Northwind Industrial Supply LLC"), (48, 64, "Tax ID: 84-2291047"), (400, 50, "INVOICE")]


def read(tmp_path, lines, vendor_format=None):
    vendors = {k: dict(v) for k, v in VENDORS.items()}
    if vendor_format is not None:
        vendors["V-1001"]["date_format"] = vendor_format
    return extract.extract(page(tmp_path, HEADER + lines), vendors, POLICY)


@pytest.mark.parametrize("printed,vendor_format,state,value", [
    ("2026-11-03", "", "FOUND", "2026-11-03"),
    ("3 Nov 2026", "", "FOUND", "2026-11-03"),
    ("November 3, 2026", "", "FOUND", "2026-11-03"),
    ("11/03/2026", "MDY", "FOUND", "2026-11-03"),
    ("11/03/2026", "DMY", "FOUND", "2026-03-11"),
    ("11/03/2026", "", "CONFLICT", None),
    ("25/03/2026", "", "FOUND", "2026-03-25"),
    ("05/05/2026", "", "FOUND", "2026-05-05"),
])
def test_dates_have_one_meaning_or_conflict(tmp_path, printed, vendor_format, state, value):
    field = read(tmp_path, [(48, 120, f"Invoice Date: {printed}")], vendor_format)["invoice_date"]
    assert field["state"] == state and field["value"] == value


def test_conflicted_date_offers_both_readings(tmp_path):
    field = read(tmp_path, [(48, 120, "Invoice Date: 11/03/2026")], "")["invoice_date"]
    assert set(field["readings"]) == {"2026-11-03", "2026-03-11"}


@pytest.mark.parametrize("printed,state,value", [
    ("12,400.00", "FOUND", "12400.00"), ("12400", "FOUND", "12400.00"), ("$13,462.20", "FOUND", "13462.20"),
    ("12.400,00", "CONFLICT", None), ("1,23", "CONFLICT", None), ("(500.00)", "FOUND", "-500.00"),
])
def test_amount_formats(tmp_path, printed, state, value):
    field = read(tmp_path, [(300, 120, "Total Due"), (450, 120, printed)])["total"]
    assert field["state"] == state and field["value"] == value


def test_two_different_totals_conflict(tmp_path):
    field = read(tmp_path, [(300, 120, "Total"), (450, 120, "500.00"), (300, 140, "Amount Due"), (450, 140, "550.00")])["total"]
    assert field["state"] == "CONFLICT" and set(field["readings"]) == {"500.00", "550.00"}


def test_due_date_never_fills_invoice_date(tmp_path):
    assert read(tmp_path, [(48, 120, "Due Date: 2026-12-01")])["invoice_date"]["state"] == "MISSING"


def test_customer_account_is_not_the_bank_account(tmp_path):
    field = read(tmp_path, [(48, 120, "Remit by wire"), (48, 134, "Customer Account No: 88120")])["bank_last4"]
    assert field["state"] == "MISSING"


def test_account_manager_is_not_a_bank_word(tmp_path):
    assert read(tmp_path, [(48, 120, "Account Manager: J. Lee")])["bank_last4"]["value"] == "NONE"


def test_vendor_named_bank_is_not_a_trigger(tmp_path):
    vendors = {"V-1": {**VENDORS["V-1001"], "vendor_id": "V-1", "legal_name": "River Bank Ltd", "aliases": "", "tax_id": "11-1111111"}}
    path = page(tmp_path, [(48, 50, "River Bank Ltd"), (48, 300, "Riverbank Road")])
    assert extract.extract(path, vendors, POLICY)["bank_last4"]["value"] == "NONE"


def test_no_currency_mark_is_missing(tmp_path):
    assert read(tmp_path, [(300, 120, "Total Due"), (450, 120, "500.00")])["currency"]["state"] == "MISSING"


def test_ship_to_vendor_is_not_the_issuer(tmp_path):
    path = page(tmp_path, [(48, 50, "Unknown Newco Ltd"), (320, 120, "Ship to"), (320, 134, "Northwind Industrial Supply LLC")])
    assert extract.extract(path, VENDORS, POLICY)["vendor"]["state"] == "MISSING"


def test_tax_id_and_name_disagreeing_is_a_conflict(tmp_path):
    path = page(tmp_path, [(48, 50, "Northwind Industrial Supply LLC"), (48, 64, "Tax ID: 47-1185520")])
    assert extract.extract(path, VENDORS, POLICY)["vendor"]["state"] == "CONFLICT"


def test_credit_note_title(tmp_path):
    path = page(tmp_path, [(48, 50, "Northwind Industrial Supply LLC"), (400, 50, "CREDIT NOTE")])
    assert extract.extract(path, VENDORS, POLICY)["document_type"]["value"] == "credit_note"


def test_unfamiliar_layout_holds_instead_of_guessing(tmp_path):
    """Labels this reader has never seen must end in a hold, never an approval or a guessed value."""
    path = page(tmp_path, [
        (48, 50, "Northwind Industrial Supply LLC"), (400, 50, "INVOICE"),
        (48, 120, "Bill Ref 55123"), (48, 134, "Raised on 2026-10-26"), (48, 148, "Your Order 4501"),
        (300, 300, "Amount Payable"), (450, 300, "USD 10,800.00"),
    ])
    fields = extract.extract(path, VENDORS, POLICY)
    for name in ("invoice_number", "invoice_date", "po_number", "total"):
        assert fields[name]["state"] == "MISSING", name
    assert decide(fields)["decision"] == "HOLD"
