# paper_search_mcp/academic_platforms/doaj.py
"""Searcher for DOAJ (Directory of Open Access Journals).

DOAJ is a community-curated online directory that indexes and provides
access to high quality, open access, peer-reviewed journals.

API Documentation: https://doaj.org/api/v2
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import requests
import logging
import time
from urllib.parse import quote
from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource

logger = logging.getLogger(__name__)


class DOAJSearcher(PaperSource):
    """Searcher for DOAJ (Directory of Open Access Journals)."""

    BASE_URL = "https://doaj.org/api"
    USER_AGENT = "paper-search-mcp/0.1.3 (https://github.com/openags/paper-search-mcp)"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize DOAJ searcher.

        Args:
            api_key: DOAJ API key (optional, free registration required)
                     Can also be set via DOAJ_API_KEY environment variable.
        """
        self.api_key = api_key or get_env("DOAJ_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.USER_AGENT,
            'Accept': 'application/json'
        })

        if self.api_key:
            self.session.headers.update({'X-API-Key': self.api_key})
            logger.info("DOAJ API key configured")
        else:
            logger.warning(
                "No DOAJ API key provided. Searches will use public access "
                "with rate limits (100 requests/hour). "
                "Get a free API key at: https://doaj.org/apply-for-api-key/"
            )

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Search DOAJ for open access journal articles.

        Args:
            query: Search query string (supports Lucene query syntax)
            max_results: Maximum number of results (1-100, DOAJ default: 10)
            **kwargs: Additional parameters:
                - year: Filter by publication year (e.g., 2023)
                - journal: Filter by journal ISSN or title
                - publisher: Filter by publisher
                - country: Filter by country
                - language: Filter by language (e.g., 'en')
                - subject: Filter by subject category
                - open_access: Filter by open access status (default: True for DOAJ)
                - sort: Sort field (e.g., 'created_date', 'title')
                - sort_dir: Sort direction ('asc' or 'desc')

        Returns:
            List of Paper objects
        """
        if max_results > 100:
            max_results = 100  # DOAJ API limit per request
        if max_results < 1:
            max_results = 10

        papers = []
        page_size = min(max_results, 100)  # DOAJ max page size
        page = 1

        try:
            # Build Lucene query
            lucene_query = self._build_lucene_query(query, kwargs)

            params = {
                'page': page,
                'pageSize': page_size,
                'query': lucene_query
            }

            # Add sorting
            if 'sort' in kwargs:
                params['sort'] = kwargs['sort']
                if 'sort_dir' in kwargs and kwargs['sort_dir'] in ('asc', 'desc'):
                    params['sort_dir'] = kwargs['sort_dir']

            # Make request to DOAJ API
            encoded_query = quote(query.strip() or "*", safe="")
            search_url = f"{self.BASE_URL}/search/articles/{encoded_query}"
            response = self.session.get(
                search_url,
                params=params,
                timeout=30
            )
            response.raise_for_status()

            data = response.json()

            # Check for API errors
            if 'error' in data:
                logger.error(f"DOAJ API error: {data['error']}")
                return papers

            total = data.get('total', 0)
            logger.info(f"DOAJ search found {total} total results")

            # Parse results
            results = data.get('results', [])
            for item in results:
                if len(papers) >= max_results:
                    break

                try:
                    paper = self._parse_doaj_item(item)
                    if paper:
                        papers.append(paper)
                except Exception as e:
                    logger.warning(f"Error parsing DOAJ item: {e}")
                    continue

            # Rate limiting - be polite to DOAJ API
            # Public access: 100 requests/hour, API key: higher limits
            time.sleep(0.5 if self.api_key else 1.0)

        except requests.exceptions.RequestException as e:
            logger.error(f"DOAJ API request failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                if e.response.status_code == 429:
                    logger.warning("DOAJ rate limit exceeded. Consider using API key.")
        except ValueError as e:
            logger.error(f"Failed to parse DOAJ JSON response: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in DOAJ search: {e}")

        return papers[:max_results]

    def _build_lucene_query(self, query: str, filters: Dict[str, Any]) -> str:
        """Build Lucene query string for DOAJ API.

        DOAJ uses Lucene query syntax with field-specific filters.

        Args:
            query: Base search query
            filters: Additional filter parameters

        Returns:
            Lucene query string
        """
        query_parts = []

        # Add base query
        if query:
            # Search in title, abstract, keywords, fulltext
            query_parts.append(f"({query})")

        # Add year filter
        if 'year' in filters and filters['year']:
            year = filters['year']
            if isinstance(year, str) and '-' in year:
                # Year range
                year_range = year.split('-')
                if len(year_range) == 2:
                    query_parts.append(f"year:[{year_range[0]} TO {year_range[1]}]")
            else:
                query_parts.append(f"year:{year}")

        # Add journal filter
        if 'journal' in filters and filters['journal']:
            journal = filters['journal']
            # Try as ISSN first, then as title
            if len(journal) == 9 and '-' in journal:  # ISSN format: 1234-5678
                query_parts.append(f"issn:{journal}")
            else:
                query_parts.append(f"journal.title:{journal}")

        # Add publisher filter
        if 'publisher' in filters and filters['publisher']:
            query_parts.append(f"publisher:{filters['publisher']}")

        # Add country filter
        if 'country' in filters and filters['country']:
            query_parts.append(f"country:{filters['country']}")

        # Add language filter
        if 'language' in filters and filters['language']:
            query_parts.append(f"language:{filters['language']}")

        # Add subject filter
        if 'subject' in filters and filters['subject']:
            query_parts.append(f"subject:{filters['subject']}")

        # DOAJ only contains open access content, but we can still filter
        if 'open_access' in filters and filters['open_access'] is not None:
            # DOAJ is all open access, but we can filter by license type
            pass

        # Combine query parts with AND
        if len(query_parts) == 0:
            return "*:*"  # Match all query

        return " AND ".join(f"({part})" for part in query_parts)

    def _parse_doaj_item(self, item: Dict[str, Any]) -> Optional[Paper]:
        """Parse DOAJ API response item to Paper object.

        Args:
            item: DOAJ article item from API response

        Returns:
            Paper object or None if parsing fails
        """
        try:
            bibjson = item.get('bibjson', {})
            if not bibjson:
                return None

            # Extract title
            title = bibjson.get('title', '')
            if not title:
                return None

            # Extract authors
            authors = []
            author_list = bibjson.get('author', [])
            for author in author_list:
                name = author.get('name', '')
                if name:
                    authors.append(name.strip())

            # Extract abstract
            abstract = ''
            abstract_elem = bibjson.get('abstract')
            if isinstance(abstract_elem, str):
                abstract = abstract_elem
            elif isinstance(abstract_elem, dict):
                abstract = abstract_elem.get('text', '')

            # Extract DOI
            doi = ''
            identifiers = bibjson.get('identifier', [])
            for ident in identifiers:
                if ident.get('type') == 'doi' and ident.get('id'):
                    doi = ident['id']
                    break

            # Extract publication date
            published_date = None
            year = bibjson.get('year')
            month = bibjson.get('month', 1)
            day = bibjson.get('day', 1)

            if year:
                try:
                    published_date = datetime(int(year), int(month), int(day))
                except (ValueError, TypeError):
                    # Try just year
                    try:
                        published_date = datetime(int(year), 1, 1)
                    except (ValueError, TypeError):
                        pass

            # Extract journal information
            journal = bibjson.get('journal', {})
            journal_title = journal.get('title', '')
            journal_issn = journal.get('issn', '')
            if isinstance(journal_issn, list):
                journal_issn = journal_issn[0] if journal_issn else ''

            # Extract keywords
            keywords = []
            keywords_list = bibjson.get('keywords', [])
            if isinstance(keywords_list, list):
                keywords = [kw.strip() for kw in keywords_list if isinstance(kw, str) and kw.strip()]

            # Extract subject categories
            categories = []
            subject_list = bibjson.get('subject', [])
            if isinstance(subject_list, list):
                categories = [sub.get('term', '') for sub in subject_list if isinstance(sub, dict)]
                categories = [cat for cat in categories if cat]

            # Extract links (PDF and HTML)
            pdf_url = ''
            url = item.get('admin', {}).get('url', '')

            links = bibjson.get('link', [])
            for link in links:
                if isinstance(link, dict):
                    link_type = link.get('type', '')
                    link_url = link.get('url', '')
                    if link_type == 'fulltext' and link_url:
                        if link_url.lower().endswith('.pdf'):
                            pdf_url = link_url
                        elif not url:
                            url = link_url

            # If no PDF found, check for fulltext PDF in other fields
            if not pdf_url and 'fulltext' in bibjson:
                fulltext = bibjson.get('fulltext')
                if isinstance(fulltext, str) and fulltext.lower().endswith('.pdf'):
                    pdf_url = fulltext

            # Construct DOAJ URL if not available
            if not url and doi:
                url = f"https://doi.org/{doi}"
            elif not url:
                # Use DOAJ article page
                article_id = item.get('id', '')
                if article_id:
                    url = f"https://doaj.org/article/{article_id}"

            # Create Paper object
            paper = Paper(
                paper_id=item.get('id', '') or doi or f"doaj_{hash(title) & 0xffffffff:08x}",
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url,
                source='doaj',
                categories=categories,
                keywords=keywords
            )

            # Add extra metadata
            paper.extra = {
                'journal': journal_title,
                'issn': journal_issn,
                'publisher': journal.get('publisher', {}),
                'country': journal.get('country', ''),
                'language': bibjson.get('language', ''),
                'license': bibjson.get('license', [{}])[0] if isinstance(bibjson.get('license'), list) else {},
                'start_page': bibjson.get('start_page', ''),
                'end_page': bibjson.get('end_page', ''),
                'volume': bibjson.get('volume', ''),
                'number': bibjson.get('number', '')
            }

            return paper

        except Exception as e:
            logger.warning(f"Error parsing DOAJ article: {e}")
            return None

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """Download PDF for a DOAJ article.

        DOAJ provides direct PDF links for open access articles.

        Args:
            paper_id: DOAJ article ID or DOI
            save_path: Directory to save PDF

        Returns:
            Path to saved PDF file

        Raises:
            ValueError: If paper not found or no PDF available
            IOError: If download fails
        """
        # Try to get paper info first
        papers = self.search(paper_id, max_results=1)
        if not papers:
            raise ValueError(f"DOAJ article not found: {paper_id}")

        paper = papers[0]
        if not paper.pdf_url:
            # Try to construct PDF URL from DOI
            if paper.doi:
                # Some publishers provide direct PDF links via DOI
                pdf_url = f"https://doi.org/{paper.doi}"
                # But we need to check if it's actually a PDF
                # For now, try the URL
                paper.pdf_url = pdf_url
            else:
                raise ValueError(f"No PDF available for DOAJ article: {paper_id}")

        # Download PDF
        import os
        response = self.session.get(paper.pdf_url, timeout=30)
        response.raise_for_status()

        # Check if response is actually PDF
        content_type = response.headers.get('content-type', '')
        if 'pdf' not in content_type.lower() and not paper.pdf_url.lower().endswith('.pdf'):
            logger.warning(f"Response may not be PDF: {content_type}")

        os.makedirs(save_path, exist_ok=True)

        # Create safe filename
        safe_id = paper_id.replace('/', '_').replace(':', '_')
        filename = f"doaj_{safe_id}.pdf"
        output_file = os.path.join(save_path, filename)

        with open(output_file, 'wb') as f:
            f.write(response.content)

        logger.info(f"Downloaded PDF to {output_file}")
        return output_file

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
            # Try to download PDF first
            pdf_path = self.download_pdf(paper_id, save_path)

            # Extract text from PDF
            from PyPDF2 import PdfReader
            reader = PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text.strip()
        except Exception as e:
            logger.error(f"Error reading DOAJ paper {paper_id}: {e}")
            raise NotImplementedError(
                f"Cannot read paper from DOAJ: {e}"
            )


if __name__ == "__main__":
    """Test the DOAJSearcher."""
    import logging
    logging.basicConfig(level=logging.INFO)

    # Test with and without API key
    searcher = DOAJSearcher()

    # Test search
    print("Testing DOAJ search...")
    test_queries = [
        "machine learning",
        "open access",
        "climate change"
    ]

    for query in test_queries[:1]:  # Test first query only
        print(f"\nSearching DOAJ for: '{query}'")
        papers = searcher.search(query, max_results=3)
        print(f"Found {len(papers)} papers")
        for i, paper in enumerate(papers):
            print(f"{i+1}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors[:3])}")
            print(f"   Journal: {paper.extra.get('journal', 'Unknown')}")
            print(f"   Year: {paper.published_date.year if paper.published_date else 'Unknown'}")
            print(f"   DOI: {paper.doi}")
            print(f"   PDF: {'Yes' if paper.pdf_url else 'No'}")
            print()