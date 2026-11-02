"""Builds every sample invoice PDF. All companies and numbers are made up.

Three layouts, each carrying one trap for the field reader:
  A: line table with a "Total" column
  B: "Invoice No. / PO No. / Date / Due Date" header row above a value row
  C: "Ship to" names another vendor in the master, "Bill to" prints the buyer's tax ID
"""
from pathlib import Path

import pymupdf

ROOT = Path(__file__).parent
HISTORY = ROOT / "samples" / "history"
LIVE = ROOT / "samples" / "live"

INK = (0.13, 0.15, 0.18)
MUTED = (0.42, 0.45, 0.5)
RULE = (0.78, 0.8, 0.83)


def money(v):
    return f"{v:,.2f}"


class Sheet:
    def __init__(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=612, height=792)

    def text(self, x, y, s, size=9.5, bold=False, color=INK):
        self.page.insert_text((x, y), s, fontsize=size, fontname="hebo" if bold else "helv", color=color)

    def right(self, x, y, s, size=9.5, bold=False, color=INK):
        font = "hebo" if bold else "helv"
        w = pymupdf.get_text_length(s, fontname=font, fontsize=size)
        self.page.insert_text((x - w, y), s, fontsize=size, fontname=font, color=color)

    def rule(self, y, x0=48, x1=564, color=RULE, width=0.7):
        self.page.draw_line((x0, y), (x1, y), color=color, width=width)

    def band(self, y0, y1, color):
        self.page.draw_rect(pymupdf.Rect(48, y0, 564, y1), color=color, fill=color)

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(path)
        self.doc.close()


def layout_a(path, inv, accent=(0.1, 0.27, 0.45)):
    """Ruled table with a Total column; label and value side by side."""
    s = Sheet()
    s.text(48, 62, inv["vendor"], 15, True, accent)
    y = 78
    for line in inv["address"]:
        s.text(48, y, line, 9, color=MUTED)
        y += 12
    s.text(48, y, f"Tax ID: {inv['tax_id']}", 9, color=MUTED)

    s.right(564, 62, "INVOICE", 20, True, accent)
    labels = [("Invoice No:", inv.get("number")), ("Invoice Date:", inv["date"]), ("PO Number:", inv.get("po")), ("Terms:", "Net 30")]
    y = 84
    for label, value in labels:
        if value:
            s.text(392, y, label, 9.5, color=MUTED)
            s.text(472, y, value, 9.5, True)
            y += 14

    s.text(48, 158, "Bill To", 9, True, accent)
    s.text(48, 171, "Halden Manufacturing Inc")
    s.text(48, 183, "2200 Foundry Road, Dayton, OH 45402")

    top = 214
    s.band(top - 12, top + 5, accent)
    white = (1, 1, 1)
    s.text(54, top, "Item", 9, True, white)
    s.text(118, top, "Description", 9, True, white)
    s.right(392, top, "Qty", 9, True, white)
    s.right(478, top, "Unit Price", 9, True, white)
    s.right(558, top, "Total", 9, True, white)
    y = top + 21
    for code, desc, qty, unit in inv["lines"]:
        s.text(54, y, code)
        s.text(118, y, desc)
        s.right(392, y, str(qty))
        s.right(478, y, money(unit))
        s.right(558, y, money(qty * unit))
        s.rule(y + 6)
        y += 20

    y += 14
    cur = inv.get("symbol", "$")
    s.text(392, y, "Subtotal")
    s.right(558, y, f"{cur}{money(inv['net'])}")
    y += 15
    s.text(392, y, "Sales Tax (8%)")
    s.right(558, y, f"{cur}{money(inv['tax'])}")
    y += 7
    s.rule(y, 392, 564, accent, 1)
    y += 14
    s.text(392, y, "Total Due", 11, True)
    s.right(558, y, f"{cur}{money(inv['net'] + inv['tax'])}", 11, True)

    y = 610
    s.text(48, y, "Payment Instructions", 9, True, accent)
    s.text(48, y + 13, inv["bank_name"])
    s.text(48, y + 25, f"Account No: {inv['account']}")
    s.text(48, y + 37, "Routing: 041000124")
    s.rule(740)
    s.text(48, 754, "Thank you for your business. Questions about this invoice: billing@example.com", 8, color=MUTED)
    s.save(path)


def layout_b(path, inv, accent=(0.2, 0.36, 0.28)):
    """Header row above a value row; no labels beside values."""
    s = Sheet()
    s.band(40, 92, accent)
    s.text(60, 66, inv["vendor"], 15, True, (1, 1, 1))
    s.text(60, 81, f"{inv['address'][0]}  |  EIN {inv['tax_id']}", 8.5, color=(0.88, 0.92, 0.89))
    s.right(552, 72, "Invoice", 18, True, (1, 1, 1))

    y = 122
    columns = [(60, "Invoice No."), (190, "PO No."), (310, "Date"), (440, "Due Date")]
    values = [inv.get("number", ""), inv.get("po", ""), inv["date"], inv["due"]]
    for (x, label), value in zip(columns, values):
        s.text(x, y, label, 8.5, True, MUTED)
        s.text(x, y + 14, value, 10)
    s.rule(y + 24)

    s.text(60, 170, "Customer", 8.5, True, MUTED)
    s.text(60, 184, "Halden Manufacturing Inc, 2200 Foundry Road, Dayton, OH 45402")
    s.text(60, 198, "Customer Account No: 88120", 9, color=MUTED)

    top = 236
    s.text(60, top, "Description", 8.5, True, MUTED)
    s.right(400, top, "Qty", 8.5, True, MUTED)
    s.right(480, top, "Rate", 8.5, True, MUTED)
    s.right(552, top, "Amount", 8.5, True, MUTED)
    s.rule(top + 6, 60, 552, accent, 1)
    y = top + 24
    for desc, qty, unit in inv["lines"]:
        s.text(60, y, desc)
        s.right(400, y, str(qty))
        s.right(480, y, money(unit))
        s.right(552, y, money(qty * unit))
        y += 20
    s.rule(y - 8, 60, 552)

    y += 18
    currency = inv.get("currency", "USD")
    s.text(380, y, "Net Amount")
    s.right(552, y, money(inv["net"]))
    y += 15
    s.text(380, y, "Tax")
    s.right(552, y, money(inv["tax"]))
    y += 18
    s.text(380, y, f"Amount Due ({currency})", 11, True)
    s.right(552, y, money(inv["net"] + inv["tax"]), 11, True)

    y = 650
    s.text(60, y, "Remit by wire", 9, True, accent)
    s.text(60, y + 13, inv["bank_name"])
    s.text(60, y + 25, f"Acct: {inv['account']}")
    s.save(path)


def layout_c(path, inv, accent=(0.45, 0.2, 0.12)):
    """Ship to names another vendor; Bill to prints the buyer's own tax ID."""
    s = Sheet()
    s.text(320, 58, inv["vendor"], 14, True, accent)
    y = 72
    for line in inv["address"]:
        s.text(320, y, line, 9, color=MUTED)
        y += 12
    s.text(320, y, f"VAT No: {inv['tax_id']}", 9, color=MUTED)

    s.text(48, 62, "INVOICE", 22, True)
    y = 84
    if inv.get("number"):
        s.text(48, y, f"Invoice #  {inv['number']}")
        y += 14
    s.text(48, y, f"Date of Issue:  {inv['date']}")
    y += 14
    if inv.get("po"):
        s.text(48, y, f"Purchase Order:  {inv['po']}")
        y += 14
    s.text(48, y, "Quote No:  Q-20817", color=MUTED)

    s.text(48, 146, "Bill to", 9, True, accent)
    s.text(48, 158, "Halden Manufacturing Inc")
    s.text(48, 170, "Tax ID: 91-3304418")
    s.text(320, 146, "Ship to", 9, True, accent)
    s.text(320, 158, inv["ship_to"])
    s.text(320, 170, "Dock 4, 2200 Foundry Road, Dayton, OH")

    top = 216
    s.rule(top - 12, color=INK, width=1)
    s.text(48, top, "Description", 9, True)
    s.right(420, top, "Quantity", 9, True)
    s.right(490, top, "Price", 9, True)
    s.right(564, top, "Amount", 9, True)
    s.rule(top + 6, color=INK, width=1)
    y = top + 22
    for desc, qty, unit in inv["lines"]:
        s.text(48, y, desc)
        s.right(420, y, str(qty))
        s.right(490, y, money(unit))
        s.right(564, y, money(qty * unit))
        y += 18

    y += 18
    if inv.get("tax") is not None:
        s.text(400, y, "Subtotal")
        s.right(564, y, money(inv["net"]))
        y += 15
        s.text(400, y, "Tax")
        s.right(564, y, money(inv["tax"]))
        y += 15
        total = inv["net"] + inv["tax"]
    else:
        total = inv["total"]
    s.text(400, y, "Invoice Total", 11, True)
    s.right(564, y, money(total), 11, True)
    y += 16
    s.text(400, y, "All amounts in USD", 8.5, color=MUTED)
    if inv.get("note"):
        y += 22
        s.text(48, y, inv["note"], 9.5)
    s.save(path)


def to_scan(source, target, dpi=110, angle=0.8):
    """Renders a text PDF to a slightly skewed, low-quality image PDF with no text layer."""
    src = pymupdf.open(source)
    tilted = pymupdf.open()
    page = tilted.new_page(width=612, height=792)
    page.draw_rect(page.rect, color=(0.97, 0.97, 0.95), fill=(0.97, 0.97, 0.95))
    page.show_pdf_page(pymupdf.Rect(8, 6, 604, 786), src, 0, rotate=angle)
    jpeg = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).tobytes("jpeg", jpg_quality=45)
    out = pymupdf.open()
    out_page = out.new_page(width=612, height=792)
    out_page.insert_image(out_page.rect, stream=jpeg)
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target)
    out.close()
    tilted.close()
    src.close()


NORTHWIND = dict(
    vendor="Northwind Industrial Supply LLC",
    address=["880 Commerce Parkway", "Toledo, OH 43604"],
    tax_id="84-2291047",
    bank_name="First Harbor Bank",
)
MERIDIAN = dict(vendor="Meridian Facilities Services Inc", address=["41 Union Street, Columbus, OH 43215"], tax_id="47-1185520", bank_name="Great Lakes Bank", account="7730182093")
APEX = dict(vendor="Apex Packaging Co", address=["1209 Mill Avenue, Akron, OH 44308"], tax_id="36-7740912", bank_name="Summit Commercial Bank", account="5512006158", )
BRIGHTLINE = dict(vendor="Brightline Staffing Group LLC", address=["77 Market Street, Cincinnati, OH 45202"], tax_id="73-4410268", bank_name="Ohio Valley Bank", account="6120095527")
HARBOR = dict(vendor="Harbor Office Supply Inc", address=["310 Harbor Drive", "Sandusky, OH 44870"], tax_id="27-5561390")
LAKESHORE = dict(vendor="Lakeshore Electrical Contractors Inc", address=["5 Pier Road", "Cleveland, OH 44114"], tax_id="61-2207745")
CEDAR = dict(
    vendor="Cedar Ridge Logistics LLC",
    address=["92 Depot Lane", "Fort Wayne, IN 46802"],
    tax_id="52-9038114",
    bank_name="Heartland Trust Bank",
    account="4409127702",
)


def build():
    # History: real runs of these become the earlier approvals the edge cases depend on.
    layout_b(HISTORY / "MF-1101.pdf", {**MERIDIAN, "number": "MF-1101", "po": "PO-4620", "date": "14 Sep 2026", "due": "14 Oct 2026",
             "lines": [("Facilities maintenance, Phase 1", 1, 18000.00)], "net": 18000.00, "tax": 1440.00})
    layout_b(HISTORY / "MF-1102.pdf", {**MERIDIAN, "number": "MF-1102", "po": "PO-4620", "date": "5 Oct 2026", "due": "4 Nov 2026",
             "lines": [("Facilities maintenance, Phase 2", 1, 14500.00)], "net": 14500.00, "tax": 1160.00})
    layout_b(HISTORY / "AP-7781.pdf", {**APEX, "number": "AP-7781", "po": "PO-4733", "date": "12 Oct 2026", "due": "11 Nov 2026",
             "lines": [("Corrugated shipper, 18x12x10", 2500, 3.70)], "net": 9250.00, "tax": 740.00})
    layout_b(HISTORY / "BK-300.pdf", {**BRIGHTLINE, "number": "BK-300", "po": "PO-4300", "date": "8 Oct 2026", "due": "7 Nov 2026",
             "lines": [("Temporary staffing, September", 1, 4200.00)], "net": 4200.00, "tax": 336.00})

    # S1: happy path. 65.00 of freight over PO, inside the 248.00 tolerance.
    layout_a(LIVE / "S1-happy.pdf", {**NORTHWIND, "number": "INV-00417", "date": "2026-10-26", "po": "PO-4501", "account": "000184414471",
             "lines": [("NW-220", "Bearing assembly, sealed", 40, 250.00), ("NW-310", "Drive coupling, 40mm", 12, 200.00), ("", "Freight", 1, 65.00)],
             "net": 12465.00, "tax": 997.20})
    # S2 (EC1): third invoice on a split PO. Fine alone, over the limit in total.
    layout_b(LIVE / "S2-split.pdf", {**MERIDIAN, "number": "MF-1103", "po": "PO-4620", "date": "26 Oct 2026", "due": "25 Nov 2026",
             "lines": [("Facilities maintenance, Phase 3", 1, 8100.00)], "net": 8100.00, "tax": 648.00})
    # S3 (EC2): a resend of AP-7781 under a new number, a week later.
    layout_b(LIVE / "S3-resend.pdf", {**APEX, "number": "AP7781-R1", "po": "PO-4733", "date": "19 Oct 2026", "due": "18 Nov 2026",
             "lines": [("Corrugated shipper, 18x12x10", 2500, 3.70)], "net": 9250.00, "tax": 740.00})
    # S4 (EC3): a low-quality scan with no text layer.
    clean = ROOT / "samples" / "_scan_source.pdf"
    layout_a(clean, {**CEDAR, "number": "CR-5520", "date": "2026-10-22", "po": "PO-4810",
             "lines": [("CR-LH", "Linehaul, Fort Wayne to Dayton", 4, 1250.00), ("CR-DR", "Drayage and handling", 1, 1200.00)],
             "net": 6200.00, "tax": 496.00})
    to_scan(clean, LIVE / "S4-scan.pdf")
    clean.unlink()
    # S5: a clean invoice that prints no invoice number.
    layout_c(LIVE / "S5-no-number.pdf", {**HARBOR, "date": "October 23, 2026", "po": "PO-4906", "ship_to": "Lakeshore Electrical Contractors Inc",
             "lines": [("Copier paper, case", 40, 42.50)], "net": 1700.00, "tax": 136.00})
    # S6: bank account on the invoice differs from the vendor master (4471).
    layout_a(LIVE / "S6-bank.pdf", {**NORTHWIND, "number": "INV-00433", "date": "2026-10-28", "po": "PO-4502", "account": "000299319931",
             "lines": [("NW-410", "Gearbox seal kit", 20, 300.00)], "net": 6000.00, "tax": 480.00})
    # S7: one bundled line, tax embedded in the total. 5,400.00 / 1.08 = 5,000.00 net.
    layout_c(LIVE / "S7-embedded.pdf", {**HARBOR, "number": "HOS-2291", "date": "October 20, 2026", "po": "PO-4905", "ship_to": "Lakeshore Electrical Contractors Inc",
             "lines": [("Office furniture, bundled supply and fit-out", 1, 5400.00)], "tax": None, "total": 5400.00, "note": "Price includes 8% tax."})
    # S8: no PO printed; exactly one open PO could fit, so it is suggested and never assumed.
    layout_c(LIVE / "S8-implied.pdf", {**LAKESHORE, "number": "LE-8810", "date": "October 27, 2026", "ship_to": "Harbor Office Supply Inc",
             "lines": [("Panel rewiring, Building 2", 1, 2800.00)], "net": 2800.00, "tax": 224.00})
    # S9: bills the whole PO, but receiving has only booked two of the three pallets.
    layout_a(LIVE / "S9-receipt.pdf", {**APEX, "number": "AP-7802", "date": "2026-10-29", "po": "PO-4970",
             "lines": [("AX-PW", "Stretch film pallet kit", 3, 1600.00)], "net": 4800.00, "tax": 384.00}, accent=(0.36, 0.2, 0.45))
    build_inbox()


INBOX = ROOT / "samples" / "inbox"
# One email per live sample. S6 comes from a look-alike domain, asking for payment to a new account.
EMAILS = [
    ("01", "S1-happy.pdf", "Northwind Billing", "billing@northwind-industrial.example", "Invoice INV-00417 for PO-4501", "Tue, 27 Oct 2026 09:12:00 -0400",
     "Hello,\n\nPlease find attached invoice INV-00417 for PO-4501, including freight.\n\nRegards,\nNorthwind Billing"),
    ("02", "S2-split.pdf", "Meridian AR", "ar@meridianfacilities.example", "MF-1103: Phase 3 maintenance", "Tue, 27 Oct 2026 10:03:00 -0400",
     "Hi,\n\nAttached is our invoice for phase 3 of the facilities maintenance work under PO-4620.\n\nThanks,\nMeridian Accounts Receivable"),
    ("03", "S3-resend.pdf", "Apex Accounts", "accounts@apexpackaging.example", "Invoice for PO-4733", "Tue, 27 Oct 2026 11:40:00 -0400",
     "Hello,\n\nPlease process the attached invoice for the corrugated shippers on PO-4733.\n\nApex Packaging"),
    ("04", "S4-scan.pdf", "Cedar Ridge Dispatch", "invoices@cedarridgelogistics.example", "Scanned invoice CR-5520", "Wed, 28 Oct 2026 08:15:00 -0400",
     "Scanned copy of invoice CR-5520 attached. Original in the post.\n\nCedar Ridge Logistics"),
    ("05", "S5-no-number.pdf", "Harbor Office Supply", "billing@harboroffice.example", "Copier paper invoice", "Wed, 28 Oct 2026 09:30:00 -0400",
     "Hi, invoice for the copier paper delivered last week is attached.\n\nHarbor Office Supply"),
    ("06", "S6-bank.pdf", "Northwind Accounts", "accounts@northwind-industrial-billing.example", "URGENT: updated bank details, invoice INV-00433",
     "Wed, 28 Oct 2026 16:58:00 -0400",
     "Dear customer,\n\nPlease note our bank details have changed. Kindly remit payment for the attached invoice to the new account shown on it. "
     "Our old account is no longer in use.\n\nThis is time sensitive.\n\nNorthwind Accounts"),
    ("07", "S7-embedded.pdf", "Harbor Office Supply", "billing@harboroffice.example", "Invoice HOS-2291, furniture fit-out", "Thu, 29 Oct 2026 10:20:00 -0400",
     "Hello, attached is invoice HOS-2291 for the bundled furniture supply and fit-out.\n\nHarbor Office Supply"),
    ("08", "S8-implied.pdf", "Lakeshore Electrical", "office@lakeshore-electric.example", "Invoice LE-8810", "Thu, 29 Oct 2026 13:05:00 -0400",
     "Invoice LE-8810 for the panel rewiring in Building 2 is attached.\n\nLakeshore Electrical Contractors"),
    ("09", "S9-receipt.pdf", "Apex Accounts", "accounts@apexpackaging.example", "Invoice AP-7802, PO-4970 full order", "Fri, 30 Oct 2026 09:45:00 -0400",
     "Hello,\n\nAttached is our invoice for the full order on PO-4970.\n\nApex Packaging"),
]


def build_inbox():
    from email.message import EmailMessage

    INBOX.mkdir(parents=True, exist_ok=True)
    for old in INBOX.glob("*.eml"):
        old.unlink()
    for number, pdf, name, address, subject, when, body in EMAILS:
        msg = EmailMessage()
        msg["From"] = f"{name} <{address}>"
        msg["To"] = "ap@halden-mfg.example"
        msg["Subject"] = subject
        msg["Date"] = when
        msg.set_content(body)
        msg.add_attachment((LIVE / pdf).read_bytes(), maintype="application", subtype="pdf", filename=pdf)
        (INBOX / f"{number}-{pdf.split('-')[0]}.eml").write_bytes(bytes(msg))


if __name__ == "__main__":
    build()
    print("samples written:", len(list(HISTORY.glob("*.pdf"))) + len(list(LIVE.glob("*.pdf"))))
