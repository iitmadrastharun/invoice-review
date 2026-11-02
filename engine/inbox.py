"""The AP mailbox, standing in for a real one: .eml files in samples/inbox, each with a PDF attached."""
import email
import email.policy
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

INBOX = Path(__file__).parent.parent / "samples" / "inbox"


def _parse(path):
    with open(path, "rb") as f:
        return email.message_from_binary_file(f, policy=email.policy.default)


def messages():
    """Every email in the mailbox, newest first, without the attachment bytes."""
    out = []
    for path in sorted(INBOX.glob("*.eml")):
        msg = _parse(path)
        name, address = parseaddr(msg["From"])
        body = msg.get_body(preferencelist=("plain",))
        attachments = [part.get_filename() for part in msg.iter_attachments() if part.get_filename()]
        out.append({
            "id": path.stem, "from_name": name, "from": address, "subject": str(msg["Subject"] or ""),
            "date": parsedate_to_datetime(msg["Date"]).isoformat() if msg["Date"] else None,
            "body": body.get_content().strip() if body else "", "attachments": attachments,
        })
    return sorted(out, key=lambda m: m["date"] or "", reverse=True)


def attachments(message_id, folder):
    """Writes each PDF attachment of one email into folder. Returns [(path, file name)] and the sender."""
    path = INBOX / f"{message_id}.eml"
    if path.parent != INBOX or not path.exists():
        raise KeyError(message_id)
    msg = _parse(path)
    saved = []
    for i, part in enumerate(msg.iter_attachments()):
        name = part.get_filename() or f"attachment-{i + 1}.pdf"
        if not name.lower().endswith(".pdf") and part.get_content_type() != "application/pdf":
            continue
        target = Path(folder) / f"{i}-{Path(name).name}"
        target.write_bytes(part.get_payload(decode=True))
        saved.append((target, Path(name).name))
    return saved, parseaddr(msg["From"])[1], str(msg["Subject"] or "")
