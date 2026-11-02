"""Decides how a PDF can be read: text layer, scan, or unreadable. A plain rule, no AI."""
import hashlib
import string

import pymupdf

MIN_CHARS_PER_PAGE = 50
MIN_PRINTABLE = 0.85
IMAGE_COVERS_PAGE = 0.80
MAX_PAGES = 20

PRINTABLE = set(string.printable) | set("€£₹–—’‘“”•")


def sha256_of(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def route(path):
    """Returns {route: text|scan|unreadable, pages, reason}."""
    try:
        doc = pymupdf.open(path)
    except Exception:
        return {"route": "unreadable", "pages": 0, "reason": "This file cannot be opened as a PDF."}
    with doc:
        if doc.needs_pass:
            return {"route": "unreadable", "pages": doc.page_count, "reason": "This PDF is password protected."}
        pages = doc.page_count
        if pages == 0:
            return {"route": "unreadable", "pages": 0, "reason": "This PDF has no pages."}
        if pages > MAX_PAGES:
            return {"route": "unreadable", "pages": pages, "reason": f"This PDF has {pages} pages; the limit is {MAX_PAGES}."}

        chars = printable = 0
        has_image = covering_image = False
        for page in doc:
            text = "".join(page.get_text().split())
            chars += len(text)
            printable += sum(c in PRINTABLE for c in text)
            area = abs(page.rect)
            for info in page.get_image_info():
                has_image = True
                if area and abs(pymupdf.Rect(info["bbox"]) & page.rect) / area > IMAGE_COVERS_PAGE:
                    covering_image = True

        has_text = chars / pages >= MIN_CHARS_PER_PAGE and printable / max(chars, 1) >= MIN_PRINTABLE
        if has_text and covering_image:
            return {"route": "scan", "pages": pages,
                    "reason": "A full-page image with someone else's text layer on top, so that text is not trusted. Treated as a scan."}
        if has_text:
            return {"route": "text", "pages": pages, "reason": "Machine-readable text found, so rules can read it."}
        if has_image:
            return {"route": "scan", "pages": pages, "reason": "No usable text on the page, so this is a scan."}
        return {"route": "unreadable", "pages": pages, "reason": "This PDF has neither usable text nor an image."}
