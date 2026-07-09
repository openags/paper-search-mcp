"""Small CrossRef helpers used by the CLI and filename fallback paths."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote

import requests

from .config import load_env_file

CROSSREF_WORKS_URL = "https://api.crossref.org/works"

# Known DOI prefixes used for spam/duplicate registrations; skip them.
_SPAM_DOI_PREFIXES = {"10.65215/"}

# Ensure proxy env vars are loaded before any network call.
load_env_file()


def _is_spam_doi(doi: str) -> bool:
    doi_lower = (doi or "").lower()
    return any(doi_lower.startswith(prefix) for prefix in _SPAM_DOI_PREFIXES)


def _resolve_via_crossref(title: str, *, timeout: int = 20) -> dict[str, Any]:
    """Resolve a title through CrossRef."""
    try:
        response = requests.get(
            CROSSREF_WORKS_URL,
            params={"query.title": title, "rows": 40},
            timeout=timeout,
        )
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
    except Exception:
        return {"error": "not found"}

    clean_items = [item for item in items if not _is_spam_doi(item.get("DOI", ""))]
    if not clean_items:
        return {"error": "not found"}

    item = max(clean_items, key=lambda candidate: _ranking_score(title, candidate))
    doi = (item.get("DOI") or "").strip()
    resolved_title = _first(item.get("title")) or title
    if not doi:
        return {"error": "not found"}

    return {
        "title": resolved_title,
        "doi": doi,
        "score": item.get("score", 0),
        "year": _year_from_item(item),
    }


def resolve_title(title: str, *, timeout: int = 20) -> dict[str, Any]:
    """Resolve a title to the best DOI candidate from CrossRef."""
    query = title.strip()
    if not query:
        return {"error": "not found"}

    result = _resolve_via_crossref(query, timeout=timeout)
    if result.get("error"):
        return {"error": "not found"}

    return {
        "title": result.get("title") or query,
        "doi": result.get("doi", ""),
        "score": result.get("score", 0),
        "year": result.get("year"),
    }


def metadata_for_identifier(identifier: str, *, timeout: int = 20) -> dict[str, Any]:
    """Return CrossRef metadata for a DOI or title."""
    value = identifier.strip()
    if not value:
        return {}

    doi = value if _looks_like_doi(value) else ""
    if not doi:
        resolved = resolve_title(value, timeout=timeout)
        doi = resolved.get("doi", "")
        if not doi:
            return {
                "title": value,
                "authors": [],
                "abstract": "",
                "doi": "",
                "url": "",
                "published_date": "",
            }

    try:
        response = requests.get(
            f"{CROSSREF_WORKS_URL}/{quote(doi, safe='')}",
            timeout=timeout,
        )
        response.raise_for_status()
        item = response.json().get("message", {})
    except Exception:
        return {
            "title": "",
            "authors": [],
            "abstract": "",
            "doi": doi,
            "url": f"https://doi.org/{doi}",
            "published_date": "",
        }

    return {
        "title": _first(item.get("title")),
        "authors": _authors(item.get("author", [])),
        "abstract": _clean_abstract(item.get("abstract", "")),
        "doi": item.get("DOI", doi),
        "url": item.get("URL") or f"https://doi.org/{doi}",
        "published_date": str(_year_from_item(item) or ""),
    }


def _looks_like_doi(value: str) -> bool:
    return bool(re.match(r"^10\.\d{4,9}/\S+$", value.strip(), flags=re.I))


def _ranking_score(query: str, item: dict[str, Any]) -> float:
    title = _first(item.get("title"))
    similarity = _title_similarity(query, title)
    crossref_score = float(item.get("score") or 0)
    query_words = len(_normalise_title(query).split())
    title_words = len(_normalise_title(title).split())
    length_boost = 0.1 if query_words and abs(query_words - title_words) <= 2 else 0.0
    return (similarity + length_boost) * 2000 + crossref_score


def _title_similarity(left: str, right: str) -> float:
    left_norm = _normalise_title(left)
    right_norm = _normalise_title(right)
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _normalise_title(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _first(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]).strip() if value else ""
    return str(value or "").strip()


def _year_from_item(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "published", "issued"):
        date_parts = item.get(key, {}).get("date-parts", [])
        if date_parts and date_parts[0]:
            try:
                return int(date_parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _authors(raw_authors: list[dict[str, Any]]) -> list[str]:
    authors = []
    for author in raw_authors:
        literal = str(author.get("name", "")).strip()
        given = str(author.get("given", "")).strip()
        family = str(author.get("family", "")).strip()
        value = literal or " ".join(part for part in (given, family) if part)
        if value:
            authors.append(value)
    return authors


def _clean_abstract(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", text).strip()
