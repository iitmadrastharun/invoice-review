"""Reads invoice fields from a PDF's text layer using label and position rules.

Each field ends FOUND, MISSING or CONFLICT. The label lists are finite on purpose:
an unfamiliar label becomes a hold, never a guess.
"""
import re
from datetime import date, datetime

import pymupdf

CHUNK_GAP = 12  # points between words before they count as separate values
LINE_TOLERANCE = 3
BELOW_MAX_GAP = 24

LABELS = {
    "invoice_number": ["invoice no", "invoice number", "invoice #", "inv no", "inv #"],
    "invoice_date": ["invoice date", "date of issue"],
    "po_number": ["po no", "po #", "po number", "purchase order no", "purchase order", "customer po", "po"],
    "subtotal": ["subtotal", "net amount", "net total"],
    "tax": ["sales tax", "total tax", "igst", "gst", "tax", "vat"],
    "total": ["total due", "amount due", "balance due", "invoice total", "grand total", "total"],
}
BARE_DATE = ["date"]
NEVER = ["due date", "po date", "order date", "ship date", "delivery date", "purchase order date", "tax id", "vat no",
         "tax invoice", "gstin", "gst no", "po box", "order no", "customer no", "quote no", "account no", "total qty"]
TABLE_WORDS = {"description", "item", "qty", "quantity", "unit", "price", "rate", "amount", "total"}
BLOCK_LABELS = ["bill to", "ship to", "sold to", "deliver to", "attention", "customer"]
TAX_ID_LABELS = ["gstin", "gst no", "tax id", "ein", "tin", "vat no"]
# Western (1,234,567) or Indian (12,34,567) digit grouping.
AMOUNT_RE = re.compile(r"^(\d{1,3}(,\d{3})+|\d{1,2}(,\d{2})+,\d{3}|\d+)(\.\d{2})?$")
INCLUDED_RE = re.compile(r"\b(?:price|prices|total|amount|amounts)\s+(?:includes?|including|inclusive\s+of)\s+(\d+(?:\.\d+)?)\s*%\s*(?:sales\s+)?(?:tax|vat|gst)\b", re.I)
BANK_TRIGGER_RE = re.compile(r"\b(bank|iban|swift|bic|routing|ach|wire|ifsc|neft|rtgs)\b", re.I)
ACCOUNT_RE = re.compile(r"(\w+\s+)?\b(?:account\s*(?:no\.?|number|#)|acct\.?(?:\s*(?:no\.?|#))?|iban)\s*:?\s*([A-Za-z0-9*\-]+(?:\s\d{2,})*)", re.I)
NOT_BANK_PREFIX = {"customer", "client", "your"}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def tax_key(tax_id):
    """GSTINs mix letters and digits, so compare letters and digits, ignoring spacing and dashes."""
    return re.sub(r"[^0-9A-Z]", "", str(tax_id).upper())


def _norm(word):
    return re.sub(r"[.:\-,()]", "", word.lower())


def _concat(label):
    return "".join(_norm(p) for p in label.split())


class Line:
    def __init__(self, page, words):
        self.page = page
        self.words = sorted(words, key=lambda w: w[0])
        self.y = sum((w[1] + w[3]) / 2 for w in words) / len(words)
        self.norm = [_norm(w[4]) for w in self.words]
        self.text = " ".join(w[4] for w in self.words)


def _lines(page, index):
    lines = []
    for word in sorted(page.get_text("words"), key=lambda w: (w[1] + w[3]) / 2):
        centre = (word[1] + word[3]) / 2
        if lines and abs(lines[-1]["y"] - centre) <= LINE_TOLERANCE:
            lines[-1]["words"].append(word)
        else:
            lines.append({"y": centre, "words": [word]})
    return [Line(index, l["words"]) for l in lines]


def _spans(line, labels):
    """Finds label occurrences on a line, longest labels first, without overlaps."""
    found, used = [], set()
    for label in sorted(labels, key=lambda l: -len(_concat(l))):
        target = _concat(label)
        for start in range(len(line.norm)):
            built, end = "", start
            while end < len(line.norm) and len(built) < len(target):
                built += line.norm[end]
                end += 1
            if built == target and not used.intersection(range(start, end)):
                found.append((label, start, end))
                used.update(range(start, end))
    return sorted(found, key=lambda s: s[1])


ALL_LABELS = [l for group in LABELS.values() for l in group] + BARE_DATE + NEVER


def _label_starts(line):
    return {start for _, start, _ in _spans(line, ALL_LABELS)}


def _chunks(words):
    chunks = []
    for word in words:
        if chunks and word[0] - chunks[-1][-1][2] <= CHUNK_GAP:
            chunks[-1].append(word)
        else:
            chunks.append([word])
    return chunks


def _rect(words):
    return [min(w[0] for w in words), min(w[1] for w in words), max(w[2] for w in words), max(w[3] for w in words)]


def _value_words(lines, i, span, amount=False):
    """Where a label's value is: right of it on the line, otherwise under it."""
    line = lines[i]
    _, _, end = span
    starts = _label_starts(line)
    right = []
    for k in range(end, len(line.words)):
        if k in starts:
            break
        right.append(line.words[k])
    if amount:
        tokens = [w for w in right if re.search(r"\d", w[4]) and "%" not in w[4]]
        if tokens:
            return [tokens[-1]]
    elif right:
        return _chunks(right)[0]

    # Under the label: the chunk on the next line that overlaps the label's own x-span.
    if i + 1 >= len(lines) or lines[i + 1].page != line.page or lines[i + 1].y - line.y > BELOW_MAX_GAP:
        return None
    below = lines[i + 1]
    x0, x1 = line.words[span[1]][0], line.words[end - 1][2]
    starts = _label_starts(below)
    index = {id(w): k for k, w in enumerate(below.words)}
    best, overlap = None, 0
    for chunk in _chunks(below.words):
        size = min(x1, chunk[-1][2]) - max(x0, chunk[0][0])
        if size > overlap and index[id(chunk[0])] not in starts:
            best, overlap = chunk, size
    if best and amount:
        tokens = [w for w in best if re.search(r"\d", w[4]) and "%" not in w[4]]
        return [tokens[-1]] if tokens else None
    return best


def parse_amount(printed):
    """Returns a plain string such as 12465.00, or None if the format is not safely readable."""
    text = re.sub(r"(USD|EUR|GBP|CAD|AUD|INR|Rs\.?|CA\$|A\$|\$|€|£|₹)", "", printed).strip()
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")")) or text.endswith("-")
    text = text.strip("()-").strip()
    if not AMOUNT_RE.match(text):
        return None
    value = f"{float(text.replace(',', '')):.2f}"
    return f"-{value}" if negative else value


def parse_date(printed, date_format=""):
    """Returns (list of possible ISO dates). One entry means one meaning."""
    text = printed.strip().rstrip(".,")
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return _dates([(int(match[1]), int(match[2]), int(match[3]))])
    match = re.fullmatch(r"(\d{1,2})[ \-]([A-Za-z]{3,9})\.?,?[ \-](\d{4})", text)
    if match and match[2][:3].lower() in MONTHS:
        return _dates([(int(match[3]), MONTHS[match[2][:3].lower()], int(match[1]))])
    match = re.fullmatch(r"([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})", text)
    if match and match[1][:3].lower() in MONTHS:
        return _dates([(int(match[3]), MONTHS[match[1][:3].lower()], int(match[2]))])
    match = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", text)
    if match:
        a, b, year = int(match[1]), int(match[2]), int(match[3])
        if date_format == "MDY":
            return _dates([(year, a, b)])
        if date_format == "DMY":
            return _dates([(year, b, a)])
        return _dates([(year, a, b), (year, b, a)])
    return []


def _dates(parts):
    out = []
    for year, month, day in parts:
        try:
            iso = date(year, month, day).isoformat()
        except ValueError:
            continue
        if iso not in out:
            out.append(iso)
    return out


def _field(state, value=None, **extra):
    return {"state": state, "value": value, **extra}


def _settle(candidates):
    """candidates: list of dict(value, printed, page, rect, label). Turns them into one field."""
    if not candidates:
        return _field("MISSING")
    bad = [c for c in candidates if c["value"] is None]
    distinct = list(dict.fromkeys(c["value"] for c in candidates if c["value"] is not None))
    if bad or len(distinct) > 1:
        readings = distinct + [c["printed"] for c in bad]
        first = bad[0] if bad else candidates[0]
        return _field("CONFLICT", None, readings=readings, printed=first["printed"], page=first["page"], rect=first["rect"], label=first["label"])
    c = next(c for c in candidates if c["value"] is not None)
    return _field("FOUND", c["value"], printed=c["printed"], page=c["page"], rect=c["rect"], label=c["label"])


def _table_lines(lines):
    """Lines inside the line-item table never supply field values."""
    inside, active = set(), False
    total_block = LABELS["subtotal"] + LABELS["total"] + LABELS["tax"]
    for k, line in enumerate(lines):
        if active and _spans(line, total_block):
            active = False
        if not active and len(TABLE_WORDS.intersection(line.norm)) >= 3:
            active = True
        if active:
            inside.add(k)
    return inside


def _collect(lines, inside, labels, make_value, amount=False, skip_line=None):
    out = []
    for i, line in enumerate(lines):
        if i in inside or (skip_line and skip_line(line)):
            continue
        spans = _spans(line, labels + NEVER)
        for span in spans:
            if span[0] not in labels:
                continue
            words = _value_words(lines, i, span, amount)
            if not words:
                continue
            printed = " ".join(w[4] for w in words)
            value = make_value(printed)
            if value is _SKIP:
                continue
            out.append({"value": value, "printed": printed, "page": line.page, "rect": _rect(words), "label": span[0]})
    return out


_SKIP = object()


def _identifier(printed):
    token = printed.split()[0].rstrip(",;")
    return token.upper() if re.search(r"\d", token) else _SKIP


def _po_value(printed):
    tokens = re.findall(r"[A-Za-z]{0,4}[\-/]?\d{3,}", printed)
    if not tokens:
        return _SKIP
    distinct = list(dict.fromkeys(t.upper() for t in tokens))
    return distinct[0] if len(distinct) == 1 else None


def _blocks(lines):
    """Rectangles under Bill to, Ship to and the like; they describe someone other than the issuer."""
    out = []
    for line in lines:
        if line.page != 0:
            continue
        for _, start, _ in _spans(line, BLOCK_LABELS):
            word = line.words[start]
            out.append((word[0] - 5, line.y - 3, word[0] + 235, line.y + 60))
    return out


def _in_block(word, blocks):
    centre = (word[1] + word[3]) / 2
    return any(x0 <= word[0] <= x1 and y0 <= centre <= y1 for x0, y0, x1, y1 in blocks)


def _match_vendor(lines, page_height, vendors, policy):
    blocks = _blocks(lines)
    zone = []
    for line in lines:
        if line.page == 0 and line.y <= page_height * 0.25:
            kept = [w for w in line.words if not _in_block(w, blocks)]
            for chunk in _chunks(kept):
                zone.append(" ".join(w[4] for w in chunk))
    zone_text = " | ".join(zone).lower()

    by_name = []
    for vendor in vendors.values():
        names = [vendor["legal_name"]] + [a for a in vendor["aliases"].split(";") if a]
        if any(re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", zone_text) for n in names):
            by_name.append(vendor["vendor_id"])

    tax_ids, tax_rect = [], None
    for i, line in enumerate(lines):
        for span in _spans(line, TAX_ID_LABELS):
            if _in_block(line.words[span[1]], blocks):
                continue
            words = _value_words(lines, i, span)
            if not words:
                continue
            printed = words[0][4].strip(",;")
            digits = tax_key(printed)
            if not digits or digits == tax_key(policy["buyer_tax_id"]):
                continue
            if digits not in tax_ids:
                tax_ids.append(digits)
                tax_rect = (line.page, _rect(words))
    by_tax = [v["vendor_id"] for v in vendors.values() if tax_key(v["tax_id"]) in tax_ids]

    guess = zone[0] if zone else None
    if len(by_name) > 1:
        return _field("CONFLICT", readings=by_name, printed=guess, reason="More than one vendor in the master is named at the top of this invoice.")
    if tax_ids and by_name and by_tax != by_name:
        return _field("CONFLICT", readings=list(dict.fromkeys(by_name + by_tax)), printed=guess, reason="The tax ID and the company name point to different vendors.")
    match = by_tax[0] if by_tax else (by_name[0] if by_name else None)
    if match is None:
        return _field("MISSING", printed=guess)
    how = "tax ID" if by_tax else "exact name"
    page, rect = tax_rect if by_tax and tax_rect else (0, None)
    return _field("FOUND", match, printed=vendors[match]["legal_name"], page=page, rect=rect, label=how)


def _currency(words):
    seen = {}
    for word in words:
        text = word[4]
        code = None
        if re.search(r"\b(USD|EUR|GBP|CAD|AUD|INR)\b", text):
            code = re.search(r"\b(USD|EUR|GBP|CAD|AUD|INR)\b", text)[1]
        elif "CA$" in text:
            code = "CAD"
        elif "A$" in text:
            code = "AUD"
        elif "$" in text:
            code = "USD"
        elif "€" in text:
            code = "EUR"
        elif "£" in text:
            code = "GBP"
        elif "₹" in text or re.fullmatch(r"Rs\.?", text):
            code = "INR"
        if code:
            seen.setdefault(code, (word[-1], word))
    if not seen:
        return _field("MISSING")
    if len(seen) > 1:
        return _field("CONFLICT", readings=list(seen), printed=", ".join(seen))
    code = next(iter(seen))
    return _field("FOUND", code, printed=code, label="currency mark")


def _bank(lines, vendor_name):
    trigger_lines = []
    for k, line in enumerate(lines):
        text = line.text
        if vendor_name:
            text = re.sub(re.escape(vendor_name), "", text, flags=re.I)
        if BANK_TRIGGER_RE.search(text):
            trigger_lines.append(k)
    if not trigger_lines:
        return _field("FOUND", "NONE", printed="No bank details printed", label="no bank words on the page")
    found = []
    for k, line in enumerate(lines):
        if not any(t <= k <= t + 4 for t in trigger_lines):
            continue
        for match in ACCOUNT_RE.finditer(line.text):
            before = (match[1] or "").strip().lower()
            if before in NOT_BANK_PREFIX:
                continue
            digits = re.sub(r"\D", "", match[2])
            if len(digits) >= 4:
                found.append((digits[-4:], match[2], line.page))
    distinct = list(dict.fromkeys(f[0] for f in found))
    if not distinct:
        return _field("MISSING", reason="Bank words are printed, but no account number could be read.")
    if len(distinct) > 1:
        return _field("CONFLICT", readings=distinct, printed=", ".join(distinct))
    return _field("FOUND", distinct[0], printed=found[0][1], page=found[0][2], label="account number")


def _doc_type(lines, page_height):
    top = " ".join(l.text for l in lines if l.page == 0 and l.y <= page_height * 0.25).lower()
    for phrase, kind in [("credit note", "credit_note"), ("credit memo", "credit_note"), ("statement", "statement"), ("proforma", "proforma"),
                         ("pro forma", "proforma"), ("quotation", "quotation"), ("reminder", "reminder")]:
        if phrase in top:
            return _field("FOUND", kind, printed=phrase, label="title")
    if any("invoice" in l.norm or "invoice" in " ".join(l.norm) for l in lines):
        return _field("FOUND", "invoice", printed="invoice", label="title")
    return _field("MISSING", reason="The word invoice does not appear on this document.")


def extract(path, vendors, policy):
    """Returns the fields of a text-layer invoice, each with a state."""
    with pymupdf.open(path) as doc:
        lines, words = [], []
        for index, page in enumerate(doc):
            lines.extend(_lines(page, index))
            words.extend(tuple(w) for w in page.get_text("words"))
        height = doc[0].rect.height
    inside = _table_lines(lines)
    full_text = "\n".join(l.text for l in lines)

    fields = {}
    fields["vendor"] = _match_vendor(lines, height, vendors, policy)
    vendor = vendors.get(fields["vendor"].get("value") or "")
    date_format = vendor["date_format"] if vendor else ""

    fields["invoice_number"] = _settle(_collect(lines, inside, LABELS["invoice_number"], _identifier))

    def make_date(printed):
        meanings = parse_date(printed, date_format)
        return meanings[0] if len(meanings) == 1 else None

    date_candidates = _collect(lines, inside, LABELS["invoice_date"], make_date)
    if not date_candidates:
        date_candidates = _collect(lines, inside, BARE_DATE, make_date)
    fields["invoice_date"] = _settle(date_candidates)
    if fields["invoice_date"]["state"] == "CONFLICT":
        readings = []
        for c in date_candidates:
            readings.extend(parse_date(c["printed"], date_format))
        fields["invoice_date"]["readings"] = list(dict.fromkeys(readings)) or fields["invoice_date"]["readings"]

    fields["po_number"] = _settle(_collect(lines, inside, LABELS["po_number"], _po_value))

    fields["subtotal"] = _settle(_collect(lines, inside, LABELS["subtotal"], parse_amount, amount=True))
    fields["tax"] = _settle(_collect(lines, inside, LABELS["tax"], parse_amount, amount=True, skip_line=lambda l: INCLUDED_RE.search(l.text)))
    fields["total"] = _settle(_collect(lines, inside, LABELS["total"], parse_amount, amount=True))

    included = INCLUDED_RE.search(full_text)
    if included:
        fields["tax_included_rate"] = _field("FOUND", f"{float(included[1]) / 100:.4f}", printed=included[0], label="tax wording")
    else:
        fields["tax_included_rate"] = _field("MISSING")

    fields["currency"] = _currency(words)
    fields["bank_last4"] = _bank(lines, vendor["legal_name"] if vendor else None)
    fields["document_type"] = _doc_type(lines, height)
    return fields


def page_has_text(path, needle):
    """True if the value occurs in the document's text layer."""
    with pymupdf.open(path) as doc:
        return any(page.search_for(needle) for page in doc)


def today():
    return datetime.now().date()
