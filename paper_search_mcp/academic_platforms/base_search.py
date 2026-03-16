# paper_search_mcp/academic_platforms/base_search.py
"""Searcher for BASE (Bielefeld Academic Search Engine).

BASE is one of the world's most voluminous search engines especially for
academic open access web resources. It provides OAI-PMH access to metadata
from thousands of repositories.

OAI-PMH Endpoint: https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi
Documentation: https://www.base-search.net/about/en/about_sources_date.php
"""

from typing import List, Optional, Dict, Any
import logging
from .oaipmh import OAIPMHSearcher
from ..paper import Paper

logger = logging.getLogger(__name__)


class BASESearcher(OAIPMHSearcher):
    """Searcher for BASE (Bielefeld Academic Search Engine)."""

    def __init__(self):
        """Initialize BASE searcher with OAI-PMH endpoint."""
        super().__init__(
            base_url="https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
            metadata_prefix="oai_dc"
        )
        # Update User-Agent for BASE
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/0.1.3 (BASE OAI-PMH client; https://github.com/openags/paper-search-mcp)'
        })

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Search BASE using OAI-PMH with query filtering.

        Args:
            query: Search query string
            max_results: Maximum number of results to return
            **kwargs: Additional parameters:
                - set: OAI-PMH set specification (e.g., 'pubtype:article')
                - from_date: Harvest from date (YYYY-MM-DD)
                - until_date: Harvest until date (YYYY-MM-DD)
                - language: Filter by language (e.g., 'en', 'de')
                - subject: Filter by subject category
                - has_fulltext: Filter for fulltext availability (True/False)
                - open_access: Filter for open access content (True/False)

        Returns:
            List of Paper objects
        """
        # BASE-specific sets
        if 'has_fulltext' in kwargs and kwargs['has_fulltext']:
            kwargs['set'] = kwargs.get('set', '') + ' dcterms:accessRights:open'
        if 'open_access' in kwargs and kwargs['open_access']:
            kwargs['set'] = kwargs.get('set', '') + ' dcterms:accessRights:open'

        # Call parent OAI-PMH search
        papers = super().search(query, max_results, **kwargs)

        # Apply additional BASE-specific filtering
        filtered_papers = []
        for paper in papers:
            if self._filter_paper(paper, kwargs):
                filtered_papers.append(paper)
            if len(filtered_papers) >= max_results:
                break

        return filtered_papers[:max_results]

    def _filter_paper(self, paper: Paper, filters: Dict[str, Any]) -> bool:
        """Apply BASE-specific filters to paper.

        Args:
            paper: Paper object
            filters: Filter parameters

        Returns:
            True if paper passes all filters
        """
        # Language filter
        if 'language' in filters and filters['language']:
            paper_lang = paper.extra.get('language', '').lower() if paper.extra else ''
            if not paper_lang or paper_lang != filters['language'].lower():
                return False

        # Subject filter
        if 'subject' in filters and filters['subject']:
            subject_lower = filters['subject'].lower()
            in_categories = any(subject_lower in cat.lower() for cat in paper.categories)
            in_keywords = any(subject_lower in kw.lower() for kw in paper.keywords)
            if not in_categories and not in_keywords:
                return False

        # Open access filter (already handled in OAI-PMH set)
        # Fulltext filter
        if 'has_fulltext' in filters and filters['has_fulltext']:
            if not paper.pdf_url and not paper.url:
                return False

        return True

    def _enrich_paper_from_oai(self, paper: Paper, dc_root):
        """Enrich Paper object with BASE-specific metadata.

        Overrides parent method to extract BASE-specific fields.

        Args:
            paper: Paper object to enrich
            dc_root: Dublin Core XML element
        """
        super()._enrich_paper_from_oai(paper, dc_root)

        # BASE-specific fields
        if not paper.extra:
            paper.extra = {}

        # Extract BASE-specific identifiers
        import xml.etree.ElementTree as ET
        identifiers = dc_root.findall('.//{http://purl.org/dc/elements/1.1/}identifier') or \
                     dc_root.findall('identifier')

        for ident_elem in identifiers:
            if ident_elem.text:
                ident_text = ident_elem.text.lower()
                if 'base-search.net' in ident_text:
                    paper.extra['base_id'] = ident_text
                elif 'urn:nbn:' in ident_text:
                    paper.extra['urn'] = ident_text
                elif 'hdl.handle.net' in ident_text:
                    paper.extra['handle'] = ident_text

        # Extract rights information
        rights_elems = dc_root.findall('.//{http://purl.org/dc/elements/1.1/}rights') or \
                      dc_root.findall('rights')
        if rights_elems:
            paper.extra['rights'] = [elem.text for elem in rights_elems if elem.text]

        # Extract source repository
        source_elems = dc_root.findall('.//{http://purl.org/dc/elements/1.1/}source') or \
                      dc_root.findall('source')
        if source_elems:
            paper.extra['repository'] = source_elems[0].text if source_elems[0].text else ''

        # Try to extract PDF URL from identifiers
        if not paper.pdf_url:
            for ident_elem in identifiers:
                if ident_elem.text and ident_elem.text.lower().endswith('.pdf'):
                    paper.pdf_url = ident_elem.text
                    break

        # Extract BASE relevance score if available
        # (BASE doesn't provide relevance scores in OAI-PMH, but we might add it from other sources)

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """Download PDF for a BASE record.

        BASE often provides direct PDF links in metadata.

        Args:
            paper_id: BASE identifier or OAI-PMH identifier
            save_path: Directory to save PDF

        Returns:
            Path to saved PDF file

        Raises:
            NotImplementedError: If PDF cannot be downloaded
        """
        # Try parent method first (searches for PDF URL)
        try:
            return super().download_pdf(paper_id, save_path)
        except Exception as e:
            logger.warning(f"Parent download failed: {e}")

        # Try alternative approach: search for paper and use first PDF link
        papers = self.search(paper_id, max_results=1)
        if not papers:
            raise ValueError(f"BASE record not found: {paper_id}")

        paper = papers[0]
        if paper.pdf_url:
            import os
            import requests
            response = self.session.get(paper.pdf_url, timeout=30)
            response.raise_for_status()
            os.makedirs(save_path, exist_ok=True)

            # Create safe filename
            safe_id = paper_id.replace('/', '_').replace(':', '_')
            filename = f"base_{safe_id}.pdf"
            output_file = os.path.join(save_path, filename)

            with open(output_file, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded PDF to {output_file}")
            return output_file

        raise NotImplementedError(
            f"No PDF available for BASE record: {paper_id}"
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read paper text from PDF.

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
            logger.error(f"Error reading BASE paper {paper_id}: {e}")
            raise NotImplementedError(
                f"Cannot read paper from BASE: {e}"
            )


if __name__ == "__main__":
    """Test the BASESearcher."""
    import logging
    logging.basicConfig(level=logging.INFO)

    searcher = BASESearcher()

    # Test search
    print("Testing BASE search...")

    # Test queries
    test_queries = [
        "machine learning",
        "artificial intelligence",
        "data science"
    ]

    for query in test_queries[:1]:  # Test first query only
        print(f"\nSearching BASE for: '{query}'")
        papers = searcher.search(query, max_results=3)
        print(f"Found {len(papers)} papers")
        for i, paper in enumerate(papers):
            print(f"{i+1}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors[:3])}")
            print(f"   Source: {paper.source}")
            print(f"   PDF: {'Yes' if paper.pdf_url else 'No'}")
            print(f"   URL: {paper.url}")
            print()