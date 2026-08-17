"""Shared output-path and filename helpers for downloaded papers."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import get_env

DEFAULT_OUTPUT_DIR = r"D:\documents\文献"


def get_default_output_dir() -> str:
    """Return the configured default output directory for CLI downloads."""
    value = get_env("DEFAULT_OUTPUT", DEFAULT_OUTPUT_DIR).strip()
    return value or DEFAULT_OUTPUT_DIR


def paper_output_path(
    save_path: str,
    *,
    title: str = "",
    authors: Any = None,
    published_date: Any = None,
    identifier: str = "",
    extension: str = ".pdf",
    unique: bool = False,
) -> Path:
    """Return an output path using FirstAuthor_Year_ShortTitle.ext."""
    output_dir = Path(save_path).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = paper_filename(
        title=title,
        authors=authors,
        published_date=published_date,
        identifier=identifier,
        extension=extension,
    )
    path = output_dir / filename
    return _unique_path(path) if unique else path


def paper_output_path_for_paper(
    save_path: str,
    paper: Any,
    *,
    identifier: str = "",
    extension: str = ".pdf",
    unique: bool = False,
) -> Path:
    """Return a unique output path from a Paper-like object or dict."""
    return paper_output_path(
        save_path,
        title=_field(paper, "title"),
        authors=_field(paper, "authors"),
        published_date=_field(paper, "published_date"),
        identifier=identifier or _field(paper, "paper_id") or _field(paper, "doi"),
        extension=extension,
        unique=unique,
    )


def paper_filename(
    *,
    title: str = "",
    authors: Any = None,
    published_date: Any = None,
    identifier: str = "",
    extension: str = ".pdf",
) -> str:
    """Build FirstAuthor_Year_ShortTitle.ext with filesystem-safe parts."""
    ext = extension if extension.startswith(".") else f".{extension}"
    first_author = _first_author(authors)
    year = _year(published_date)
    short_title = _short_title(title, identifier)
    return f"{first_author}_{year}_{short_title}{ext}"


def metadata_text(
    *,
    title: str = "",
    authors: Any = None,
    abstract: str = "",
    doi: str = "",
    url: str = "",
) -> str:
    """Format metadata for abstract-only fallback files."""
    author_text = "; ".join(_author_values(authors))
    return "\n".join(
        [
            f"Title: {title}",
            f"Authors: {author_text}",
            f"Abstract: {abstract}",
            f"DOI: {doi}",
            f"URL: {url}",
            "",
        ]
    )


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name, "")
    return getattr(value, name, "")


def _author_values(authors: Any) -> list[str]:
    if not authors:
        return []
    if isinstance(authors, str):
        return [part.strip() for part in re.split(r"\s*;\s*|\s+\band\b\s+", authors) if part.strip()]
    if isinstance(authors, Iterable):
        return [str(part).strip() for part in authors if str(part).strip()]
    return [str(authors).strip()]


def _first_author(authors: Any) -> str:
    values = _author_values(authors)
    if not values:
        return "Unknown"

    first = values[0]
    if "," in first:
        first = first.split(",", 1)[0]
    tokens = re.findall(r"[^\W_]+", first, flags=re.UNICODE)
    if not tokens:
        return "Unknown"
    if len(tokens) > 1 and len(tokens[-1]) <= 3 and tokens[-1].isupper():
        token = tokens[0]
    else:
        token = tokens[-1]
    return _safe_part(token, "Unknown")


def _year(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return str(value.year)
    text = str(value or "")
    match = re.search(r"(18|19|20|21)\d{2}", text)
    return match.group(0) if match else "UnknownYear"


def _short_title(title: str, identifier: str) -> str:
    source = title or identifier or "paper"
    words = re.findall(r"[^\W_]+", source, flags=re.UNICODE)[:5]
    value = "_".join(words) if words else source
    return _safe_part(value, "paper")


def _safe_part(value: str, default: str) -> str:
    cleaned = re.sub(r"[^\w]+", "_", str(value), flags=re.UNICODE).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or default


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find an available filename for {path}")
