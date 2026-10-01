"""Conservative validation for new public-source PDF downloads."""
from __future__ import annotations

import io
import os
from pathlib import Path
import re
import tempfile
import unicodedata

from pypdf import PdfReader

MAX_PDF_BYTES = 50 * 1024 * 1024


def _normalize(text: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text).casefold())


def save_verified_pdf(response, output: Path, expected_title: str) -> str:
    """Save atomically only after parsing and matching the title on page one.

    MIME alone never establishes that a response is a PDF. Image-only, corrupt,
    wrong-paper and over-50-MiB files are rejected without replacing a good file.
    """
    content = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        if chunk:
            content.extend(chunk)
            if len(content) > MAX_PDF_BYTES:
                raise ValueError("Public PDF exceeds the 50 MiB download limit")
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-2048:]:
        raise ValueError("Public download is not a complete PDF")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        text = reader.pages[0].extract_text() if reader.pages else ""
    except Exception:
        raise ValueError("Public download is not a readable PDF") from None
    title = _normalize(expected_title)
    if not title or title not in _normalize((text or "")[:12000]):
        raise ValueError("PDF identity could not be verified against the paper title")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".part", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary:
            os.unlink(temporary)
    return str(output)


def read_pdf_text(path: str) -> str:
    return "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
