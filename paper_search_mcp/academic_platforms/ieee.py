"""IEEE Xplore connector — requires API key env variable.

This module connects to the IEEE Xplore Metadata Search API.
Enable usage::

    export PAPER_SEARCH_MCP_IEEE_API_KEY=<your_ieee_api_key>

Obtain a free API key at https://developer.ieee.org/.
"""

from __future__ import annotations

import logging
import time
import re
from urllib.parse import parse_qs, urlparse
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
    "to enable IEEE Xplore metadata search.  "
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
    RETRYABLE_CODES = {429, 500, 502, 503, 504}
    RETRYABLE_EXCEPTIONS = (requests.Timeout, requests.ConnectionError)

    def __init__(self) -> None:
        self.api_key: str = get_env("IEEE_API_KEY", "").strip()
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

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        """Bounded retry, with redacted errors (IEEE puts its key in the URL)."""
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.session.get(self.BASE_URL, params=params, timeout=30,
                                            allow_redirects=False)
            except self.RETRYABLE_EXCEPTIONS:
                if attempt + 1 == self.MAX_RETRIES:
                    raise RuntimeError("IEEE metadata request failed (network or timeout)") from None
                time.sleep(min(2 ** attempt, 4))
                continue
            except requests.RequestException:
                raise RuntimeError("IEEE metadata request failed") from None
            if response.status_code == 200:
                try:
                    data = response.json()
                except (ValueError, TypeError):
                    raise RuntimeError("IEEE metadata API returned invalid JSON") from None
                if not isinstance(data, dict):
                    raise RuntimeError("IEEE metadata API returned an invalid response")
                if data.get("error") or data.get("errors"):
                    raise RuntimeError("IEEE metadata API returned an error response")
                if not isinstance(data.get("articles"), list):
                    if data.get("total_records") in (0, "0") or data.get("totalfound") in (0, "0"):
                        data["articles"] = []
                    else:
                        raise RuntimeError("IEEE metadata response is missing its articles array")
                return data
            if response.status_code in {401, 403}:
                raise RuntimeError(f"IEEE metadata access denied (HTTP {response.status_code}); check the configured key and entitlement")
            if response.status_code not in self.RETRYABLE_CODES or attempt + 1 == self.MAX_RETRIES:
                raise RuntimeError(f"IEEE metadata API returned HTTP {response.status_code}")
            retry_after = (response.headers.get("Retry-After") or "").strip()
            wait = int(retry_after) if retry_after.isdigit() else min(2 ** attempt, 4)
            if wait > 4:
                raise RuntimeError("IEEE metadata API is rate-limited; retry later")
            time.sleep(wait)
        raise RuntimeError("IEEE metadata retries exhausted")

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
            max_results: Maximum number of results (0 through 1000; higher values fail).
            **kwargs: Ignored for forward compatibility.

        Returns:
            List of Paper objects.

        Raises:
            NotImplementedError: When IEEE API key is not configured.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 0 <= max_results <= self.MAX_RESULTS_CAP:
            raise ValueError("IEEE max_results must be an integer from 0 to 1000")
        if not max_results:
            return []
        if not isinstance(query, str) or not query.strip():
            raise ValueError("IEEE query must not be empty")
        papers, seen = [], set()
        start_record = 1
        # At most 20 pages, so even a drifting upstream index is bounded.
        for _ in range(20):
            page_size = min(max_results - len(papers), 200)
            data = self._request({"querytext": query, "apikey": self.api_key,
                                  "start_record": start_record, "max_records": page_size})
            articles = data["articles"]
            previous_count = len(seen)
            for article in articles:
                paper = self._parse_article(article)
                if paper.paper_id not in seen:
                    seen.add(paper.paper_id)
                    papers.append(paper)
                    if len(papers) == max_results:
                        return papers
            if not articles or len(articles) < page_size:
                return papers
            if len(seen) == previous_count:
                raise RuntimeError("IEEE metadata pagination did not advance")
            start_record += len(articles)
        raise RuntimeError("IEEE metadata scan limit reached; narrow the query")

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download a PDF from IEEE Xplore.

        Note: Full-text download typically requires institutional IEEE access.
        """
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        raise NotImplementedError(
            "This connector supports IEEE metadata only. A metadata API key does not "
            "grant PDF retrieval; open the returned paper URL for available access options."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read paper content from IEEE Xplore."""
        if not self.is_configured():
            raise NotImplementedError(_NOT_CONFIGURED_MSG)

        raise NotImplementedError(
            "This connector supports IEEE metadata only; direct PDF reading is not implemented."
        )

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_article(self, article: dict) -> Optional[Paper]:
        """Parse a single IEEE API article into a Paper object."""
        if not isinstance(article, dict):
            raise RuntimeError("IEEE metadata returned a malformed article")
        article_number = str(article.get("article_number") or "")
        title = article.get("title")
        if not re.fullmatch(r"[0-9]+", article_number) or not isinstance(title, str) or not title.strip():
            raise RuntimeError("IEEE article is missing a valid article number or title")
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
            url = f"https://ieeexplore.ieee.org/document/{article_number}"
            pdf_url = article.get("pdf_url") or ""
            parsed_pdf = urlparse(pdf_url)
            if (parsed_pdf.scheme != "https" or parsed_pdf.hostname != "ieeexplore.ieee.org"
                    or parsed_pdf.username or parsed_pdf.password or parsed_pdf.port
                    or parse_qs(parsed_pdf.query).get("arnumber") != [article_number]):
                pdf_url = ""

            return Paper(
                paper_id=str(article_number),
                title=article.get("title", ""),
                authors=authors,
                abstract=article.get("abstract", ""),
                doi=article.get("doi", ""),
                published_date=pub_date,
                pdf_url=pdf_url,
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
        except (ValueError, TypeError, AttributeError):
            raise RuntimeError("IEEE metadata returned malformed article fields") from None

    def _parse_date(self, article: dict) -> Optional[datetime]:
        """Multi-format date parser with publication_year fallback.

        IEEE publication_date formats vary:
        - YYYY-MM-DD, YYYY-MM, YYYY, Mon YYYY (e.g. 'Jan 2023')
        """
        date_str = article.get("publication_date", "")
        if isinstance(date_str, str) and date_str:
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
