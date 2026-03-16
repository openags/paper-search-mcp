from typing import List, Optional
from datetime import datetime
import requests
import logging
from ..paper import Paper
from .base import PaperSource
from ..utils import extract_doi

logger = logging.getLogger(__name__)


class OpenAlexSearcher(PaperSource):
    """OpenAlex paper search implementation"""

    BASE_URL = "https://api.openalex.org/works"

    def __init__(self):
        self.session = requests.Session()
        # OpenAlex encourages providing an email in User-Agent for the "polite pool"
        self.session.headers.update(
            {"User-Agent": "paper-search-mcp/1.0 (mailto:openags@example.com)"}
        )

    def _reconstruct_abstract(self, inverted_index: dict) -> str:
        """
        OpenAlex provides abstracts as an inverted index to save space.
        This function reconstructs the original abstract text.
        """
        if not inverted_index:
            return ""
        try:
            word_positions = []
            for word, positions in inverted_index.items():
                for pos in positions:
                    word_positions.append((pos, word))
            # Sort by position
            word_positions.sort(key=lambda x: x[0])
            return " ".join([word for _, word in word_positions])
        except Exception as e:
            logger.warning(f"Error reconstructing OpenAlex abstract: {e}")
            return ""

    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        """
        Search OpenAlex works. Uses the 'search' filter.

        Args:
            query: Search query string
            max_results: Maximum results to return (natively max 200 per page)

        Returns:
            List[Paper]: List of found papers with metadata.
        """
        papers = []

        try:
            params = {
                "search": query,
                "per_page": min(max_results, 200),
            }

            response = self.session.get(self.BASE_URL, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"OpenAlex search failed with status {response.status_code}")
                return papers

            data = response.json()
            results = data.get("results", [])

            for item in results:
                if len(papers) >= max_results:
                    break

                # ID usually looks like 'https://openalex.org/W2741809807'
                paper_id = item.get("id", "").replace("https://openalex.org/", "")
                title = item.get("title")
                if not title:
                    continue  # Skip items without a title

                # Process Authors
                authors = [
                    author.get("author", {}).get("display_name", "")
                    for author in item.get("authorships", [])
                    if author.get("author", {}).get("display_name")
                ]

                # Abstract
                abstract = self._reconstruct_abstract(
                    item.get("abstract_inverted_index")
                )

                # Process DOI
                doi = item.get("doi", "")
                if doi:
                    # OpenAlex DOI is returned as a full url e.g. https://doi.org/10...
                    doi = doi.replace("https://doi.org/", "")

                if not doi and abstract:
                    doi = extract_doi(abstract)

                # Process URLs (Landing page vs direct PDF)
                url = ""
                pdf_url = ""

                primary_location = item.get("primary_location")
                if primary_location:
                    url = primary_location.get("landing_page_url", "")
                    pdf_url = primary_location.get("pdf_url", "")

                if not url:
                    url = item.get("id", "")

                # Check general open access availability for PDF fallback
                open_access = item.get("open_access", {})
                if not pdf_url and open_access.get("is_oa"):
                    pdf_url = open_access.get("oa_url", "")

                # Dates
                pub_date_str = item.get("publication_date")
                published_date = None
                if pub_date_str:
                    try:
                        published_date = datetime.strptime(pub_date_str, "%Y-%m-%d")
                    except ValueError:
                        pass

                # Categories / Concepts
                concepts = [
                    concept.get("display_name")
                    for concept in item.get("concepts", [])
                    if concept.get("display_name")
                ]

                papers.append(
                    Paper(
                        paper_id=paper_id,
                        title=title,
                        authors=authors,
                        abstract=abstract,
                        url=url,
                        pdf_url=pdf_url or "",
                        published_date=published_date,
                        source="openalex",
                        categories=concepts[:5],  # Keep top 5 concepts to reduce size
                        doi=doi,
                        citations=item.get("cited_by_count", 0),
                    )
                )

        except Exception as e:
            logger.error(f"OpenAlex search error: {e}")

        return papers

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        OpenAlex does not host PDFs natively, it only links to open access versions.
        """
        raise NotImplementedError(
            "OpenAlex does not provide direct PDF downloads natively. "
            "Please use the extracted 'pdf_url' if available, or DOI for fallback."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Not implemented for OpenAlex.
        """
        return (
            "OpenAlex papers cannot be read directly through this aggregator. "
            "Please use the paper's DOI or pdf_url to access the full text."
        )


if __name__ == "__main__":
    searcher = OpenAlexSearcher()
    print("Testing OpenAlex search...")
    papers = searcher.search("CRISPR Cas9 Nature", max_results=3)

    for i, paper in enumerate(papers, 1):
        print(f"\n{i}. {paper.title}")
        print(f"   DOI: {paper.doi}")
        print(f"   URL: {paper.url}")
        print(f"   OA PDF: {paper.pdf_url}")
        print(f"   Citations: {paper.citations}")
        print(f"   Authors: {', '.join(paper.authors[:3])}")
        if paper.abstract:
            print(f"   Abstract: {paper.abstract[:100]}...")
