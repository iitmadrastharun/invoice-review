"""The decision rules. Pure: structured invoice record in, check results out. No PDFs, no AI, no database.

Order is always the same:
  1. no trusted reading        -> HOLD
  2. a reject rule fired       -> REJECT
  3. any hold rule fired       -> HOLD
  4. a required check not run  -> HOLD (H-CHECK-NOT-RUN)
  5. otherwise                 -> APPROVE
"""
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

USABLE = {"FOUND", "ENTERED", "OVERRIDDEN", "PROPOSED"}

CHECKS = ["document_type", "required_fields", "arithmetic", "net_and_tax", "date", "vendor_status",
          "bank_details", "currency", "po", "duplicates", "amount_tolerance", "goods_receipt"]
CHECK_LABELS = {
    "document_type": "Document type", "required_fields": "Required fields", "arithmetic": "Arithmetic",
    "net_and_tax": "Net and tax", "date": "Date", "vendor_status": "Vendor status", "bank_details": "Bank details",
    "currency": "Currency", "po": "PO", "duplicates": "Duplicates", "amount_tolerance": "Amount tolerance",
    "goods_receipt": "Goods receipt", "sender": "Sender",
}
STAGE_CHECKS = {
    5: ["document_type", "required_fields", "arithmetic", "net_and_tax", "date", "currency"],
    6: ["vendor_status", "bank_details"],
    7: ["po"],
    8: ["duplicates"],
    9: ["amount_tolerance", "goods_receipt"],
}
# Plain names for each code, for the dashboard and the drafted messages.
CODE_NAMES = {
    "RJ-VENDOR": "Blocked vendor", "RJ-PO": "Cancelled PO", "RJ-DUP": "Exact duplicate",
    "H-READ-NONE": "Not machine-readable", "H-READ-AI": "AI reading to confirm", "H-READ-DISAGREE": "AI and rules read differently",
    "H-FIELD": "Missing details", "H-OVERRIDE": "Printed value changed", "H-CHECK-NOT-RUN": "A check could not run",
    "H-DOC": "Not a payable invoice", "H-DATE": "Date problem", "H-ARITH": "Sums do not add up", "H-TAX": "Tax problem",
    "H-AMOUNT": "Over the PO limit", "H-RECEIPT": "Goods not received", "H-VENDOR": "Vendor not approved",
    "H-VENDOR-BANK": "Bank details changed", "H-SENDER": "Unknown sender", "H-CURRENCY": "Currency problem",
    "H-PO-INVALID": "PO problem", "H-PO-NONE": "No PO", "H-DUP-NUMBER": "Possible duplicate", "H-DUP-NEAR": "Possible duplicate",
}
FIELD_LABELS = {
    "vendor": "Vendor", "invoice_number": "Invoice number", "invoice_date": "Invoice date", "po_number": "PO number",
    "subtotal": "Subtotal", "tax": "Tax", "total": "Total", "currency": "Currency", "bank_last4": "Bank last 4",
    "document_type": "Document type",
}
# Which hold explains the decision first when several fired.
HOLD_PRIORITY = ["H-READ-NONE", "H-READ-AI", "H-OVERRIDE", "H-SENDER", "H-VENDOR-BANK", "H-READ-DISAGREE", "H-DOC", "H-DUP-NUMBER", "H-DUP-NEAR",
                 "H-AMOUNT", "H-RECEIPT", "H-VENDOR", "H-PO-INVALID", "H-PO-NONE", "H-CURRENCY", "H-FIELD", "H-ARITH", "H-TAX", "H-DATE",
                 "H-CHECK-NOT-RUN"]
CENTS = Decimal("0.01")


def D(value):
    return Decimal(str(value))


def cents(value):
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def fmt(value):
    """Indian digit grouping with the rupee sign: ₹12,46,500.00."""
    value = cents(D(value))
    sign, whole, paise = "-" if value < 0 else "", f"{abs(value):.2f}".split(".")[0], f"{abs(value):.2f}".split(".")[1]
    head, tail = whole[:-3], whole[-3:]
    while len(head) > 2:
        tail, head = f"{head[-2:]},{tail}", head[:-2]
    return f"{sign}₹{head + ',' if head else ''}{tail}.{paise}"


def normalise_number(number):
    if not number:
        return None
    text = re.sub(r"[\s\-/.#]", "", str(number)).upper()
    text = re.sub(r"^(INVOICE|INV|NO)", "", text)
    return text.lstrip("0") or text


def normalise_po(po):
    return re.sub(r"\s", "", str(po)).upper() if po else None


class Evaluation:
    def __init__(self, fields, vendors, pos, ledger, policy, today, read_source="text_layer", receipts=None, sender=None):
        self.fields = fields
        self.vendors = vendors
        self.pos = pos
        self.ledger = ledger
        self.policy = policy
        self.today = today
        self.read_source = read_source
        self.receipts = receipts  # {po_number: [receipt rows]}; None means the receipt data is not available
        self.sender = sender  # the email address an invoice came from, when it came by email
        self.checks = {}
        self.findings = []  # dict(code, sentence, action, owner)
        self.notes = []
        self.numbers = {}
        self.net = None
        self.gross = None
        self.vendor = vendors.get(self.value("vendor") or "")
        self.po_number = normalise_po(self.value("po_number"))
        self.po = pos.get(self.po_number or "")
        self.po_ok = False

    # ---- helpers
    def field(self, name):
        return self.fields.get(name) or {"state": "MISSING", "value": None}

    def usable(self, name):
        f = self.field(name)
        return f["state"] in USABLE and f.get("value") not in (None, "")

    def value(self, name):
        return self.field(name).get("value") if self.usable(name) else None

    def amount(self, name):
        return D(self.value(name)) if self.usable(name) else None

    def buyer(self):
        return self.po["buyer"] if self.po else "the buyer"

    def vendor_name(self):
        return self.vendor["legal_name"] if self.vendor else "the vendor"

    def fired(self, code):
        return any(f["code"] == code for f in self.findings)

    def _find(self, code, sentence, action, owner="AP clerk"):
        self.findings.append({"code": code, "sentence": sentence, "action": action, "owner": owner})

    def _result(self, name, status, sentence, **detail):
        self.checks[name] = {"name": name, "label": CHECK_LABELS[name], "status": status, "sentence": sentence, **detail}

    def _not_run(self, name, reason, covered_by=None):
        self._result(name, "NOT_RUN", f"Not run because {reason}.", covered_by=covered_by)

    # ---- checks
    def check_document_type(self):
        kind = self.value("document_type")
        total = self.amount("total")
        if total is not None and total < 0:
            self._find("H-DOC", f"The total is negative ({fmt(total)}), so this is not a payable invoice.", "Route it to whoever handles credits. It must not enter the PO's running total.")
            return self._result("document_type", "HOLD", "A negative total means this is not a single payable invoice.")
        if kind is None:
            reason = self.field("document_type").get("reason", "the document type could not be read")
            self._find("H-DOC", reason, "Check what this document is. Only a payable invoice can go further.")
            return self._result("document_type", "HOLD", reason)
        if kind != "invoice":
            name = kind.replace("_", " ")
            self._find("H-DOC", f"This is a {name}, not a payable invoice.", "Route it to whoever handles these. It must not enter the PO's running total.")
            return self._result("document_type", "HOLD", f"This is a {name}, not a payable invoice.")
        self._result("document_type", "PASS", "This is an invoice.")

    def check_required_fields(self):
        missing = []
        for name in ["invoice_number", "invoice_date", "total"]:
            f = self.field(name)
            if f["state"] == "CONFLICT":
                readings = " or ".join(str(r) for r in f.get("readings", []))
                missing.append(f"{FIELD_LABELS[name]} could mean {readings}")
            elif not self.usable(name):
                missing.append(f"{FIELD_LABELS[name]} is missing")
        if missing:
            sentence = "; ".join(missing) + "."
            self._find("H-FIELD", sentence, f"Get the missing or unclear details from {self.vendor_name()} and enter them, then re-run. Nothing is assumed.")
            return self._result("required_fields", "HOLD", sentence)
        self._result("required_fields", "PASS", "Invoice number, date and total are all present.")

    def check_arithmetic(self):
        subtotal, tax, total = self.amount("subtotal"), self.amount("tax"), self.amount("total")
        if total is None:
            return self._not_run("arithmetic", "there is no usable total", "H-FIELD")
        if subtotal is None or tax is None:
            return self._result("arithmetic", "PASS", "Only part of the sum is printed, so there is nothing further to add up.")
        gap = abs(subtotal + tax - total)
        if gap > D(self.policy["arith_cents"]):
            sentence = f"Subtotal {fmt(subtotal)} + tax {fmt(tax)} = {fmt(subtotal + tax)}, but the invoice says {fmt(total)}."
            self._find("H-ARITH", sentence, f"Ask {self.vendor_name()} to correct and reissue the invoice.")
            return self._result("arithmetic", "HOLD", sentence)
        self._result("arithmetic", "PASS", f"Subtotal {fmt(subtotal)} + tax {fmt(tax)} = total {fmt(total)}.")

    def check_net_and_tax(self):
        subtotal, tax, total = self.amount("subtotal"), self.amount("tax"), self.amount("total")
        included = self.value("tax_included_rate")
        rate = None
        source = None
        if subtotal is not None:
            self.net, source = subtotal, "printed subtotal"
            if tax is None and total is not None:
                tax = total - subtotal
        elif tax is not None and total is not None:
            self.net, source = total - tax, "derived: total minus tax"
        elif total is not None and included is not None:
            rate = D(included)
            self.net, source = cents(total / (1 + rate)), f"derived: total divided by 1 + {rate * 100:.4g}% printed tax rate"
            tax = total - self.net
            self.notes.append("Tax is embedded in the total, so net is worked out from the printed rate. Header test only: there are no separate lines to check.")
        self.gross = total if total is not None else (self.net + tax if self.net is not None and tax is not None else None)
        po_rate = D(self.po["tax_rate"]) if self.po else None

        if self.net is None:
            if total is None and subtotal is None:
                return self._not_run("net_and_tax", "there is no usable total", "H-FIELD")
            hint = ""
            if total is not None and po_rate is not None:
                hint = f" At the PO's {po_rate * 100:.4g}% rate, net would be {fmt(total / (1 + po_rate))}."
            sentence = "Only a total is printed, with nothing about tax, so the net amount cannot be established." + hint
            self._find("H-TAX", sentence, f"Get the tax split from {self.vendor_name()} and enter the figures. The net amount is not assumed.")
            return self._result("net_and_tax", "HOLD", sentence)
        self.numbers["this_invoice_net"] = str(cents(self.net))
        if po_rate is None:
            return self._result("net_and_tax", "PASS", f"Net {fmt(self.net)} ({source}). Tax rate not compared because there is no PO to compare it with.")
        if rate is None and self.net:
            rate = (tax / self.net) if tax is not None else None
        if rate is None:
            return self._result("net_and_tax", "PASS", f"Net {fmt(self.net)} ({source}).")
        if abs(rate - po_rate) > D(self.policy["tax_rate_points"]):
            sentence = f"Tax is {rate * 100:.2f}% of net, but {self.po_number} is at {po_rate * 100:.2f}%."
            self._find("H-TAX", sentence, f"Ask {self.buyer()} whether this tax rate is right, or ask {self.vendor_name()} to correct it.")
            return self._result("net_and_tax", "HOLD", sentence)
        self._result("net_and_tax", "PASS", f"Net {fmt(self.net)} ({source}). Tax rate {rate * 100:.2f}% matches the PO.")

    def check_date(self):
        if not self.usable("invoice_date"):
            return self._not_run("date", "the invoice date is not usable", "H-FIELD")
        invoice_date = date.fromisoformat(self.value("invoice_date"))
        problem = None
        if (invoice_date - self.today).days > 1:
            problem = f"The invoice date {invoice_date} is in the future."
        elif self.po and invoice_date < date.fromisoformat(self.po["po_date"]):
            problem = f"The invoice date {invoice_date} is earlier than {self.po_number}'s date {self.po['po_date']}."
        elif (self.today - invoice_date).days > self.policy["max_invoice_age_days"]:
            problem = f"The invoice date {invoice_date} is over {self.policy['max_invoice_age_days']} days old."
        if problem:
            self._find("H-DATE", problem, f"Confirm the date with {self.vendor_name()}.")
            return self._result("date", "HOLD", problem)
        self._result("date", "PASS", f"Invoice date {invoice_date} is acceptable.")

    def check_currency(self):
        if self.field("currency")["state"] == "CONFLICT":
            readings = " and ".join(self.field("currency").get("readings", []))
            self._find("H-CURRENCY", f"More than one currency is printed: {readings}.", f"Ask {self.vendor_name()} which currency applies.")
            return self._result("currency", "HOLD", f"More than one currency is printed: {readings}.")
        if not self.usable("currency"):
            return self._not_run("currency", "no currency mark is printed")
        code = self.value("currency")
        if not self.po:
            return self._not_run("currency", "there is no PO to compare it with", "PO")
        if code != self.po["currency"]:
            sentence = f"The invoice is in {code}; {self.po_number} is in {self.po['currency']}."
            self._find("H-CURRENCY", sentence, f"Ask {self.buyer()} how this should be handled. There is no currency conversion here.")
            return self._result("currency", "HOLD", sentence)
        self._result("currency", "PASS", f"Currency {code} matches the PO.")

    def check_vendor_status(self):
        f = self.field("vendor")
        if f["state"] == "CONFLICT":
            sentence = f.get("reason", "This invoice points to more than one vendor.")
            self._find("H-VENDOR", sentence, "Check who issued this invoice before doing anything else.")
            return self._result("vendor_status", "HOLD", sentence)
        if not self.vendor:
            printed = f.get("printed")
            named = f' ("{printed}")' if printed else ""
            sentence = f"No approved-vendor record matches this invoice's issuer{named}."
            self._find("H-VENDOR", sentence, "Send the vendor through onboarding, or correct the vendor if the reading is wrong.")
            return self._result("vendor_status", "HOLD", sentence)
        status = self.vendor["status"]
        name = self.vendor["legal_name"]
        how = f.get("label") or "entered value"
        if status == "blocked":
            self._find("RJ-VENDOR", f"{name} is blocked in the vendor master.", "Send the rejection to the vendor. A blocked vendor cannot be paid.", "AP clerk")
            return self._result("vendor_status", "REJECT", f"{name} ({self.vendor['vendor_id']}) is blocked.")
        if status != "approved":
            self._find("H-VENDOR", f"{name} is still in {status}, not approved.", "Wait for onboarding to finish, or ask procurement to expedite it.")
            return self._result("vendor_status", "HOLD", f"{name} is still in {status}.")
        self._result("vendor_status", "PASS", f"Matched {name} ({self.vendor['vendor_id']}) by {how}. Status approved.")

    def check_bank_details(self):
        if not self.vendor:
            return self._not_run("bank_details", "the vendor is not known", "H-VENDOR")
        f = self.field("bank_last4")
        if f["state"] == "CONFLICT":
            readings = " and ".join(f.get("readings", []))
            sentence = f"More than one bank account is printed, ending {readings}."
            self._find("H-VENDOR-BANK", sentence, f"Call {self.vendor['legal_name']} on the number on file ({self.vendor['callback_phone']}). Never use a number on the invoice.")
            return self._result("bank_details", "HOLD", sentence)
        if not self.usable("bank_last4"):
            return self._not_run("bank_details", f.get("reason", "the bank details could not be read").rstrip(".").lower())
        printed = self.value("bank_last4")
        if printed == "NONE":
            self.notes.append("No bank details printed. Pay only the account on file.")
            return self._result("bank_details", "PASS", "No bank details printed. Pay only the account on file.")
        master = self.vendor["bank_last4"]
        if not master:
            return self._not_run("bank_details", "the vendor master has no bank details yet", "H-VENDOR")
        if printed != master:
            sentence = f"The invoice asks for payment to an account ending {printed}; the account on file ends {master}."
            self._find("H-VENDOR-BANK", sentence, f"Call {self.vendor['legal_name']} on the number on file ({self.vendor['callback_phone']}). Never use a number on the invoice.")
            return self._result("bank_details", "HOLD", sentence)
        self._result("bank_details", "PASS", f"Bank account ending {printed} matches the account on file.")

    def check_po(self):
        f = self.field("po_number")
        if not self.po_number:
            candidates = self._po_candidates()
            if f["state"] == "CONFLICT":
                readings = " and ".join(str(r) for r in f.get("readings", []))
                lead = f"Several POs are printed: {readings}."
            else:
                lead = "No PO number is printed."
            if len(candidates) == 1:
                sentence = f"{lead} {candidates[0]} is the only open PO for this vendor with room, so it is suggested, not assumed."
                action = f"Ask the requester to confirm {candidates[0]}, then enter it and re-run."
            elif candidates:
                sentence = f"{lead} Possible POs: {', '.join(candidates)}."
                action = "Ask the requester which PO this belongs to, then enter it and re-run."
            else:
                sentence = f"{lead} No open PO for this vendor has room for it."
                action = "Ask the requester which PO this belongs to."
            self._find("H-PO-NONE", sentence, action)
            return self._result("po", "HOLD", sentence, candidates=candidates)
        if not self.po:
            sentence = f"{self.po_number} is not in the PO data."
            self._find("H-PO-INVALID", sentence, f"Ask {self.vendor_name()} or the requester for the right PO number.")
            return self._result("po", "HOLD", sentence)
        status = self.po["status"]
        owned = self.vendor is not None and self.po["vendor_id"] == self.vendor["vendor_id"]
        if status == "cancelled" and owned:
            self._find("RJ-PO", f"{self.po_number} was cancelled.", "Send the rejection to the vendor, and tell them this PO is cancelled.")
            return self._result("po", "REJECT", f"{self.po_number} was cancelled.")
        if self.vendor and not owned:
            sentence = f"{self.po_number} belongs to another vendor, not {self.vendor['legal_name']}."
            self._find("H-PO-INVALID", sentence, f"Ask {self.buyer()} which PO this invoice belongs to.")
            return self._result("po", "HOLD", sentence)
        if status != "open":
            sentence = f"{self.po_number} is {status}."
            self._find("H-PO-INVALID", sentence, f"Ask {self.buyer()} whether this PO should be reopened.")
            return self._result("po", "HOLD", sentence)
        if not self.vendor:
            return self._not_run("po", "the vendor is not known, so ownership cannot be checked", "H-VENDOR")
        self.po_ok = True
        how = "printed on the invoice" if f["state"] in ("FOUND", "PROPOSED") else "entered by the clerk"
        self._result("po", "PASS", f"{self.po_number} {how}: open, same vendor, net {fmt(self.po['net_amount'])}.")

    def _po_candidates(self):
        if not self.vendor:
            return []
        out = []
        for po in self.pos.values():
            if po["vendor_id"] != self.vendor["vendor_id"] or po["status"] != "open":
                continue
            if self.net is None or self._after(po["po_number"]) <= self._limit(po):
                out.append(po["po_number"])
        return out

    def _limit(self, po):
        net = D(po["net_amount"])
        return net + min(net * D(self.policy["amount_pct"]), D(self.policy["amount_cap"]))

    def _approved_to_date(self, po_number):
        return sum((D(r["net"]) for r in self.ledger if r["po_number"] == po_number and r["status"] == "APPROVED" and r["net"] is not None), D(0))

    def _after(self, po_number):
        return self._approved_to_date(po_number) + (self.net or D(0))

    def check_duplicates(self):
        if not self.vendor:
            return self._not_run("duplicates", "the vendor is not known", "H-VENDOR")
        gross = self.gross
        if gross is None:
            return self._not_run("duplicates", "there is no usable total", "H-FIELD")
        number = normalise_number(self.value("invoice_number"))
        printed = self.value("invoice_number")
        invoice_date = self.value("invoice_date")
        vendor_id = self.vendor["vendor_id"]
        fired = None
        earlier_rejected = []
        for row in self.ledger:
            if row["vendor_id"] != vendor_id or row["gross"] is None:
                continue
            same_number = number is not None and row["number_norm"] == number
            gap = abs(D(row["gross"]) - gross)
            where = f"{row['invoice_number'] or 'an invoice with no number'} in run {row['last_run_id']}"
            if row["status"] == "REJECTED":
                if same_number or gap <= D(self.policy["near_dup_amount"]):
                    earlier_rejected.append(where)
                continue
            if same_number:
                same_date = invoice_date is not None and row["invoice_date"] == invoice_date
                if row["status"] == "APPROVED" and gap <= CENTS and same_date:
                    self._find("RJ-DUP", f"This is the same invoice as {where}: same number, amount and date, already approved. Reset the demo data to start over.",
                               "Send the rejection to the vendor. It has already been approved for payment.")
                    return self._result("duplicates", "REJECT", f"Same number, amount and date as {where}, already approved.", other_run=row["last_run_id"])
                if row["status"] == "HELD":
                    why = "that one is still with the clerk"
                elif gap > CENTS:
                    why = f"the amount differs ({fmt(row['gross'])} then, {fmt(gross)} now), so this may be a correction"
                else:
                    why = f"the date differs ({row['invoice_date']} then, {invoice_date} now), so this may be a re-dated resend or a vendor reusing numbers"
                fired = fired or ("H-DUP-NUMBER", f"Same invoice number as {where}, but {why}.", row)
            elif gap <= D(self.policy["near_dup_amount"]):
                tests = self._near_tests(row, invoice_date, number)
                if tests:
                    sentence = f"Different number, but the same amount as {where}: {', '.join(tests)}. Could be a resend under a new number, or a genuine second order."
                    if fired is None:
                        fired = ("H-DUP-NEAR", sentence, row)
        for where in earlier_rejected:
            self.notes.append(f"An earlier version was rejected: {where}.")
        if fired:
            code, sentence, row = fired
            self._find(code, sentence, f"Compare the two invoices side by side, then check against {self.vendor['legal_name']}'s statement.")
            return self._result("duplicates", "HOLD", sentence, other_run=row["last_run_id"])
        self._result("duplicates", "PASS", "No earlier invoice with this number or amount for this vendor.")

    def _near_tests(self, row, invoice_date, number):
        tests = []
        if invoice_date and row["invoice_date"]:
            days = abs((date.fromisoformat(invoice_date) - date.fromisoformat(row["invoice_date"])).days)
            if days <= self.policy["near_dup_days"]:
                tests.append(f"{days} days apart")
        if self.po_number and row["po_number"] == self.po_number:
            tests.append("same PO")
        minimum = self.policy["dup_number_min_chars"]
        other = row["number_norm"]
        if number and other and min(len(number), len(other)) >= minimum and (number in other or other in number):
            tests.append("related invoice number")
        return tests

    def check_amount_tolerance(self):
        if self.fired("RJ-DUP"):
            return self._not_run("amount_tolerance", "this invoice is already counted in the PO's running total", "RJ-DUP")
        if not self.po_ok:
            cover = next((c for c in ("RJ-PO", "H-PO-NONE", "H-PO-INVALID", "H-VENDOR", "RJ-VENDOR") if self.fired(c)), None)
            return self._not_run("amount_tolerance", "there is no valid PO to compare with", cover)
        if self.net is None:
            cover = "H-TAX" if self.fired("H-TAX") else "H-FIELD"
            return self._not_run("amount_tolerance", "the net amount is not known", cover)
        po_net = D(self.po["net_amount"])
        tolerance = min(po_net * D(self.policy["amount_pct"]), D(self.policy["amount_cap"]))
        limit = po_net + tolerance
        to_date = self._approved_to_date(self.po_number)
        after = to_date + self.net
        table = {"po_net": str(cents(po_net)), "tolerance": str(cents(tolerance)), "limit": str(cents(limit)), "approved_to_date": str(cents(to_date)),
                 "this_invoice_net": str(cents(self.net)), "after_this_invoice": str(cents(after))}
        self.numbers.update(table)
        held = [r for r in self.ledger if r["po_number"] == self.po_number and r["status"] == "HELD"]
        held_net = sum((D(r["net"]) for r in held if r["net"] is not None), D(0))
        if held and after + held_net > limit:
            self.notes.append(f"Other invoices on this PO are on hold ({fmt(held_net)}). Approving them would go over the limit.")
        if after > limit:
            over = after - limit
            self.numbers["over_limit_by"] = str(cents(over))
            sentence = f"This invoice takes {self.po_number} to {fmt(after)}. The limit is {fmt(limit)}."
            reissue = limit - to_date
            action = f"Ask {self.po['buyer']} for a change order of at least {fmt(over)}, or ask {self.vendor_name()} to reissue at {fmt(reissue)} or less."
            self._find("H-AMOUNT", sentence, action)
            return self._result("amount_tolerance", "HOLD", f"{fmt(to_date)} approved so far + {fmt(self.net)} this invoice = {fmt(after)}, which is {fmt(over)} over the {fmt(limit)} limit.", table=table)
        if after > po_net:
            over = after - po_net
            self.numbers["over_po_by"] = str(cents(over))
            self.notes.append(f"{fmt(over)} over the PO, inside the {fmt(tolerance)} tolerance. Variance recorded.")
            return self._result("amount_tolerance", "PASS", f"Net {fmt(after)} against a limit of {fmt(limit)}: {fmt(over)} over the PO, inside the {fmt(tolerance)} tolerance.", table=table)
        self._result("amount_tolerance", "PASS", f"Running total {fmt(after)} is within the PO's {fmt(po_net)}. {fmt(po_net - after)} stays open.", table=table)

    def check_goods_receipt(self):
        """Three-way match: what has been billed on the PO must not run ahead of what has been received."""
        if self.fired("RJ-DUP"):
            return self._not_run("goods_receipt", "this invoice is already counted against the PO", "RJ-DUP")
        if not self.po_ok:
            cover = next((c for c in ("RJ-PO", "H-PO-NONE", "H-PO-INVALID", "H-VENDOR", "RJ-VENDOR") if self.fired(c)), None)
            return self._not_run("goods_receipt", "there is no valid PO to compare with", cover)
        if self.net is None:
            cover = "H-TAX" if self.fired("H-TAX") else "H-FIELD"
            return self._not_run("goods_receipt", "the net amount is not known", cover)
        if self.receipts is None:
            return self._not_run("goods_receipt", "the goods receipt data is not available")
        rows = self.receipts.get(self.po_number, [])
        received = sum((D(r["net_value"]) for r in rows), D(0))
        billed = self._approved_to_date(self.po_number) + self.net
        tolerance = min(D(self.po["net_amount"]) * D(self.policy["amount_pct"]), D(self.policy["amount_cap"]))
        receipt = {"received": str(cents(received)), "billed_after": str(cents(billed)), "receipts": len(rows),
                   "latest": max((r["received_date"] for r in rows), default=None)}
        self.numbers.update({"received_to_date": receipt["received"], "billed_after_this_invoice": receipt["billed_after"]})
        if not rows:
            sentence = f"No goods receipt has been posted for {self.po_number}, so nothing confirms the goods or services arrived."
            self._find("H-RECEIPT", sentence, f"Ask {self.buyer()} to confirm receipt in the system, then re-run.")
            return self._result("goods_receipt", "HOLD", sentence, receipt=receipt)
        if billed > received + tolerance:
            short = billed - received
            sentence = f"Goods received on {self.po_number} are worth {fmt(received)}, but billing would reach {fmt(billed)}: {fmt(short)} more than has arrived."
            pay_now = received - self._approved_to_date(self.po_number)
            action = (f"Ask {self.buyer()} whether the rest has arrived. Until receiving confirms it, ask {self.vendor_name()} to bill "
                      f"{fmt(max(pay_now, D(0)))} now and the rest on delivery.")
            self._find("H-RECEIPT", sentence, action)
            return self._result("goods_receipt", "HOLD", sentence, receipt=receipt)
        count = f"{len(rows)} receipt{'s' if len(rows) != 1 else ''}"
        self._result("goods_receipt", "PASS", f"Received {fmt(received)} on {self.po_number} ({count}, latest {receipt['latest']}). Billing of {fmt(billed)} is covered.", receipt=receipt)

    def check_sender(self):
        """Only for invoices that arrived by email: the sender's domain must be the vendor's domain on file."""
        domain = self.sender.rsplit("@", 1)[-1].lower().strip(" >")
        if not self.vendor:
            return self._result("sender", "NOT_RUN", f"Sent from {self.sender}. Not compared because the vendor is not known.")
        on_file = (self.vendor.get("email_domain") or "").lower()
        if not on_file:
            return self._result("sender", "NOT_RUN", f"Sent from {self.sender}. {self.vendor_name()} has no email domain on file.")
        if domain != on_file and not domain.endswith("." + on_file):
            sentence = f"This invoice was emailed from {self.sender}, but {self.vendor_name()}'s domain on file is {on_file}."
            self._find("H-SENDER", sentence, f"Do not reply to that address. Call {self.vendor_name()} on the number on file ({self.vendor['callback_phone']}) to confirm they sent it.")
            return self._result("sender", "HOLD", sentence)
        self._result("sender", "PASS", f"Sent from {self.sender}, which matches {self.vendor_name()}'s domain on file.")

    def apply_cross_check(self, differences):
        """A second, independent reading of the page by Claude. It can only add a hold, never clear one."""
        if not differences:
            return
        parts = [f"{FIELD_LABELS.get(d['field'], d['field'])}: rules read {d['rules']}, Claude read {d['ai']}" for d in differences]
        self._find("H-READ-DISAGREE", "Two independent readings of this page disagree. " + "; ".join(parts) + ".",
                   "Look at the highlighted fields on the page. If a reading is wrong, correct it and re-run; if the rules' reading is right, approve with a note.")

    # ---- running
    def run_stage(self, stage):
        names = list(STAGE_CHECKS[stage])
        if stage == 6 and self.sender:
            names.append("sender")
        for name in names:
            getattr(self, f"check_{name}")()
        return [self.checks[n] for n in names]

    def run_all(self):
        # Currency and net compare against the PO, which is looked up in __init__.
        for stage in sorted(STAGE_CHECKS):
            self.run_stage(stage)
        return self.decide()

    def decide(self):
        ran = [c for c in CHECKS if self.checks.get(c, {}).get("status") != "NOT_RUN"]
        codes = [f["code"] for f in self.findings]
        overridden = [n for n, f in self.fields.items() if f["state"] == "OVERRIDDEN"]
        if overridden:
            parts = []
            for name in overridden:
                f = self.fields[name]
                parts.append(f"{FIELD_LABELS.get(name, name)} changed from {f.get('old_value') or 'blank'} to {f['value']} by the clerk")
            self._find("H-OVERRIDE", ". ".join(parts) + ".", "Changing a printed value needs a person's final decision, with a note saying who confirmed it.")
            codes.append("H-OVERRIDE")
        not_run = [c for c in CHECKS if self.checks.get(c, {}).get("status") == "NOT_RUN"]
        uncovered = []
        for name in not_run:
            cover = self.checks[name].get("covered_by")
            if cover == "PO":
                covered = any(self.fired(c) for c in ("RJ-PO", "H-PO-NONE", "H-PO-INVALID"))
            else:
                covered = cover is not None and self.fired(cover)
            if not covered and self.read_source != "none":
                uncovered.append(name)
        if uncovered:
            names = ", ".join(CHECK_LABELS[c].lower() for c in uncovered)
            why = "; ".join(self.checks[c]["sentence"] for c in uncovered)
            self._find("H-CHECK-NOT-RUN", f"The {names} check could not run. {why}", "Check this by hand against the page, or enter the value and re-run.")
            codes.append("H-CHECK-NOT-RUN")

        rejects = [f for f in self.findings if f["code"].startswith("RJ-")]
        holds = sorted((f for f in self.findings if f["code"].startswith("H-")), key=lambda f: HOLD_PRIORITY.index(f["code"]))
        result = {"ran": len(ran), "of": len(CHECKS), "not_run": not_run}
        if self.read_source not in ("text_layer", "clerk_entered"):
            first = self._untrusted_finding()
            preview = rejects[0]["code"] if rejects else None
            return self._output("HOLD", [first] + rejects + holds, result, preview_reject=preview)
        if rejects:
            return self._output("REJECT", rejects + holds, result)
        if holds:
            return self._output("HOLD", holds, result)
        return self._output("APPROVE", [], result)

    def _untrusted_finding(self):
        if self.read_source == "ai":
            return {"code": "H-READ-AI", "sentence": "This is a scan, and an AI's reading is not trusted to move money.",
                    "action": "Check the values against the page, correct anything wrong, then re-run.", "owner": "AP clerk"}
        return {"code": "H-READ-NONE", "sentence": "This document could not be read automatically.",
                "action": "Type the values from the page, then re-run. If the file is corrupt or locked, ask the vendor to resend it.", "owner": "AP clerk"}

    def _output(self, decision, findings, result, preview_reject=None):
        if decision == "APPROVE":
            over = self.numbers.get("over_po_by")
            lead = f"Approved for payment. {fmt(over)} over the PO, inside the {fmt(self.numbers['tolerance'])} tolerance." if over else "Approved for payment. Every check passed."
            summary = f"{lead} {result['ran']} of {result['of']} checks ran."
            next_step = {"owner": "AP clerk", "action": "Release to the next payment run. No payment has been made."}
        else:
            summary = findings[0]["sentence"]
            next_step = {"owner": findings[0]["owner"], "action": findings[0]["action"]}
        notes = list(self.notes)
        if preview_reject:
            notes.append(f"Preview only: on these AI-read values, this would be rejected ({preview_reject}) once a person confirms them.")
        return {
            "decision": decision,
            "summary": summary,
            "codes": [f["code"] for f in findings],
            "reasons": [{"code": f["code"], "sentence": f["sentence"], "action": f["action"], "owner": f["owner"]} for f in findings],
            "checks": result,
            "check_results": [self.checks[c] for c in CHECKS + ["sender"] if c in self.checks],
            "next_step": next_step,
            "numbers": self.numbers,
            "notes": notes,
            "net": str(cents(self.net)) if self.net is not None else None,
            "gross": str(cents(self.gross)) if self.gross is not None else None,
        }


def evaluate(fields, vendors, pos, ledger, policy, today, read_source="text_layer", receipts=None, sender=None):
    return Evaluation(fields, vendors, pos, ledger, policy, today, read_source, receipts, sender).run_all()
