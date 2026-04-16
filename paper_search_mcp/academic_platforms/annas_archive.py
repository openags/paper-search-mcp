"""Anna's Archive downloader integration."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


class AnnasArchiveFetcher:
    """Best-effort PDF downloader via Anna's Archive search and file pages."""

    def __init__(self, base_url: str = "https://annas-archive.org", output_dir: str = "./downloads"):
        self.base_url = base_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; paper-search-mcp/0.1.4; +https://github.com/openags/paper-search-mcp)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def download_pdf(self, identifier: str) -> Optional[str]:
        identifier = (identifier or "").strip()
        if not identifier:
            return None

        if identifier.lower().startswith(("http://", "https://")) and identifier.lower().endswith(".pdf"):
            return self._download_url(identifier, identifier)

        file_page_url = self._find_file_page(identifier)
        if not file_page_url:
            return None

        pdf_url = self._extract_pdf_url(file_page_url)
        if not pdf_url:
            return None

        return self._download_url(pdf_url, identifier)

    def _find_file_page(self, identifier: str) -> str:
        search_url = f"{self.base_url}/search?q={quote_plus(identifier)}"
        try:
            response = self.session.get(search_url, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Anna's Archive search failed for '%s': %s", identifier, exc)
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if re.search(r"/md5/[0-9a-fA-F]{32}", href):
                return urljoin(self.base_url, href)
        return ""

    def _extract_pdf_url(self, file_page_url: str) -> str:
        try:
            response = self.session.get(file_page_url, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Anna's Archive file page request failed '%s': %s", file_page_url, exc)
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            lowered = href.lower()
            if "torrent" in lowered:
                continue

            if ".pdf" in lowered or "download" in lowered:
                return urljoin(self.base_url, href)
        return ""

    def _download_url(self, url: str, identifier: str) -> Optional[str]:
        try:
            response = self.session.get(url, stream=True, timeout=60)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Anna's Archive download failed '%s': %s", url, exc)
            return None

        content_type = (response.headers.get("content-type") or "").lower()
        first_chunk = next(response.iter_content(chunk_size=1024), b"")
        if "pdf" not in content_type and not first_chunk.startswith(b"%PDF"):
            logger.warning("Anna's Archive URL does not look like a PDF: %s", url)
            return None

        safe_hint = re.sub(r"[^a-zA-Z0-9._-]+", "_", identifier)[:80] or "paper"
        digest = hashlib.md5((url + identifier).encode("utf-8")).hexdigest()[:8]
        output_path = os.path.join(self.output_dir, f"annas_archive_{safe_hint}_{digest}.pdf")
        with open(output_path, "wb") as fh:
            if first_chunk:
                fh.write(first_chunk)
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
        return output_path
