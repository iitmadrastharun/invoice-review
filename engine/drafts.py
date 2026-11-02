"""Drafts the message a clerk sends next: to the vendor, the buyer, or a call note.

Claude writes it when it is available; otherwise a fixed template does. Either way it only uses
facts the rules already established, and a person reads and sends it. Nothing is sent from here.
"""
import json
import shutil
import subprocess

from engine import ai_reader
from engine.rules import CODE_NAMES, fmt

TIMEOUT_SECONDS = 60
# Codes where the next step is to call the vendor on the number on file, never to email.
CALL_CODES = {"H-VENDOR-BANK", "H-SENDER"}
# Codes the buyer, not the vendor, has to answer.
BUYER_CODES = {"H-AMOUNT", "H-RECEIPT", "H-PO-NONE", "H-TAX"}

SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
}

PROMPT = """You are helping an accounts payable clerk at {buyer_company} write one short message about a held or rejected vendor invoice.

Write to: {to}
Kind of message: {kind}
Facts (the only facts you may use; do not add numbers, dates or promises that are not here):
{facts}

Rules:
- Plain, polite, specific. Under 140 words. No greeting fluff, no sign-off name (the clerk adds it).
- Say exactly what is needed to move the invoice forward, using the figures given.
- For a call note, write the points the clerk should confirm on the phone, as short lines.
- Never ask anyone to change bank details by email, and never use contact details from the invoice itself.
Return a subject line and a body."""


def recipient(run, vendor, po):
    codes = set(run["codes"])
    if run["decision"] == "REJECT":
        return "vendor", f"{vendor['legal_name']}, accounts receivable" if vendor else "The vendor"
    if codes & CALL_CODES:
        phone = vendor["callback_phone"] if vendor else "the number on file"
        return "call", f"Call {vendor['legal_name'] if vendor else 'the vendor'} on {phone} (the number on file, not the one on the invoice)"
    if run["codes"] and run["codes"][0] in BUYER_CODES and po:
        return "buyer", f"{po['buyer']}, buyer for {po['po_number']}"
    return "vendor", f"{vendor['legal_name']}, accounts receivable" if vendor else "The vendor"


def facts(run, vendor, po):
    result = run["result"] or {}
    fields = run["fields"] or {}
    value = lambda n: (fields.get(n) or {}).get("value")
    lines = [
        f"Vendor: {vendor['legal_name'] if vendor else 'not identified'}",
        f"Invoice number: {value('invoice_number') or 'not printed'}",
        f"Invoice date: {value('invoice_date') or 'unknown'}",
        f"PO: {value('po_number') or 'none printed'}",
        f"Invoice total: {fmt(result['gross']) if result.get('gross') else 'unknown'}",
        f"Decision: {run['decision']}",
    ]
    for reason in result.get("reasons", []):
        lines.append(f"Problem ({CODE_NAMES.get(reason['code'], reason['code'])}): {reason['sentence']}")
        lines.append(f"What is needed: {reason['action']}")
    if po:
        lines.append(f"PO buyer: {po['buyer']}")
    return "\n".join(f"- {line}" for line in lines)


def template(run, kind, to, vendor):
    """The fallback when Claude is not available. Built from the rules' own sentences."""
    result = run["result"] or {}
    fields = run["fields"] or {}
    number = (fields.get("invoice_number") or {}).get("value") or "(no number printed)"
    reasons = result.get("reasons", [])
    first = reasons[0] if reasons else {"sentence": result.get("summary", ""), "action": ""}
    if kind == "call":
        body = "\n".join([
            f"Before calling: use only the phone number on file. Do not use any number or address on the invoice or in the email.",
            f"- Confirm they sent invoice {number}.",
            f"- {first['sentence']}",
            "- Ask them to confirm the account they want to be paid into. Read back only the last 4 digits on file; never read out the new one.",
            "- Note who you spoke to and when, then record it in the final decision.",
        ])
        return {"subject": f"Call note: confirm invoice {number}", "body": body}
    what = " ".join(r["sentence"] for r in reasons[:2]) or result.get("summary", "")
    if run["decision"] == "REJECT":
        body = f"We cannot process invoice {number}. {what}\n\n{first['action']}"
        return {"subject": f"Invoice {number} cannot be processed", "body": body}
    ask = first["action"]
    body = f"Invoice {number} is on hold. {what}\n\n{ask}\n\nOnce this is sorted out, the invoice goes back into the next payment run."
    return {"subject": f"Invoice {number} on hold: {CODE_NAMES.get(first.get('code', ''), 'action needed').lower()}", "body": body}


def draft(run, vendor, po, policy):
    kind, to = recipient(run, vendor, po)
    base = {"to": to, "kind": kind}
    claude = shutil.which("claude")
    if ai_reader.available() and claude:
        prompt = PROMPT.format(buyer_company=policy["buyer_name"], to=to, kind={"call": "a call note", "buyer": "an internal email to the buyer", "vendor": "an email to the vendor"}[kind],
                               facts=facts(run, vendor, po))
        # A smaller model is enough here: it only rewords facts the rules settled, and a person reads it before sending.
        command = [claude, "-p", "--model", "haiku", "--tools", "", "--output-format", "json", "--json-schema", json.dumps(SCHEMA), "--no-session-persistence"]
        try:
            done = subprocess.run(command, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_SECONDS)
            envelope = json.loads(done.stdout) if done.returncode == 0 else {}
            out = envelope.get("structured_output") or json.loads(envelope.get("result") or "null")
            if isinstance(out, dict) and out.get("subject") and out.get("body"):
                return {**base, "subject": out["subject"].strip(), "body": out["body"].strip(), "source": "claude"}
        except (subprocess.TimeoutExpired, json.JSONDecodeError, TypeError, OSError):
            pass
        return {**base, **template(run, kind, to, vendor), "source": "template", "note": "Claude did not answer in time, so this is the standard template."}
    return {**base, **template(run, kind, to, vendor), "source": "template"}
