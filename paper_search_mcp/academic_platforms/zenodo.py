"""Zenodo connector for paper-search-mcp.

Zenodo is a CERN-operated open-access repository that accepts all research
outputs across all fields of science.  Its public REST API does NOT require
an API key for read (search) operations.

API docs: https://developers.zenodo.org/
"""

from __future__ import annotations

import logging
from typing import List, Optional, Dict, Any

import requests

from .base import PaperSource
from ..paper import Paper
from ..config import get_env

logger = logging.getLogger(__name__)


class ZenodoSearcher(PaperSource):
    """Search and discover papers on Zenodo via its public REST API.

    Search is always available without an API key.
    Setting ``ZENODO_ACCESS_TOKEN`` enables higher rate-limits and access to
    restricted / embargoed records that a user has permission to read.
    """

    BASE_URL = "https://zenodo.org/api"
    USER_AGENT = "paper-search-mcp/0.1.3 (https://github.com/openags/paper-search-mcp)"

    def __init__(self, access_token: Optional[str] = None) -> None:
        self.access_token = access_token or get_env("ZENODO_ACCESS_TOKEN", "")
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": self.USER_AGENT, "Accept": "application/json"}
        )
        if self.access_token:
            self.session.headers.update(
                {"Authorization": f"Bearer {self.access_token}"}
            )
            logger.info("Zenodo: using access token for authenticated requests.")
        else:
            logger.debug(
                "Zenodo: no access token configured — using public API (rate-limited)."
            )

    # ------------------------------------------------------------------
    # PaperSource interface
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Search Zenodo records.

        Args:
            query: Free-text or Elasticsearch query string.
            max_results: Maximum number of results (1–200; Zenodo page limit is 10k).
            **kwargs: Extra filters:
                - ``type``: Record type, e.g. ``"publication"`` (default), ``"dataset"``,
                  ``"image"``, ``"video"``, ``"software"``, ``"poster"``, etc.
                - ``subtype``: Publication subtype, e.g. ``"article"``, ``"preprint"``.
                - ``year``: Filter by publication year (e.g. ``2023``).
                - ``access_right``: ``"open"``, ``"embargoed"``, ``"restricted"``, ``"closed"``.

        Returns:
            List of :class:`~paper_search_mcp.paper.Paper` objects.
        """
        max_results = max(1, min(max_results, 200))

        params: Dict[str, Any] = {
            "q": query,
            "size": max_results,
            "sort": "mostrecent",
        }

        record_type = kwargs.get("type", "publication")
        if record_type:
            params["type"] = record_type

        subtype = kwargs.get("subtype", "")
        if subtype:
            params["subtype"] = subtype

        access_right = kwargs.get("access_right", "")
        if access_right:
            params["access_right"] = access_right

        year = kwargs.get("year")
        if year:
            params["q"] = f"{query} AND publication_date:[{year}-01-01 TO {year}-12-31]"

        try:
            response = self.session.get(
                f"{self.BASE_URL}/records", params=params, timeout=20
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.error("Zenodo search failed: %s", exc)
            return []

        papers: List[Paper] = []
        for hit in data.get("hits", {}).get("hits", []):
            paper = self._parse_record(hit)
            if paper:
                papers.append(paper)

        return papers

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download an open-access PDF from Zenodo.

        Args:
            paper_id: Zenodo record ID (numeric string or full DOI ``10.5281/zenodo.NNNNNN``).
            save_path: Directory to save the PDF.

        Returns:
            Absolute path to the saved PDF, or an error message.
        """
        import re
        import os

        record_id = self._extract_record_id(paper_id)
        if not record_id:
            return f"Could not determine Zenodo record ID from: {paper_id}"

        try:
            response = self.session.get(
                f"{self.BASE_URL}/records/{record_id}", timeout=20
            )
            response.raise_for_status()
            record = response.json()
        except Exception as exc:
            return f"Failed to fetch Zenodo record {record_id}: {exc}"

        pdf_url = self._find_pdf_url(record)
        if not pdf_url:
            return (
                f"No open-access PDF found for Zenodo record {record_id}.  "
                "The record may be embargoed or restricted."
            )

        os.makedirs(save_path, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", record_id) or record_id
        output_path = os.path.join(save_path, f"zenodo_{safe_name}.pdf")

        try:
            dl_response = self.session.get(pdf_url, stream=True, timeout=60)
            dl_response.raise_for_status()
            with open(output_path, "wb") as fh:
                for chunk in dl_response.iter_content(chunk_size=8192):
                    fh.write(chunk)
            return output_path
        except Exception as exc:
            return f"Failed to download PDF from {pdf_url}: {exc}"

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download and extract text from a Zenodo PDF.

        Args:
            paper_id: Zenodo record ID or DOI.
            save_path: Directory where the PDF is/will be saved.

        Returns:
            Extracted text content or error message.
        """
        path = self.download_pdf(paper_id, save_path)
        if not path.endswith(".pdf"):
            return path  # error message

        try:
            from pypdf import PdfReader

            reader = PdfReader(path)
            text_parts = [
                page.extract_text() for page in reader.pages if page.extract_text()
            ]
            return (
                "\n\n".join(text_parts) if text_parts else "No extractable text in PDF."
            )
        except ImportError:
            return f"PDF downloaded to {path}. Install 'pypdf' to extract text."
        except Exception as exc:
            return f"PDF downloaded to {path} but text extraction failed: {exc}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_record_id(paper_id: str) -> str:
        """Return the raw numeric Zenodo record ID from a DOI or bare ID."""
        import re

        paper_id = paper_id.strip()
        if paper_id.lower().startswith("zenodo:"):
            candidate = paper_id.split(":", 1)[1].strip()
            if re.fullmatch(r"\d+", candidate):
                return candidate

        # doi format: 10.5281/zenodo.1234567
        m = re.search(r"zenodo\.(\d+)", paper_id, re.IGNORECASE)
        if m:
            return m.group(1)
        # bare numeric ID
        if re.fullmatch(r"\d+", paper_id):
            return paper_id
        return ""

    def _find_pdf_url(self, record: Dict[str, Any]) -> str:
        """Return the best open-access PDF URL from a Zenodo record dict."""
        for f in record.get("files", []):
            key = f.get("key", "")
            if key.lower().endswith(".pdf"):
                # Zenodo file link format (REST API v1)
                links = f.get("links", {})
                return links.get("self", "") or links.get("download", "")
        return ""

    def _parse_record(self, hit: Dict[str, Any]) -> Optional[Paper]:
        """Convert a Zenodo API hit dict into a :class:`Paper`."""
        try:
            meta = hit.get("metadata", {})
            record_id = str(hit.get("id", ""))
            doi = hit.get("doi", "") or meta.get("doi", "")

            title = meta.get("title", "").strip()
            if not title:
                return None

            creators = meta.get("creators", [])
            authors = ", ".join(
                c.get("name", "")
                or f"{c.get('given_name', '')} {c.get('family_name', '')}".strip()
                for c in creators
            )

            abstract = (meta.get("description") or "").strip()
            # Zenodo descriptions can contain HTML — strip tags minimally
            import re

            abstract = re.sub(r"<[^>]+>", " ", abstract).strip()

            pub_date = meta.get("publication_date", "")
            if len(pub_date) >= 4:
                pub_date = pub_date[:10]  # keep YYYY-MM-DD

            # Pick the best available PDF url from top-level links
            pdf_url = ""
            for f in hit.get("files", []):
                if f.get("key", "").lower().endswith(".pdf"):
                    links = f.get("links", {})
                    pdf_url = links.get("self", "") or links.get("download", "")
                    break

            record_url = hit.get("links", {}).get(
                "html", f"https://zenodo.org/record/{record_id}"
            )

            return Paper(
                paper_id=f"zenodo:{record_id}",
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=pub_date,
                pdf_url=pdf_url,
                url=record_url,
                source="zenodo",
            )
        except Exception as exc:
            logger.debug("Zenodo: failed to parse record %s: %s", hit.get("id"), exc)
            return None
