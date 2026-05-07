import re

def extract_doi(text: str) -> str:
    """Extract DOI from arbitrary text or URL if present."""
    if not text:
        return ""
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.IGNORECASE)
    return match.group(0).rstrip(".,;)") if match else ""


def is_pdf_content(content: bytes, content_type: str = "", url: str = "") -> bool:
    """Return True when bytes look like a real PDF response."""
    if not content:
        return False

    lowered_type = (content_type or "").lower()
    lowered_url = (url or "").lower()
    return (
        content.startswith(b"%PDF")
        or "pdf" in lowered_type
        or lowered_url.endswith(".pdf")
    )
