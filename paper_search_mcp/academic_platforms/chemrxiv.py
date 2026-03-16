# paper_search_mcp/academic_platforms/chemrxiv.py
"""Searcher for ChemRxiv chemistry preprint server.

ChemRxiv (Chemical Preprint Server) is a free submission, distribution,
and archive service for unpublished preprints in chemistry and related fields.

This searcher uses the Crossref API filtered for ChemRxiv preprints.
"""

from typing import List, Optional
import logging
from .crossref import CrossRefSearcher
from ..paper import Paper

logger = logging.getLogger(__name__)


class ChemRxivSearcher(CrossRefSearcher):
    """Searcher for ChemRxiv chemistry preprints."""

    def __init__(self):
        """Initialize ChemRxiv searcher."""
        super().__init__()
        self.preprint_server = "chemrxiv"
        self.server_name = "ChemRxiv"
        self.server_url = "https://chemrxiv.org"

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Search ChemRxiv for chemistry preprints.

        Args:
            query: Search query string
            max_results: Maximum number of results to return
            **kwargs: Additional parameters:
                - year: Filter by year
                - author: Filter by author
                - subject: Filter by subject area

        Returns:
            List of Paper objects from ChemRxiv
        """
        # Build Crossref filter for ChemRxiv preprints
        base_filter = f"type:posted-content,from-publisher:{self.preprint_server}"

        # Add subject filter for chemistry if not already specified
        if 'subject' not in kwargs:
            # Chemistry-related subjects
            chemistry_subjects = [
                'chemistry', 'chemical', 'biochemistry', 'organic chemistry',
                'inorganic chemistry', 'physical chemistry', 'analytical chemistry'
            ]
            # But we'll let Crossref handle subject filtering
            pass

        # Update kwargs with ChemRxiv-specific filter
        if 'filter' in kwargs:
            kwargs['filter'] = f"{kwargs['filter']},{base_filter}"
        else:
            kwargs['filter'] = base_filter

        # Call parent Crossref search
        papers = super().search(query, max_results, **kwargs)

        # Add ChemRxiv-specific metadata
        for paper in papers:
            paper.source = 'chemrxiv'
            if not paper.extra:
                paper.extra = {}
            paper.extra['preprint_server'] = self.preprint_server
            paper.extra['server_name'] = self.server_name
            paper.extra['server_url'] = self.server_url

            # Ensure URL points to ChemRxiv if possible
            if not paper.url or 'chemrxiv' not in paper.url:
                if paper.doi:
                    # Try to construct ChemRxiv URL from DOI
                    paper.url = f"https://doi.org/{paper.doi}"
                # Crossref should already provide publisher URLs

        return papers

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """Download PDF for a ChemRxiv preprint.

        Args:
            paper_id: DOI or ChemRxiv identifier
            save_path: Directory to save PDF

        Returns:
            Path to saved PDF file

        Raises:
            NotImplementedError: If PDF cannot be downloaded
        """
        # Try parent method first (uses Crossref links)
        try:
            return super().download_pdf(paper_id, save_path)
        except Exception as e:
            logger.warning(f"Crossref download failed: {e}")

        # Try ChemRxiv-specific approach
        # ChemRxiv PDFs are typically at: https://chemrxiv.org/engage/chemrxiv/article-details/{id}
        # But we need the article ID

        # Search for the paper first
        papers = self.search(paper_id, max_results=1)
        if not papers:
            raise ValueError(f"ChemRxiv preprint not found: {paper_id}")

        paper = papers[0]
        if paper.pdf_url:
            import os
            import requests
            response = self.session.get(paper.pdf_url, timeout=30)
            response.raise_for_status()
            os.makedirs(save_path, exist_ok=True)

            # Create safe filename
            safe_id = paper_id.replace('/', '_').replace(':', '_')
            filename = f"chemrxiv_{safe_id}.pdf"
            output_file = os.path.join(save_path, filename)

            with open(output_file, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded PDF to {output_file}")
            return output_file

        raise NotImplementedError(
            f"No PDF available for ChemRxiv preprint: {paper_id}"
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read preprint text from PDF.

        Args:
            paper_id: Paper identifier
            save_path: Directory where PDF is/will be saved

        Returns:
            Extracted text content

        Raises:
            NotImplementedError: If PDF cannot be read
        """
        try:
            return super().read_paper(paper_id, save_path)
        except Exception as e:
            logger.error(f"Error reading ChemRxiv preprint {paper_id}: {e}")
            raise NotImplementedError(
                f"Cannot read preprint from ChemRxiv: {e}"
            )


if __name__ == "__main__":
    """Test the ChemRxivSearcher."""
    import logging
    logging.basicConfig(level=logging.INFO)

    searcher = ChemRxivSearcher()

    # Test search
    print("Testing ChemRxiv search...")

    # Chemistry-related queries
    test_queries = [
        "catalysis",
        "organic synthesis",
        "nanomaterials",
        "protein structure"
    ]

    for query in test_queries[:1]:  # Test first query only
        print(f"\nSearching ChemRxiv for: '{query}'")
        papers = searcher.search(query, max_results=3)
        print(f"Found {len(papers)} preprints")
        for i, paper in enumerate(papers):
            print(f"{i+1}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors[:3])}")
            print(f"   Year: {paper.published_date.year if paper.published_date else 'Unknown'}")
            print(f"   DOI: {paper.doi}")
            print(f"   PDF: {'Yes' if paper.pdf_url else 'No'}")
            print()