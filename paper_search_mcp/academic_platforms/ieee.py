"""IEEE Xplore connector — requires API key env variable.

This module connects to the IEEE Xplore Metadata Search API.
Enable usage::

    export PAPER_SEARCH_MCP_IEEE_API_KEY=<your_ieee_api_key>

Obtain a free API key at https://developer.ieee.org/.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, List, Optional

import requests

from .base import PaperSource
from ..paper import Paper
from ..config import get_env

logger = logging.getLogger(__name__)

_NOT_CONFIGURED_MSG = (
    "IEEE Xplore is not configured.  Set PAPER_SEARCH_MCP_IEEE_API_KEY "
    "(or legacy IEEE_API_KEY) environment variable "
    "to enable IEEE Xplore search and download.  "
    "Obtain a free API key at https://developer.ieee.org/."
)


class IEEESearcher(PaperSource):
    """IEEE Xplore Metadata Search API connector.

    Supports search with retry/backoff, pagination for >200 results,
    and multi-format date parsing.
    """

    BASE_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
    MAX_RETRIES = 3
    MAX_RESULTS_CAP = 1000
    RETRYABLE_CODES = {403, 429, 500, 502, 503, 504}
    RETRYABLE_EXCEPTIONS = (requests.Timeout, requests.ConnectionError)

    def __init__(self) -> None:
        self.api_key: str = get_env("IEEE_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "paper-search-mcp/1.0",
            "Accept": "application/json",
        })
        if not self.api_key:
            logger.warning(
                "IEEESearcher initialised without API key. "
                "All calls will raise NotImplementedError until the key is set."
            )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return True only when a non-empty IEEE API key is available."""
        return bool(self.api_key)

    # ------------------------------------------------------------------
    # Internal request with retry/backoff
    # ------------------------------------------------------------------

    def _request(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """GET with retry/backoff aligned with semantic.py pattern."""
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self.session.get(
                    self.BASE_URL, params=params, timeout=30,
                )
            except self.RETRYABLE_EXCEPTIONS:
                wait = min(8, 2 ** attempt)
                logger.warning(
                    "IEEE connection error, retry %d/%d in %ds",
                    attempt + 1, self.MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue
            except requests.RequestException as e:
                logger.error("IEEE request failed: %s", e)
                return None

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in self.RETRYABLE_CODES:
                retry_after = (resp.headers.get("Retry-After") or "").strip()
                wait = (
                    int(retry_after)
                    if retry_after.isdigit()
                    else min(8, 2 ** attempt)
                )
                logger.warning(
                    "IEEE API %d, retry %d/%d in %ds",
                    resp.status_code, attempt + 1, self.MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue

            # Other 4xx: fail immediately
            logger.error("IEEE API returned %d", resp.status_code)
            return None

        logger.error("IEEE API max retries exceeded")
        return None

    # ------------------------------------------------------------------
    # PaperSource interface
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> List[Paper]:
        """Search IEEE Xplore Metadata API.

        Args:
            query: Search query string.
            max_results: Maximum number of results (capped at 1000).
            **kwargs: Ignored for forward compatibility.

        Returns:
            List of Paper objects.

        Raises:
            NotImplementedError: When IEEE API key is not configured.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        max_results = min(max_results, self.MAX_RESULTS_CAP)
        params: dict[str, Any] = {
            "querytext": query,
            "apikey": self.api_key,
        }

        # Pagination for max_results > 200
        articles: list[dict[str, Any]] = []
        start_record = 1
        page_size = min(max_results, 200)

        while len(articles) < max_results:
            params["start_record"] = start_record
            params["max_records"] = page_size

            data = self._request(params)
            if not data:
                break

            batch = data.get("articles", [])
            if not batch:
                break

            articles.extend(batch)
            if len(articles) >= max_results:
                break
            if len(batch) < page_size:
                break  # Last page
            start_record += page_size

        papers: list[Paper] = []
        for a in articles[:max_results]:
            p = self._parse_article(a)
            if p:
                papers.append(p)
        return papers

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download a PDF from IEEE Xplore.

        Note: Full-text download typically requires institutional IEEE access.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        raise NotImplementedError(
            "IEEE Xplore PDF download requires institutional access. "
            "Set IEEE_API_KEY to enable."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read paper content from IEEE Xplore."""
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        raise NotImplementedError(
            "IEEE Xplore paper reading requires institutional access. "
            "Set IEEE_API_KEY to enable."
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_article(self, article: dict) -> Optional[Paper]:
        """Parse a single IEEE API article into a Paper object."""
        try:
            # Authors: nested authors.authors[].full_name
            authors_container = article.get("authors")
            authors: list[str] = []
            if isinstance(authors_container, dict):
                for a in authors_container.get("authors", []):
                    if isinstance(a, dict) and a.get("full_name"):
                        authors.append(a["full_name"].strip())

            # Keywords: merge ieee_terms + author_terms from index_terms
            keywords: list[str] = []
            index_terms = article.get("index_terms", {})
            if isinstance(index_terms, dict):
                for term_key in ("ieee_terms", "author_terms"):
                    terms = index_terms.get(term_key, {})
                    if isinstance(terms, dict):
                        keywords.extend(terms.get("terms", []))

            # Date: multi-format parser with year fallback
            pub_date = self._parse_date(article)

            # URL: html_url preferred, fallback to constructed URL
            url = article.get("html_url", "")
            article_number = article.get("article_number", "")
            if not url and article_number:
                url = f"https://ieeexplore.ieee.org/document/{article_number}"

            return Paper(
                paper_id=str(article_number),
                title=article.get("title", ""),
                authors=authors,
                abstract=article.get("abstract", ""),
                doi=article.get("doi", ""),
                published_date=pub_date,
                pdf_url=article.get("pdf_url", ""),
                url=url,
                source="ieee",
                citations=int(article.get("citing_paper_count", 0) or 0),
                keywords=keywords,
                extra={
                    "publication_title": article.get("publication_title", ""),
                    "content_type": article.get("content_type", ""),
                    "access_type": article.get("accessType", ""),
                    "volume": article.get("volume", ""),
                    "issue": article.get("issue", ""),
                    "publisher": article.get("publisher", ""),
                },
            )
        except Exception as e:
            logger.warning("Failed to parse IEEE article: %s", e)
            return None

    def _parse_date(self, article: dict) -> Optional[datetime]:
        """Multi-format date parser with publication_year fallback.

        IEEE publication_date formats vary:
        - YYYY-MM-DD, YYYY-MM, YYYY, Mon YYYY (e.g. 'Jan 2023')
        """
        date_str = article.get("publication_date", "")
        if date_str:
            for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%b %Y", "%B %Y"):
                try:
                    return datetime.strptime(date_str.strip(), fmt)
                except ValueError:
                    continue
        year_str = article.get("publication_year")
        if year_str:
            try:
                return datetime(int(year_str), 1, 1)
            except (ValueError, TypeError):
                pass
        return None
