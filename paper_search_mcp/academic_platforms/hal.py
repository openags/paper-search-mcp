"""HAL (Hyper Articles en Ligne) connector for paper-search-mcp.

HAL is France's national open archive for academic publications across all
disciplines.  It exposes a public JSON search API (no key required) and also
supports OAI-PMH harvesting.

API docs: https://api.archives-ouvertes.fr/docs/search
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional, Dict, Any

import requests

from .base import PaperSource
from ..paper import Paper

logger = logging.getLogger(__name__)


class HALSearcher(PaperSource):
    """Search HAL open archive via its public Solr-based API.

    No API key or registration is required for read-only access.
    """

    # HAL search API (Solr interface)
    SEARCH_URL = "https://api.archives-ouvertes.fr/search/"
    USER_AGENT = "paper-search-mcp/0.1.3 (https://github.com/openags/paper-search-mcp)"

    # Fields requested from the API to minimise payload
    _FIELDS = ",".join(
        [
            "halId_s",
            "title_s",
            "authFullName_s",
            "abstract_s",
            "doiId_s",
            "publicationDateY_i",
            "producedDateY_i",
            "submittedDate_s",
            "linkExtUrl_s",
            "fileMain_s",
            "uri_s",
            "docType_s",
        ]
    )

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": self.USER_AGENT, "Accept": "application/json"}
        )

    # ------------------------------------------------------------------
    # PaperSource interface
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Search HAL open archive.

        Args:
            query: Free-text query (standard Solr query syntax supported).
            max_results: Number of results to return (max 10 000 per call).
            **kwargs: Optional filters:
                - ``year`` (int): Filter by exact publication year.
                - ``doctype`` (str): Document type, e.g. ``"ART"`` (journal article),
                  ``"COMM"`` (conference paper), ``"THESE"`` (thesis), etc.
                - ``domain`` (str): HAL science domain code, e.g. ``"spi"`` (engineering),
                  ``"math"``, ``"sdv"`` (life sciences).

        Returns:
            List of :class:`~paper_search_mcp.paper.Paper` objects.
        """
        max_results = max(1, min(max_results, 10000))

        fq_parts: List[str] = []

        year = kwargs.get("year")
        if year:
            fq_parts.append(f"publicationDateY_i:{year}")

        doctype = kwargs.get("doctype", "")
        if doctype:
            fq_parts.append(f"docType_s:{doctype}")

        domain = kwargs.get("domain", "")
        if domain:
            fq_parts.append(f"domain_s:{domain}")

        params: Dict[str, Any] = {
            "q": query,
            "fl": self._FIELDS,
            "rows": max_results,
            "wt": "json",
            "sort": "score desc",
        }
        if fq_parts:
            params["fq"] = " AND ".join(fq_parts)

        try:
            response = self.session.get(self.SEARCH_URL, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.error("HAL search failed: %s", exc)
            return []

        papers: List[Paper] = []
        for doc in data.get("response", {}).get("docs", []):
            paper = self._parse_doc(doc)
            if paper:
                papers.append(paper)

        return papers

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download an open-access PDF from HAL.

        Args:
            paper_id: HAL identifier (e.g. ``hal-01234567``) or the value
                returned in ``paper.paper_id`` (``hal:<id>``).
            save_path: Directory to save the downloaded PDF.

        Returns:
            Absolute path to saved PDF or an error message string.
        """
        import os

        hal_id = self._normalise_id(paper_id)
        pdf_url = self._resolve_pdf_url(hal_id)
        if not pdf_url:
            return (
                f"No open-access PDF found on HAL for {hal_id}. "
                "The document may be metadata-only or under embargo."
            )

        os.makedirs(save_path, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", hal_id) or hal_id
        output_path = os.path.join(save_path, f"hal_{safe_name}.pdf")

        try:
            dl_response = self.session.get(pdf_url, stream=True, timeout=60)
            dl_response.raise_for_status()
            with open(output_path, "wb") as fh:
                for chunk in dl_response.iter_content(chunk_size=8192):
                    fh.write(chunk)
            return output_path
        except Exception as exc:
            return f"Failed to download HAL PDF from {pdf_url}: {exc}"

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download and extract text from a HAL PDF.

        Args:
            paper_id: HAL paper ID.
            save_path: Directory where the PDF is/will be saved.

        Returns:
            Extracted text content or error message.
        """
        path = self.download_pdf(paper_id, save_path)
        if not path.endswith(".pdf"):
            return path

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
    def _normalise_id(paper_id: str) -> str:
        """Strip the leading 'hal:' prefix if present."""
        paper_id = paper_id.strip()
        if paper_id.lower().startswith("hal:"):
            return paper_id[4:]
        return paper_id

    def _resolve_pdf_url(self, hal_id: str) -> str:
        """Return the direct PDF download URL for a known HAL ID, if available."""
        # HAL's canonical PDF endpoint pattern
        candidate = f"https://hal.archives-ouvertes.fr/{hal_id}/document"
        try:
            head = self.session.head(candidate, allow_redirects=True, timeout=10)
            content_type = head.headers.get("content-type", "").lower()
            if head.status_code == 200 and "pdf" in content_type:
                return head.url if head.url else candidate
            # Even if content-type is not pdf, if redirect lands at a PDF URL, use it
            if head.status_code == 200:
                return candidate
        except Exception:
            pass
        return ""

    def _parse_doc(self, doc: Dict[str, Any]) -> Optional[Paper]:
        """Convert a HAL Solr document dict into a :class:`Paper`."""
        try:
            hal_id = doc.get("halId_s", "")
            if not hal_id:
                return None

            title_field = doc.get("title_s", [])
            title = title_field[0] if isinstance(title_field, list) else title_field
            title = (title or "").strip()
            if not title:
                return None

            authors_field = doc.get("authFullName_s", [])
            if isinstance(authors_field, list):
                authors = ", ".join(authors_field)
            else:
                authors = str(authors_field)

            abstract_field = doc.get("abstract_s", [])
            if isinstance(abstract_field, list):
                abstract = " ".join(abstract_field)
            else:
                abstract = str(abstract_field or "")

            doi = doc.get("doiId_s", "")
            if isinstance(doi, list):
                doi = doi[0] if doi else ""

            year = doc.get("publicationDateY_i") or doc.get("producedDateY_i", "")
            pub_date = (
                str(year) if year else (doc.get("submittedDate_s", "") or "")[:10]
            )

            pdf_url = doc.get("fileMain_s", "") or ""
            record_url = doc.get("uri_s", f"https://hal.archives-ouvertes.fr/{hal_id}")

            return Paper(
                paper_id=f"hal:{hal_id}",
                title=title,
                authors=authors,
                abstract=abstract.strip(),
                doi=doi,
                published_date=str(pub_date),
                pdf_url=pdf_url,
                url=record_url,
                source="hal",
            )
        except Exception as exc:
            logger.debug("HAL: failed to parse doc %s: %s", doc.get("halId_s"), exc)
            return None
