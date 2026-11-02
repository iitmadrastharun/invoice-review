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
    """Indian digit grouping, as printed on Indian invoices: 12,46,500.00."""
    whole, paise = f"{v:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    while len(head) > 2:
        tail, head = f"{head[-2:]},{tail}", head[:-2]
    return f"{head + ',' if head else ''}{tail}.{paise}"


BUYER = "Vardhan Auto Components Pvt Ltd"
BUYER_ADDRESS = "Plot 14, MIDC Bhosari, Pune 411026"
BUYER_GSTIN = "27AAFCV4421K1Z8"


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
    s.text(48, y, f"GSTIN: {inv['tax_id']}", 9, color=MUTED)

    s.right(564, 62, "INVOICE", 20, True, accent)
    labels = [("Invoice No:", inv.get("number")), ("Invoice Date:", inv["date"]), ("PO Number:", inv.get("po")), ("Terms:", "30 days")]
    y = 84
    for label, value in labels:
        if value:
            s.text(392, y, label, 9.5, color=MUTED)
            s.text(472, y, value, 9.5, True)
            y += 14

    s.text(48, 158, "Bill To", 9, True, accent)
    s.text(48, 171, BUYER)
    s.text(48, 183, BUYER_ADDRESS)

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
    cur = "INR "
    s.text(392, y, "Subtotal")
    s.right(558, y, f"{cur}{money(inv['net'])}")
    y += 15
    s.text(392, y, "IGST (18%)")
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
    s.text(48, y + 37, f"IFSC: {inv['ifsc']}")
    s.rule(740)
    s.text(48, 754, "Thank you for your business. Questions about this invoice: billing@example.com", 8, color=MUTED)
    s.save(path)


def layout_b(path, inv, accent=(0.2, 0.36, 0.28)):
    """Header row above a value row; no labels beside values."""
    s = Sheet()
    s.band(40, 92, accent)
    s.text(60, 66, inv["vendor"], 15, True, (1, 1, 1))
    s.text(60, 81, f"{inv['address'][0]}  |  GSTIN {inv['tax_id']}", 8.5, color=(0.88, 0.92, 0.89))
    s.right(552, 72, "Invoice", 18, True, (1, 1, 1))

    y = 122
    columns = [(60, "Invoice No."), (190, "PO No."), (310, "Date"), (440, "Due Date")]
    values = [inv.get("number", ""), inv.get("po", ""), inv["date"], inv["due"]]
    for (x, label), value in zip(columns, values):
        s.text(x, y, label, 8.5, True, MUTED)
        s.text(x, y + 14, value, 10)
    s.rule(y + 24)

    s.text(60, 170, "Customer", 8.5, True, MUTED)
    s.text(60, 184, f"{BUYER}, {BUYER_ADDRESS}")
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
    currency = "INR"
    s.text(380, y, "Net Amount")
    s.right(552, y, money(inv["net"]))
    y += 15
    s.text(380, y, "GST (18%)")
    s.right(552, y, money(inv["tax"]))
    y += 18
    s.text(380, y, f"Amount Due ({currency})", 11, True)
    s.right(552, y, money(inv["net"] + inv["tax"]), 11, True)

    y = 650
    s.text(60, y, "Pay by NEFT / RTGS", 9, True, accent)
    s.text(60, y + 13, inv["bank_name"])
    s.text(60, y + 25, f"Acct: {inv['account']}   IFSC: {inv['ifsc']}")
    s.save(path)


def layout_c(path, inv, accent=(0.45, 0.2, 0.12)):
    """Ship to names another vendor; Bill to prints the buyer's own tax ID."""
    s = Sheet()
    s.text(320, 58, inv["vendor"], 14, True, accent)
    y = 72
    for line in inv["address"]:
        s.text(320, y, line, 9, color=MUTED)
        y += 12
    s.text(320, y, f"GSTIN: {inv['tax_id']}", 9, color=MUTED)

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
    s.text(48, 158, BUYER)
    s.text(48, 170, f"GSTIN: {BUYER_GSTIN}")
    s.text(320, 146, "Ship to", 9, True, accent)
    s.text(320, 158, inv["ship_to"])
    s.text(320, 170, "Stores Gate 4, MIDC Bhosari, Pune")

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
        s.text(400, y, "IGST (18%)")
        s.right(564, y, money(inv["tax"]))
        y += 15
        total = inv["net"] + inv["tax"]
    else:
        total = inv["total"]
    s.text(400, y, "Invoice Total", 11, True)
    s.right(564, y, money(total), 11, True)
    y += 16
    s.text(400, y, "All amounts in INR", 8.5, color=MUTED)
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


KAVERI = dict(
    vendor="Kaveri Industrial Supplies Pvt Ltd",
    address=["42 SIDCO Industrial Estate, Guindy", "Chennai 600032, Tamil Nadu"],
    tax_id="33AABCK4471M1Z2",
    bank_name="Coromandel Co-operative Bank",
    ifsc="CCBL0000412",
)
MEENAKSHI = dict(vendor="Meenakshi Facility Services Pvt Ltd", address=["18 Bypass Road, Madurai 625010"], tax_id="33AACCM2093F1Z6",
                 bank_name="Vaigai Co-operative Bank", account="50200077302093", ifsc="VCBL0000207")
ASHOKA = dict(vendor="Ashoka Packaging Industries", address=["Gat 211, Chakan MIDC", "Pune 410501, Maharashtra"], tax_id="27AAJFA6158P1Z4",
              bank_name="Deccan Urban Co-operative Bank", account="10045512006158", ifsc="DUCB0000118")
PRABHAT = dict(vendor="Prabhat Manpower Services Pvt Ltd", address=["5th Cross, Indiranagar, Bengaluru 560038"], tax_id="29AAGCP5527H1Z3",
               bank_name="Kaveri Grameena Bank", account="61200095527", ifsc="KGBL0000331")
SAHYADRI = dict(vendor="Sahyadri Office Solutions Pvt Ltd", address=["Office 7, Baner Road", "Pune 411045, Maharashtra"], tax_id="27AAHCS3340Q1Z1")
NARMADA = dict(vendor="Narmada Electricals Pvt Ltd", address=["B-12 Naroda GIDC", "Ahmedabad 382330, Gujarat"], tax_id="24AADCN8816E1Z7")
GODAVARI = dict(
    vendor="Godavari Logistics LLP",
    address=["Plot 9, Autonagar", "Hyderabad 500070, Telangana"],
    tax_id="36AALFG7702L1Z9",
    bank_name="Telangana State Co-operative Bank",
    account="44091277702",
    ifsc="TSCB0000521",
)


def build():
    # History: real runs of these become the earlier approvals the edge cases depend on.
    layout_b(HISTORY / "MFS-1101.pdf", {**MEENAKSHI, "number": "MFS/26-27/1101", "po": "PO-4620", "date": "14 Sep 2026", "due": "14 Oct 2026",
             "lines": [("Facility maintenance, Phase 1", 1, 1800000.00)], "net": 1800000.00, "tax": 324000.00})
    layout_b(HISTORY / "MFS-1102.pdf", {**MEENAKSHI, "number": "MFS/26-27/1102", "po": "PO-4620", "date": "5 Oct 2026", "due": "4 Nov 2026",
             "lines": [("Facility maintenance, Phase 2", 1, 1450000.00)], "net": 1450000.00, "tax": 261000.00})
    layout_b(HISTORY / "API-7781.pdf", {**ASHOKA, "number": "API/26-27/7781", "po": "PO-4733", "date": "12 Oct 2026", "due": "11 Nov 2026",
             "lines": [("Corrugated box, 5-ply, 18x12x10 in", 2500, 370.00)], "net": 925000.00, "tax": 166500.00})
    layout_b(HISTORY / "PMS-0300.pdf", {**PRABHAT, "number": "PMS/26-27/0300", "po": "PO-4300", "date": "8 Oct 2026", "due": "7 Nov 2026",
             "lines": [("Contract staff, September", 1, 420000.00)], "net": 420000.00, "tax": 75600.00})

    # S1: happy path. 6,500 of freight over the PO, inside the 24,800 tolerance (2%, capped at 25,000).
    layout_a(LIVE / "S1-happy.pdf", {**KAVERI, "number": "KIS/26-27/0417", "date": "26/10/2026", "po": "PO-4501", "account": "00184014414471",
             "lines": [("KI-220", "Bearing assembly, sealed", 40, 25000.00), ("KI-310", "Drive coupling, 40mm", 12, 20000.00), ("", "Freight", 1, 6500.00)],
             "net": 1246500.00, "tax": 224370.00})
    # S2 (EC1): third invoice on a split PO. Fine alone, over the limit in total.
    layout_b(LIVE / "S2-split.pdf", {**MEENAKSHI, "number": "MFS/26-27/1103", "po": "PO-4620", "date": "26 Oct 2026", "due": "25 Nov 2026",
             "lines": [("Facility maintenance, Phase 3", 1, 810000.00)], "net": 810000.00, "tax": 145800.00})
    # S3 (EC2): a resend of API/26-27/7781 under a new number, a week later.
    layout_b(LIVE / "S3-resend.pdf", {**ASHOKA, "number": "API/26-27/7781-R", "po": "PO-4733", "date": "19 Oct 2026", "due": "18 Nov 2026",
             "lines": [("Corrugated box, 5-ply, 18x12x10 in", 2500, 370.00)], "net": 925000.00, "tax": 166500.00})
    # S4 (EC3): a low-quality scan with no text layer.
    clean = ROOT / "samples" / "_scan_source.pdf"
    layout_a(clean, {**GODAVARI, "number": "GL/26-27/5520", "date": "22/10/2026", "po": "PO-4810",
             "lines": [("GL-LH", "Linehaul, Hyderabad to Pune", 4, 125000.00), ("GL-HD", "Loading and handling", 1, 120000.00)],
             "net": 620000.00, "tax": 111600.00})
    to_scan(clean, LIVE / "S4-scan.pdf")
    clean.unlink()
    # S5: a clean invoice that prints no invoice number.
    layout_c(LIVE / "S5-no-number.pdf", {**SAHYADRI, "date": "23 October 2026", "po": "PO-4906", "ship_to": "Narmada Electricals Pvt Ltd",
             "lines": [("Copier paper A4, box of 5 reams", 40, 4250.00)], "net": 170000.00, "tax": 30600.00})
    # S6: bank account on the invoice differs from the vendor master (4471).
    layout_a(LIVE / "S6-bank.pdf", {**KAVERI, "number": "KIS/26-27/0433", "date": "28/10/2026", "po": "PO-4502", "account": "00299019319931",
             "lines": [("KI-410", "Gearbox seal kit", 20, 30000.00)], "net": 600000.00, "tax": 108000.00})
    # S7: one bundled line, GST inside the total. 5,90,000 / 1.18 = 5,00,000 net.
    layout_c(LIVE / "S7-embedded.pdf", {**SAHYADRI, "number": "SOS/26-27/2291", "date": "20 October 2026", "po": "PO-4905", "ship_to": "Narmada Electricals Pvt Ltd",
             "lines": [("Office furniture, supply and fit-out", 1, 590000.00)], "tax": None, "total": 590000.00, "note": "Price includes 18% GST."})
    # S8: no PO printed; exactly one open PO could fit, so it is suggested and never assumed.
    layout_c(LIVE / "S8-implied.pdf", {**NARMADA, "number": "NE/26-27/8810", "date": "27 October 2026", "ship_to": "Sahyadri Office Solutions Pvt Ltd",
             "lines": [("Panel rewiring, Plant 2", 1, 280000.00)], "net": 280000.00, "tax": 50400.00})
    # S9: bills the whole PO, but stores has only received two of the three pallets.
    layout_a(LIVE / "S9-receipt.pdf", {**ASHOKA, "number": "API/26-27/7802", "date": "29-Oct-2026", "po": "PO-4970", "account": "10045512006158",
             "lines": [("AP-SW", "Stretch film pallet kit", 3, 160000.00)], "net": 480000.00, "tax": 86400.00}, accent=(0.36, 0.2, 0.45))
    build_inbox()

INBOX = ROOT / "samples" / "inbox"
# One email per live sample. S6 comes from a look-alike domain, asking for payment to a new account.
EMAILS = [
    ("01", "S1-happy.pdf", "Kaveri Billing", "billing@kaveri-industrial.example", "Invoice KIS/26-27/0417 for PO-4501", "Tue, 27 Oct 2026 09:12:00 +0530",
     "Hello,\n\nPlease find attached invoice KIS/26-27/0417 for PO-4501, including freight.\n\nRegards,\nKaveri Billing"),
    ("02", "S2-split.pdf", "Meenakshi Accounts", "accounts@meenakshifacility.example", "MFS/26-27/1103: Phase 3 maintenance", "Tue, 27 Oct 2026 10:03:00 +0530",
     "Hi,\n\nAttached is our invoice for phase 3 of the facility maintenance work under PO-4620.\n\nThanks,\nMeenakshi Facility Services"),
    ("03", "S3-resend.pdf", "Ashoka Accounts", "accounts@ashokapackaging.example", "Invoice for PO-4733", "Tue, 27 Oct 2026 11:40:00 +0530",
     "Hello,\n\nPlease process the attached invoice for the corrugated boxes on PO-4733.\n\nAshoka Packaging"),
    ("04", "S4-scan.pdf", "Godavari Dispatch", "invoices@godavarilogistics.example", "Scanned invoice GL/26-27/5520", "Wed, 28 Oct 2026 08:15:00 +0530",
     "Scanned copy of invoice GL/26-27/5520 attached. Original sent by courier.\n\nGodavari Logistics"),
    ("05", "S5-no-number.pdf", "Sahyadri Office Solutions", "billing@sahyadrioffice.example", "Copier paper invoice", "Wed, 28 Oct 2026 09:30:00 +0530",
     "Hi, invoice for the copier paper delivered last week is attached.\n\nSahyadri Office Solutions"),
    ("06", "S6-bank.pdf", "Kaveri Accounts", "accounts@kaveri-industrial-billing.example", "URGENT: updated bank details, invoice KIS/26-27/0433",
     "Wed, 28 Oct 2026 16:58:00 +0530",
     "Dear Sir/Madam,\n\nPlease note our bank details have changed. Kindly release payment for the attached invoice to the new account shown on it. "
     "Our old account is no longer in use.\n\nThis is urgent.\n\nKaveri Accounts"),
    ("07", "S7-embedded.pdf", "Sahyadri Office Solutions", "billing@sahyadrioffice.example", "Invoice SOS/26-27/2291, furniture fit-out", "Thu, 29 Oct 2026 10:20:00 +0530",
     "Hello, attached is invoice SOS/26-27/2291 for the office furniture supply and fit-out.\n\nSahyadri Office Solutions"),
    ("08", "S8-implied.pdf", "Narmada Electricals", "office@narmada-electricals.example", "Invoice NE/26-27/8810", "Thu, 29 Oct 2026 13:05:00 +0530",
     "Invoice NE/26-27/8810 for the panel rewiring in Plant 2 is attached.\n\nNarmada Electricals"),
    ("09", "S9-receipt.pdf", "Ashoka Accounts", "accounts@ashokapackaging.example", "Invoice API/26-27/7802, PO-4970 full order", "Fri, 30 Oct 2026 09:45:00 +0530",
     "Hello,\n\nAttached is our invoice for the full order on PO-4970.\n\nAshoka Packaging"),
]


def build_inbox():
    from email.message import EmailMessage

    INBOX.mkdir(parents=True, exist_ok=True)
    for old in INBOX.glob("*.eml"):
        old.unlink()
    for number, pdf, name, address, subject, when, body in EMAILS:
        msg = EmailMessage()
        msg["From"] = f"{name} <{address}>"
        msg["To"] = "ap@vardhan-auto.example"
        msg["Subject"] = subject
        msg["Date"] = when
        msg.set_content(body)
        msg.add_attachment((LIVE / pdf).read_bytes(), maintype="application", subtype="pdf", filename=pdf)
        (INBOX / f"{number}-{pdf.split('-')[0]}.eml").write_bytes(bytes(msg))


if __name__ == "__main__":
    build()
    print("samples written:", len(list(HISTORY.glob("*.pdf"))) + len(list(LIVE.glob("*.pdf"))))
