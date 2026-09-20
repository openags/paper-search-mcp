import logging
import os
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode

import requests
from pypdf import PdfReader

from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource

logger = logging.getLogger(__name__)


class BioRxivSearcher(PaperSource):
    """Searcher for bioRxiv papers"""

    BASE_URL = "https://api.biorxiv.org/details/biorxiv"
    DATE_RANGE_PATTERN = re.compile(
        r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:/|:|\.\.|to)\s*(\d{4}-\d{2}-\d{2})\s*$",
        re.IGNORECASE,
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.proxies = {"http": None, "https": None}
        self.timeout = 30
        self.max_retries = 3

    @staticmethod
    def _normalize_category(category: str) -> str:
        return re.sub(r"[\s-]+", "_", category.strip().lower())

    def _resolve_query_mode(
        self, query: str, days: int
    ) -> tuple[str, str, str, str | None]:
        """Resolve a query into (mode, start_or_doi, end_or_na, category)."""
        normalized_query = (query or "").strip()
        doi = extract_doi(normalized_query)
        if doi:
            return "doi", doi, "na", None

        date_match = self.DATE_RANGE_PATTERN.match(normalized_query)
        if date_match:
            start_date, end_date = date_match.groups()
            parsed_start = date.fromisoformat(start_date)
            parsed_end = date.fromisoformat(end_date)
            if parsed_start > parsed_end:
                raise ValueError("bioRxiv date range start must not be after its end")
            return "interval", start_date, end_date, None

        if days < 1:
            raise ValueError("days must be at least 1")

        today = date.today()
        end_date = today.isoformat()
        start_date = (today - timedelta(days=days)).isoformat()
        category = (
            self._normalize_category(normalized_query) if normalized_query else None
        )
        return "interval", start_date, end_date, category

    def _request_json(self, url: str) -> dict | None:
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except (requests.exceptions.RequestException, ValueError) as exc:
                if attempt == self.max_retries:
                    logger.warning(
                        "bioRxiv request failed after %d attempts: %s",
                        self.max_retries,
                        exc,
                    )
                    return None
                logger.info(
                    "bioRxiv request attempt %d failed; retrying: %s",
                    attempt,
                    exc,
                )
        return None

    @staticmethod
    def _parse_papers(collection: list) -> list[Paper]:
        papers = []
        for item in collection:
            try:
                doi = str(item.get("doi") or "").strip()
                title = str(item.get("title") or "").strip()
                if not doi or not title:
                    raise ValueError("missing DOI or title")

                published_date = datetime.combine(
                    date.fromisoformat(item["date"]),
                    time.min,
                )
                version = str(item.get("version") or "1")
                authors = [
                    author.strip()
                    for author in str(item.get("authors") or "").split(";")
                    if author.strip()
                ]
                category = str(item.get("category") or "").strip()

                papers.append(
                    Paper(
                        paper_id=doi,
                        title=title,
                        authors=authors,
                        abstract=str(item.get("abstract") or ""),
                        url=f"https://www.biorxiv.org/content/{doi}v{version}",
                        pdf_url=(
                            f"https://www.biorxiv.org/content/{doi}v{version}.full.pdf"
                        ),
                        published_date=published_date,
                        updated_date=published_date,
                        source="biorxiv",
                        categories=[category] if category else [],
                        keywords=[],
                        doi=doi,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Failed to parse bioRxiv entry: %s", exc)
        return papers

    def search(self, query: str, max_results: int = 10, days: int = 30) -> list[Paper]:
        """Search by DOI, date range, category, or recent-paper interval."""
        if max_results <= 0:
            return []

        mode, start, end, category = self._resolve_query_mode(query, days)
        if mode == "doi":
            data = self._request_json(f"{self.BASE_URL}/{start}/{end}/json")
            if not data:
                return []
            return self._parse_papers(data.get("collection") or [])[:max_results]

        papers: list[Paper] = []
        cursor = 0
        while len(papers) < max_results:
            url = f"{self.BASE_URL}/{start}/{end}/{cursor}/json"
            if category:
                url = f"{url}?{urlencode({'category': category})}"

            data = self._request_json(url)
            if not data:
                break

            collection = data.get("collection") or []
            if not collection:
                break

            papers.extend(self._parse_papers(collection))
            if len(collection) < 100:
                break
            cursor += 100

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download a PDF for a given paper ID from bioRxiv.

        Args:
            paper_id: The DOI of the paper.
            save_path: Directory to save the PDF.

        Returns:
            Path to the downloaded PDF file.
        """
        if not paper_id:
            raise ValueError("Invalid paper_id: paper_id is empty")

        pdf_url = f"https://www.biorxiv.org/content/{paper_id}v1.full.pdf"
        tries = 0
        while tries < self.max_retries:
            try:
                # Add User-Agent to avoid potential 403 errors
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                }
                response = self.session.get(
                    pdf_url, timeout=self.timeout, headers=headers
                )
                response.raise_for_status()
                os.makedirs(save_path, exist_ok=True)
                output_file = f"{save_path}/{paper_id.replace('/', '_')}.pdf"
                with open(output_file, "wb") as f:
                    f.write(response.content)
                return output_file
            except requests.exceptions.RequestException as e:
                tries += 1
                if tries == self.max_retries:
                    raise Exception(
                        f"Failed to download PDF after {self.max_retries} attempts: {e}"
                    )
                print(f"Attempt {tries} failed, retrying...")

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Read a paper and convert it to text format.

        Args:
            paper_id: bioRxiv DOI
            save_path: Directory where the PDF is/will be saved

        Returns:
            str: The extracted text content of the paper
        """
        pdf_path = f"{save_path}/{paper_id.replace('/', '_')}.pdf"
        if not os.path.exists(pdf_path):
            pdf_path = self.download_pdf(paper_id, save_path)

        try:
            reader = PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text.strip()
        except Exception as e:
            print(f"Error reading PDF for paper {paper_id}: {e}")
            return ""
