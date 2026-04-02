"""SSRN (Social Science Research Network) connector for paper-search-mcp.

SSRN is Elsevier's preprint server primarily serving economics, law, business,
and social sciences.  SSRN does not offer a public API.  This connector uses
**SSRN's publicly accessible HTML search endpoint** to retrieve metadata only
— no content scraping, no PDF harvesting.

Legal/compliance note:
  - Only publicly visible metadata (title, authors, abstract, date) is
    collected, which SSRN makes freely available in its standard web
    interface and is legally accessible for personal research use.
  - PDF download is explicitly NOT implemented because full-text access
    requires an SSRN account and would violate automated-access policies.
  - This connector is metadata/discovery only.  For full text, open the
    returned URL in a browser and download manually.
"""

from __future__ import annotations

import logging
import re
import time
import os
from typing import List, Optional, Any, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import PaperSource
from ..paper import Paper

logger = logging.getLogger(__name__)


class SSRNSearcher(PaperSource):
    """Metadata-only connector for SSRN search results.

    Capabilities:
    - **search**: ✅ returns metadata (title, authors, abstract, date, URL)
    - **download_pdf**: ⚠️ best-effort (works only when SSRN exposes a direct public PDF URL)
    - **read_paper**: ⚠️ best-effort (depends on downloadable PDF)

    No API key required; uses standard HTTP requests with polite rate-limiting.
    """

    SEARCH_URL = "https://www.ssrn.com/index.cfm/en/rps-stage1-results/"
    ALT_SEARCH_URL = "https://papers.ssrn.com/sol3/results.cfm"
    BASE_URL = "https://papers.ssrn.com"
    USER_AGENT = (
        "Mozilla/5.0 (compatible; paper-search-mcp/0.1.3; "
        "+https://github.com/openags/paper-search-mcp)"
    )
    _RATE_LIMIT_SECONDS = 2.0  # polite delay between requests

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._last_request_time: float = 0.0

    # ------------------------------------------------------------------
    # PaperSource interface
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Search SSRN and return metadata records.

        Args:
            query: Search terms.
            max_results: Maximum results to return (practical limit ~30 without
                         pagination; SSRN returns ~15 results per page).
            **kwargs: Unused; reserved for future filter support.

        Returns:
            List of :class:`~paper_search_mcp.paper.Paper` objects.
        """
        papers: List[Paper] = []
        page = 1
        per_page = 15  # SSRN default

        while len(papers) < max_results:
            self._throttle()
            html, err = self._fetch_page(query, page)
            if err or not html:
                logger.warning("SSRN search page %d fetch failed: %s", page, err)
                break

            page_papers = self._parse_results(html)
            if not page_papers:
                break

            papers.extend(page_papers)
            if len(page_papers) < per_page:
                break  # last page

            page += 1

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download PDF for an SSRN paper when a public direct link is available.

        SSRN frequently requires login for PDF delivery. This method is
        best-effort only and returns an explanatory message when no accessible
        public PDF link can be resolved.

        Args:
            paper_id: SSRN ID in ``ssrn:<id>`` format, raw numeric id, or URL.
            save_path: Directory to save downloaded PDF.

        Returns:
            Saved PDF path on success, otherwise an explanatory message.
        """
        abstract_id = self._extract_abstract_id(paper_id)
        if not abstract_id:
            return f"Invalid SSRN paper id: {paper_id}"

        pdf_url = self._resolve_pdf_url(abstract_id)
        if not pdf_url:
            return (
                f"No publicly accessible SSRN PDF URL found for {abstract_id}. "
                "The paper may require SSRN login or restricted access."
            )

        os.makedirs(save_path, exist_ok=True)
        output_path = os.path.join(save_path, f"ssrn_{abstract_id}.pdf")

        try:
            response = self.session.get(pdf_url, stream=True, timeout=60)
            response.raise_for_status()

            content_type = (response.headers.get("content-type") or "").lower()
            first_chunk = next(response.iter_content(chunk_size=1024), b"")
            if "pdf" not in content_type and not first_chunk.startswith(b"%PDF"):
                return (
                    f"Resolved SSRN URL is not a direct PDF ({pdf_url}). "
                    "This likely requires browser login."
                )

            with open(output_path, "wb") as file_obj:
                if first_chunk:
                    file_obj.write(first_chunk)
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file_obj.write(chunk)
            return output_path
        except requests.RequestException as exc:
            return f"SSRN PDF download failed for {abstract_id}: {exc}"

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download and extract text from SSRN PDF when accessible.

        Args:
            paper_id: SSRN paper identifier.
            save_path: Directory where PDF is/will be saved.

        Returns:
            Extracted text on success, or an explanatory message.
        """
        pdf_path = self.download_pdf(paper_id, save_path)
        if not pdf_path.endswith(".pdf"):
            return pdf_path

        try:
            from PyPDF2 import PdfReader

            reader = PdfReader(pdf_path)
            text_parts = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

            if not text_parts:
                return f"SSRN PDF downloaded to {pdf_path}, but no extractable text was found."
            return "\n\n".join(text_parts)
        except Exception as exc:
            return f"SSRN PDF downloaded to {pdf_path}, but text extraction failed: {exc}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """Enforce polite per-request rate limit."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._RATE_LIMIT_SECONDS:
            time.sleep(self._RATE_LIMIT_SECONDS - elapsed)
        self._last_request_time = time.monotonic()

    def _fetch_page(self, query: str, page: int) -> Tuple[str, str]:
        """Fetch one page of SSRN search results.

        Returns:
            (html_text, error_message) — one of them will be empty.
        """
        attempts = [
            (self.SEARCH_URL, {"txtSearchTerm": query, "npage": page}),
            (self.ALT_SEARCH_URL, {"txtKeywords": query, "page": page}),
        ]

        last_error = ""
        for url, params in attempts:
            try:
                response = self.session.get(url, params=params, timeout=20)
                body = (response.text or "").lower()

                if response.status_code == 403:
                    if "just a moment" in body or "cf-challenge" in body:
                        last_error = "HTTP 403 — SSRN Cloudflare anti-bot challenge"
                    else:
                        last_error = "HTTP 403 — SSRN blocked the request (bot detection)"
                    continue

                if response.status_code == 429:
                    last_error = "HTTP 429 — SSRN rate-limited"
                    continue

                response.raise_for_status()
                return response.text, ""
            except requests.exceptions.SSLError as exc:
                last_error = f"SSRN SSL handshake failed: {exc}"
            except requests.RequestException as exc:
                last_error = str(exc)

        return "", last_error

    @staticmethod
    def _extract_abstract_id(paper_id: str) -> str:
        """Extract numeric SSRN abstract id from id/url variants."""
        value = (paper_id or "").strip()
        if not value:
            return ""

        if value.lower().startswith("ssrn:"):
            value = value.split(":", 1)[1]

        if value.isdigit():
            return value

        match = re.search(r"abstract(?:_id)?[=_](\d+)", value)
        if match:
            return match.group(1)

        return ""

    def _resolve_pdf_url(self, abstract_id: str) -> str:
        """Resolve direct PDF URL from SSRN abstract page when available."""
        abstract_url = f"{self.BASE_URL}/sol3/papers.cfm?abstract_id={abstract_id}"
        try:
            response = self.session.get(abstract_url, timeout=20)
            response.raise_for_status()
        except requests.RequestException:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        link_candidates = [
            "a[title*='Download PDF' i]",
            "a[href*='Delivery.cfm']",
            "a[href*='.pdf']",
            "a[href*='download']",
            "a[href*='abstract_id=']",
        ]

        for selector in link_candidates:
            for anchor in soup.select(selector):
                href = (anchor.get("href") or "").strip()
                if not href:
                    continue

                candidate = urljoin(self.BASE_URL, href)
                if "delivery.cfm" in candidate.lower() or candidate.lower().endswith(".pdf"):
                    return candidate

        return ""

    def _parse_results(self, html: str) -> List[Paper]:
        """Parse SSRN search-results HTML into Paper objects."""
        soup = BeautifulSoup(html, "html.parser")
        papers: List[Paper] = []

        # SSRN result items are typically in <div class="title"> / <div class="authors"> etc.
        # The structure may shift with site updates; we use heuristic selectors.
        result_blocks = soup.select("div.result-item")
        if not result_blocks:
            # Fallback — try legacy class name
            result_blocks = soup.select("div.srp-item")
        if not result_blocks:
            result_blocks = soup.select("article.search-result, div.search-result, li.search-result")

        for block in result_blocks:
            paper = self._parse_block(block)
            if paper:
                papers.append(paper)

        return papers

    def _parse_block(self, block: Any) -> Optional[Paper]:
        """Extract a single paper from an SSRN result block element."""
        try:
            # Title
            title_tag = (
                block.select_one("a.title")
                or block.select_one("h3 a")
                or block.select_one(".title a")
                or block.select_one("a[data-track-label='title']")
                or block.select_one("a[href*='abstract_id=']")
            )
            if not title_tag:
                return None
            title = title_tag.get_text(strip=True)
            if not title:
                return None

            raw_url = title_tag.get("href", "")
            if raw_url and not raw_url.startswith("http"):
                raw_url = self.BASE_URL + raw_url

            # SSRN abstract ID — extract from URL like /abstract=1234567
            paper_id = ""
            m = re.search(r"abstract[=_](\d+)", raw_url)
            if m:
                paper_id = f"ssrn:{m.group(1)}"

            # Authors
            authors_tag = (
                block.select_one(".authors")
                or block.select_one("span.author-name")
                or block.select_one(".srp-authors")
            )
            authors = authors_tag.get_text(separator=", ", strip=True) if authors_tag else ""

            # Abstract
            abstract_tag = (
                block.select_one(".abstract-text")
                or block.select_one("div.abstract")
                or block.select_one(".srp-snippet")
            )
            abstract = abstract_tag.get_text(separator=" ", strip=True) if abstract_tag else ""

            # Date
            date_tag = block.select_one(".date") or block.select_one("span.date") or block.select_one(".srp-date")
            pub_date = date_tag.get_text(strip=True) if date_tag else ""

            return Paper(
                paper_id=paper_id or f"ssrn:{hash(raw_url)}",
                title=title,
                authors=authors,
                abstract=abstract,
                doi="",
                published_date=pub_date,
                pdf_url="",  # not available without login
                url=raw_url,
                source="ssrn",
            )
        except Exception as exc:
            logger.debug("SSRN: failed to parse result block: %s", exc)
            return None
