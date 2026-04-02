from typing import Optional, Dict, Any, List
from datetime import datetime
import logging
import requests
from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource

logger = logging.getLogger(__name__)


class UnpaywallResolver:
    """Resolve open-access links using the Unpaywall API."""

    BASE_URL = "https://api.unpaywall.org/v2"
    USER_AGENT = "paper-search-mcp/0.1.3 (https://github.com/openags/paper-search-mcp)"

    def __init__(self, email: Optional[str] = None):
        configured_email = get_env("UNPAYWALL_EMAIL", "") if email is None else email
        self.email = configured_email.strip()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": "application/json",
        })

        if not self.email:
            logger.warning(
                "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL/UNPAYWALL_EMAIL not configured. "
                "Unpaywall fallback will be skipped. "
                "You can request free access at https://unpaywall.org/products/api"
            )

    def resolve_best_pdf_url(self, doi: str) -> Optional[str]:
        """Return the best available OA PDF URL for a DOI."""
        if not doi:
            return None

        if not self.email:
            return None

        normalized_doi = doi.strip()
        if not normalized_doi:
            return None

        try:
            data = self._fetch_doi_record(normalized_doi)
            if not data:
                return None

            best_location = data.get("best_oa_location") or {}
            best_pdf = best_location.get("url_for_pdf") or best_location.get("url")
            if best_pdf:
                return best_pdf

            for location in data.get("oa_locations", []) or []:
                if not isinstance(location, dict):
                    continue
                candidate = location.get("url_for_pdf") or location.get("url")
                if candidate:
                    return candidate

        except requests.RequestException as exc:
            logger.warning("Unpaywall request failed for DOI %s: %s", doi, exc)
        except Exception as exc:
            logger.warning("Unexpected Unpaywall resolver error for DOI %s: %s", doi, exc)

        return None

    def get_paper_by_doi(self, doi: str) -> Optional[Paper]:
        """Fetch Unpaywall metadata by DOI and map it to a Paper object.

        Args:
            doi: DOI string.

        Returns:
            Paper instance when record exists, otherwise None.
        """
        if not self.email:
            return None

        normalized_doi = (doi or "").strip()
        if not normalized_doi:
            return None

        data = self._fetch_doi_record(normalized_doi)
        if not data:
            return None

        title = (data.get("title") or "").strip()
        if not title:
            title = normalized_doi

        authors: List[str] = []
        for author in data.get("z_authors") or []:
            if not isinstance(author, dict):
                continue
            given = (author.get("given") or "").strip()
            family = (author.get("family") or "").strip()
            full_name = f"{given} {family}".strip()
            if full_name:
                authors.append(full_name)

        published_date = None
        published_date_str = (data.get("published_date") or "").strip()
        if published_date_str:
            try:
                if len(published_date_str) == 10:
                    published_date = datetime.strptime(published_date_str, "%Y-%m-%d")
                elif len(published_date_str) == 4:
                    published_date = datetime.strptime(published_date_str, "%Y")
            except ValueError:
                published_date = None

        best_location = data.get("best_oa_location") or {}
        landing_url = (
            best_location.get("url")
            or data.get("doi_url")
            or f"https://doi.org/{normalized_doi}"
        )
        pdf_url = best_location.get("url_for_pdf") or self.resolve_best_pdf_url(normalized_doi) or ""

        abstract = ""
        is_oa = bool(data.get("is_oa"))

        return Paper(
            paper_id=f"unpaywall:{normalized_doi}",
            title=title,
            authors=authors,
            abstract=abstract,
            doi=normalized_doi,
            published_date=published_date,
            pdf_url=pdf_url,
            url=landing_url,
            source="unpaywall",
            extra={
                "is_oa": is_oa,
                "oa_status": data.get("oa_status", ""),
                "journal_name": data.get("journal_name", ""),
                "publisher": data.get("publisher", ""),
                "host_type": best_location.get("host_type", ""),
                "license": best_location.get("license", ""),
                "version": best_location.get("version", ""),
            },
        )

    def _fetch_doi_record(self, doi: str) -> Optional[Dict[str, Any]]:
        """Fetch raw Unpaywall response payload for a DOI."""
        if not self.email or not doi:
            return None

        try:
            response = self.session.get(
                f"{self.BASE_URL}/{doi}",
                params={"email": self.email},
                timeout=20,
            )

            if response.status_code == 404:
                return None

            if response.status_code == 422:
                logger.warning(
                    "Unpaywall rejected the configured email (%s) with HTTP 422. "
                    "Please use a valid contact email in PAPER_SEARCH_MCP_UNPAYWALL_EMAIL/UNPAYWALL_EMAIL.",
                    self.email,
                )
                return None

            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.warning("Unpaywall request failed for DOI %s: %s", doi, exc)
            return None
        except Exception as exc:
            logger.warning("Unexpected Unpaywall record fetch error for DOI %s: %s", doi, exc)
            return None

    def has_api_access(self) -> bool:
        """Quick capability check based on required configuration."""
        return bool(self.email)


class UnpaywallSearcher(PaperSource):
    """DOI-centric Unpaywall connector exposed as a PaperSource.

    Unpaywall is metadata and OA-location provider. It does not host PDFs.
    """

    def __init__(self, email: Optional[str] = None, resolver: Optional[UnpaywallResolver] = None):
        self.resolver = resolver or UnpaywallResolver(email=email)

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Lookup a DOI in Unpaywall and return at most one Paper.

        Args:
            query: DOI string or text containing DOI.
            max_results: Kept for interface compatibility; Unpaywall returns max 1.
            **kwargs: Reserved for future use.
        """
        if not self.resolver.has_api_access():
            logger.warning(
                "Unpaywall search skipped: missing PAPER_SEARCH_MCP_UNPAYWALL_EMAIL/UNPAYWALL_EMAIL."
            )
            return []

        doi = extract_doi(query) or (query.strip() if query.strip().startswith("10.") else "")
        if not doi:
            return []

        paper = self.resolver.get_paper_by_doi(doi)
        if not paper:
            return []

        return [paper]

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        raise NotImplementedError(
            "Unpaywall does not host PDFs directly. "
            "Use the returned pdf_url/url with download_with_fallback or source-native download tools."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        raise NotImplementedError(
            "Unpaywall provides metadata and OA links only; it does not provide direct paper text reading."
        )
