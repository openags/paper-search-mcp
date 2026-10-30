"""Explicit-opt-in Web of Science Starter API metadata search.

Adapted from PR #48. No PDF/full-text access and no implicit default searches.
"""
from __future__ import annotations

from datetime import datetime

import requests

from .base import PaperSource
from .institutional import (
    CredentialsRequiredError, ProviderResponseError, request_json, result_limit,
    safe_int, text,
)
from ..config import get_env
from ..paper import Paper


class WebOfScienceSearcher(PaperSource):
    BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1/documents"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self.session = requests.Session()

    def is_configured(self) -> bool:
        return bool(self._key())

    def _key(self) -> str:
        return (get_env("WOS_API_KEY", "") if self._api_key is None else self._api_key).strip()

    def search(self, query: str, max_results: int = 10, *, db: str = "WOS") -> list[Paper]:
        """Search native WoS query syntax, at most 100 records / two pages."""
        limit = result_limit(max_results)
        if not query.strip() or not limit:
            return []
        key = self._key()
        if not key:
            raise CredentialsRequiredError("wos", "Set PAPER_SEARCH_MCP_WOS_API_KEY (or WOS_API_KEY).")
        if db not in {"BCI", "BIOABS", "BIOSIS", "CCC", "DIIDW", "DRCI", "MEDLINE",
                      "PPRN", "RC", "WOK", "WOS", "ZOOREC"}:
            raise ValueError("Unsupported WoS database")
        page_size = min(limit, 50)
        papers: list[Paper] = []
        seen: set[str] = set()
        for page in range(1, 3):
            payload = request_json(self.session, "wos", self.BASE_URL,
                                   headers={"X-ApiKey": key, "Accept": "application/json",
                                            "User-Agent": "paper-search-mcp/0.1.4"},
                                   params={"q": query.strip(), "db": db,
                                           "page": page, "limit": page_size})
            hits = payload.get("hits")
            if not isinstance(hits, list):
                raise ProviderResponseError("wos", "Expected a hits array.")
            for item in hits:
                if not isinstance(item, dict) or not text(item.get("uid")) or not text(item.get("title")):
                    raise ProviderResponseError("wos", "A result is missing its UID or title.")
                paper = self._parse_paper(item, db)
                if paper.paper_id not in seen:
                    seen.add(paper.paper_id)
                    papers.append(paper)
                if len(papers) == limit:
                    return papers
            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ProviderResponseError("wos", "Invalid pagination metadata.")
            total = safe_int(metadata.get("total"), default=-1)
            if not hits and total > (page - 1) * page_size:
                raise ProviderResponseError("wos", "Empty page conflicts with the result count.")
            if len(hits) < page_size or (total >= 0 and page * page_size >= total):
                break
        return papers

    @staticmethod
    def _parse_paper(item: dict, db: str) -> Paper:
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        names = item.get("names") if isinstance(item.get("names"), dict) else {}
        raw_authors = names.get("authors", [])
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]
        authors = [text(author) for author in raw_authors if text(author)] if isinstance(raw_authors, list) else []
        ids = item.get("identifiers") if isinstance(item.get("identifiers"), dict) else {}
        links = item.get("links") if isinstance(item.get("links"), dict) else {}
        try:
            published = datetime(int(source.get("publishYear") or item.get("publishYear")), 1, 1)
        except (TypeError, ValueError):
            published = None
        citations = None
        counts = item.get("citations", [])
        if isinstance(counts, list):
            for entry in counts:
                if isinstance(entry, dict) and entry.get("db") == db:
                    citations = safe_int(entry.get("count"))
                    break
        keywords = item.get("keywords") if isinstance(item.get("keywords"), dict) else {}
        return Paper(
            paper_id=text(item["uid"]), title=text(item["title"]), authors=authors,
            abstract="", doi=text(ids.get("doi")), published_date=published, pdf_url="",
            url=text(links.get("record")), source="wos", citations=citations or 0,
            keywords=keywords.get("authorKeywords") or [],
            extra={"source_title": text(source.get("sourceTitle")),
                   "metadata_only": True, "citation_count_available": citations is not None},
        )

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        raise NotImplementedError("Web of Science Starter is metadata-only; PDF download is unsupported.")

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        raise NotImplementedError("Web of Science Starter is metadata-only; full-text reading is unsupported.")
