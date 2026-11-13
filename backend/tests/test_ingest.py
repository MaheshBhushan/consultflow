import pytest

from app.ingest import IngestError, parse_document, parse_email


def make_pdf(text: str) -> bytes:
    """Build a minimal one-page PDF containing `text` (no external tools needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def test_eml_parsing_extracts_headers_body_and_attachments(samples):
    parsed = parse_email((samples / "invoice-dispute.eml").read_bytes())
    assert parsed.kind == "email"
    assert parsed.meta["subject"].startswith("Invoice INV-2026-0412 disputed")
    assert parsed.meta["message_id"] == "<20261103091400.4412@northwind-logistics.example>"
    assert parsed.meta["attachments"] == ["po-7781.txt"]
    assert "duplicate line of $3,200.00" in parsed.text
    assert parsed.text.startswith("From: Dana Whitfield")
    assert "PO 7781" not in parsed.text  # attachment content is not part of the body
    assert len(parsed.sha256) == 64


def test_pasted_email_without_headers_is_kept_as_body():
    parsed = parse_email("hi team,\nplease send the Q3 report by Friday.")
    assert parsed.meta["subject"] == ""
    assert parsed.text == "hi team,\nplease send the Q3 report by Friday."


def test_html_email_is_converted_to_text():
    raw = (
        "From: a@example.com\nSubject: hi\nContent-Type: text/html\n\n"
        "<html><body><p>Hello &amp; welcome</p><script>x()</script></body></html>"
    )
    parsed = parse_email(raw)
    assert "Hello & welcome" in parsed.text
    assert "x()" not in parsed.text


def test_markdown_document(samples):
    parsed = parse_document("sow.md", (samples / "statement-of-work.md").read_bytes())
    assert parsed.kind == "document"
    assert "2.3 million SKU records" in parsed.text


def test_pdf_document():
    parsed = parse_document("letter.pdf", make_pdf("Payment due 2026-12-01 amount $900.00"))
    assert "Payment due 2026-12-01" in parsed.text
    assert parsed.meta["pages"] == 1


def test_eml_upload_is_routed_to_email_parser(samples):
    parsed = parse_document("x.eml", (samples / "invoice-dispute.eml").read_bytes())
    assert parsed.kind == "email"


@pytest.mark.parametrize(
    "name,data,msg",
    [
        ("a.exe", b"MZ", "unsupported file type"),
        ("a.txt", b"   ", "no text"),
        ("a.pdf", b"not a pdf", "could not read PDF"),
    ],
)
def test_rejects_bad_documents(name, data, msg):
    with pytest.raises(IngestError, match=msg):
        parse_document(name, data)
