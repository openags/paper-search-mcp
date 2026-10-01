"""SSRN (Social Science Research Network) connector for paper-search-mcp.

SSRN is Elsevier's preprint server primarily serving economics, law, business,
and social sciences.  SSRN does not offer a public API.  This connector uses
**OpenAlex metadata API** restricted to SSRN-indexed locations for discovery.
Legacy HTML parsing helpers remain for compatibility but search does not call
them or fall back to HTML when OpenAlex fails.

Public full-text access is best-effort and does not sign in or bypass access
controls. A discovered locator does not guarantee a downloadable PDF.
"""

from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Any, Tuple
from urllib.parse import urljoin, urlparse, parse_qs
from pathlib import Path

from .public_pdf import save_verified_pdf, read_pdf_text

import requests
from bs4 import BeautifulSoup

from .base import PaperSource
from ..paper import Paper

logger = logging.getLogger(__name__)


class _PublicOnlyAuth(requests.auth.AuthBase):
    def __call__(self, request):
        # Do not automatically use an SSRN account from .netrc.
        request.headers.pop("Authorization", None)
        return request


class SSRNSearcher(PaperSource):
    """SSRN discovery with separately validated public PDF retrieval.

    Capabilities:
    - **search**: ✅ returns metadata (title, authors, abstract, date, URL)
    - **download_pdf**: ⚠️ best-effort (works only when SSRN exposes a direct public PDF URL)
    - **read_paper**: ⚠️ best-effort (depends on downloadable PDF)

    Search uses OpenAlex and honors existing OpenAlex configuration.
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
        self.session.auth = _PublicOnlyAuth()
        self.session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._last_request_time: float = 0.0
        self._pdf_titles: dict[str, str] = {}

    # ------------------------------------------------------------------
    # PaperSource interface
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Discover SSRN metadata via OpenAlex, without SSRN HTML fallback.

        See OpenAlexSearcher.search_ssrn for cursor limits and error behavior.
        Existing OpenAlex key/email configuration is honored; no account is
        created and no key is required by this connector itself.
        """
        from .openalex import OpenAlexSearcher
        return OpenAlexSearcher().search_ssrn(query, max_results=max_results)

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

        try:
            response = self.session.get(pdf_url, stream=True, timeout=60, allow_redirects=False)
        except requests.RequestException:
            raise RuntimeError("SSRN PDF request failed (network or timeout)") from None
        with response:
            if response.status_code != 200:
                raise RuntimeError(f"SSRN public PDF returned HTTP {response.status_code}; no login or redirect fallback is attempted")
            title = self._pdf_titles.get(abstract_id, "")
            return save_verified_pdf(response, Path(save_path) / f"ssrn_{abstract_id}.pdf", title)

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

        return read_pdf_text(pdf_path)

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

        if re.fullmatch(r"[0-9]+", value):
            return value

        from .openalex import OpenAlexSearcher
        return OpenAlexSearcher._extract_ssrn_abstract_id(value)


    def _resolve_pdf_url(self, abstract_id: str) -> str:
        """Resolve a public SSRN PDF only from an identity-verified paper page."""
        abstract_url = f"{self.BASE_URL}/sol3/papers.cfm?abstract_id={abstract_id}"
        self._pdf_titles.pop(abstract_id, None)
        try:
            response = self.session.get(abstract_url, timeout=20, allow_redirects=False)
        except requests.RequestException:
            raise RuntimeError("SSRN paper page request failed (network or timeout)") from None
        if response.status_code != 200:
            raise RuntimeError(f"SSRN paper page returned HTTP {response.status_code}; public access is unavailable")
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.find("meta", attrs={"name": "citation_title"})
        canonical = soup.find("link", rel="canonical")
        citation_url = soup.find("meta", attrs={"name": "citation_abstract_html_url"})
        identity_urls = [tag.get(attr, "") for tag, attr in ((canonical, "href"), (citation_url, "content")) if tag]
        if (not title or not title.get("content") or not identity_urls
                or any(self._extract_abstract_id(urljoin(abstract_url, url)) != abstract_id for url in identity_urls)):
            raise RuntimeError("SSRN paper page identity could not be verified")
        candidates = []
        pdf_meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
        if pdf_meta:
            candidates.append(pdf_meta.get("content", ""))
        candidates.extend(anchor.get("href", "") for anchor in soup.select("a[href*='Delivery.cfm'], a[href$='.pdf']"))
        for href in candidates:
            candidate = urljoin(abstract_url, href)
            try:
                parsed = urlparse(candidate)
                if (parsed.scheme != "https" or parsed.hostname != "papers.ssrn.com"
                        or parsed.username or parsed.password or parsed.port
                        or not ("/delivery.cfm" in parsed.path.lower() or parsed.path.lower().endswith(".pdf"))):
                    continue
                ids = parse_qs(parsed.query).get("abstract_id", []) + parse_qs(parsed.query).get("abstractid", [])
                if ids and any(value != abstract_id for value in ids):
                    continue
            except ValueError:
                continue
            self._pdf_titles[abstract_id] = title["content"]
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
