"""Turn uploaded documents and emails into plain text plus metadata."""

from __future__ import annotations

import hashlib
import html
import io
import re
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser

from pypdf import PdfReader

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".log"}
MAX_BYTES = 5 * 1024 * 1024


class IngestError(ValueError):
    pass


@dataclass
class Parsed:
    kind: str  # "document" | "email"
    text: str
    sha256: str
    meta: dict = field(default_factory=dict)


def _ext(filename: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]+)$", filename or "")
    return m.group(1).lower() if m else ""


def _strip_html(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def parse_document(filename: str, data: bytes) -> Parsed:
    if len(data) > MAX_BYTES:
        raise IngestError("file larger than 5 MB")
    ext = _ext(filename)
    meta: dict = {"bytes": len(data)}
    if ext == ".eml":
        return parse_email(data)
    if ext == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:  # pypdf raises several unrelated exception types
            raise IngestError(f"could not read PDF: {exc}") from exc
        meta["pages"] = len(reader.pages)
    elif ext in {".html", ".htm"}:
        text = _strip_html(data.decode("utf-8", errors="replace"))
    elif ext in TEXT_EXTENSIONS:
        text = data.decode("utf-8", errors="replace")
    else:
        raise IngestError(
            f"unsupported file type {ext or '(none)'}; use .txt .md .csv .pdf .html .eml"
        )
    text = text.strip()
    if not text:
        raise IngestError("no text could be extracted")
    return Parsed("document", text, hashlib.sha256(data).hexdigest(), meta)


def parse_email(raw: bytes | str) -> Parsed:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if not data.strip():
        raise IngestError("empty email")
    msg = BytesParser(policy=policy.default).parsebytes(data)
    has_headers = any(msg.get(h) for h in ("From", "Subject", "To"))
    if has_headers:
        body_part = msg.get_body(preferencelist=("plain", "html"))
        if body_part is None:
            body = ""
        else:
            body = body_part.get_content()
            if body_part.get_content_type() == "text/html":
                body = _strip_html(body)
        attachments = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
        meta = {
            "subject": str(msg.get("Subject", "")),
            "from": str(msg.get("From", "")),
            "to": str(msg.get("To", "")),
            "date": str(msg.get("Date", "")),
            "message_id": str(msg.get("Message-ID", "")),
            "attachments": attachments,
        }
    else:
        # Pasted body without headers.
        body = data.decode("utf-8", errors="replace")
        meta = {
            "subject": "",
            "from": "",
            "to": "",
            "date": "",
            "message_id": "",
            "attachments": [],
        }
    body = body.strip()
    if not body:
        raise IngestError("email has no text body")
    header = "\n".join(
        f"{k.capitalize()}: {meta[k]}" for k in ("from", "to", "date", "subject") if meta[k]
    )
    text = f"{header}\n\n{body}" if header else body
    return Parsed("email", text, hashlib.sha256(data).hexdigest(), meta)
