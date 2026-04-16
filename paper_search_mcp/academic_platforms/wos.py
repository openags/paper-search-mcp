"""Web of Science connector (optional, requires API key env)."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, List

import requests

from .base import PaperSource
from ..config import get_env
from ..paper import Paper
from ..utils import extract_doi

logger = logging.getLogger(__name__)

_NOT_CONFIGURED_MSG = (
    "Web of Science is not configured. Set PAPER_SEARCH_MCP_WOS_API_KEY "
    "(or legacy WOS_API_KEY) environment variable to enable Web of Science search."
)


class WebOfScienceSearcher(PaperSource):
    """Web of Science metadata search implementation (Starter API)."""

    BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1/documents"

    def __init__(self) -> None:
        self.api_key: str = get_env("WOS_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "paper-search-mcp/1.0"})
        if not self.api_key:
            logger.warning(
                "WebOfScienceSearcher initialized without PAPER_SEARCH_MCP_WOS_API_KEY/WOS_API_KEY. "
                "All calls will raise NotImplementedError until the key is set."
            )

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _pick_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(WebOfScienceSearcher._pick_text(v) for v in value if v)
        if isinstance(value, dict):
            for key in ("value", "name", "displayName", "content", "text"):
                selected = value.get(key)
                if selected:
                    return WebOfScienceSearcher._pick_text(selected)
        return ""

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:  # type: ignore[override]
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        query = (query or "").strip()
        if not query or max_results <= 0:
            return []

        params = {
            "db": kwargs.get("db", "WOS"),
            "q": query,
            "limit": min(max_results, 50),
            "page": max(1, int(kwargs.get("page", 1))),
        }
        headers = {"X-ApiKey": self.api_key}

        try:
            response = self.session.get(self.BASE_URL, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logger.error("Web of Science search error: %s", exc)
            return []

        records = payload.get("hits") or payload.get("records") or payload.get("data") or []
        papers: List[Paper] = []

        for item in records:
            if len(papers) >= max_results:
                break

            paper_id = self._pick_text(item.get("uid") or item.get("ut") or item.get("id"))
            title = self._pick_text(item.get("title"))
            if not title:
                continue

            names = item.get("names") or {}
            raw_authors = names.get("authors") or names.get("author") or []
            if isinstance(raw_authors, dict):
                raw_authors = raw_authors.get("authors") or raw_authors.get("author") or []
            authors = [self._pick_text(a) for a in raw_authors if self._pick_text(a)]

            abstract = self._pick_text(item.get("abstract") or item.get("abstractText"))

            identifiers = item.get("identifiers") or {}
            doi = self._pick_text(item.get("doi") or identifiers.get("doi"))
            if not doi and abstract:
                doi = extract_doi(abstract)

            links = item.get("links") or {}
            url = self._pick_text(links.get("record") or links.get("self") or item.get("sourceUrl"))

            published_date = None
            year_value = item.get("publishYear") or item.get("year")
            try:
                if year_value:
                    published_date = datetime(int(year_value), 1, 1)
            except (ValueError, TypeError):
                published_date = None

            papers.append(
                Paper(
                    paper_id=paper_id or doi or title,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    doi=doi,
                    published_date=published_date,
                    pdf_url="",
                    url=url,
                    source="wos",
                    citations=int(item.get("timesCited") or item.get("citationCount") or 0),
                    extra={"source_title": self._pick_text((item.get("source") or {}).get("sourceTitle"))},
                )
            )

        return papers

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)
        raise NotImplementedError(
            "Web of Science is a metadata/index source and does not provide direct PDF downloads."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)
        raise NotImplementedError(
            "Web of Science papers cannot be read directly through this aggregator. "
            "Use DOI/URL to access publisher-hosted full text."
        )
