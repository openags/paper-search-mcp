# paper_search_mcp/academic_platforms/citeseerx.py
from typing import List, Optional, Dict, Any
from datetime import datetime
import requests
import logging
import json
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode
from requests.exceptions import SSLError
import urllib3

from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource

logger = logging.getLogger(__name__)


class CiteSeerXSearcher(PaperSource):
    """Searcher for CiteSeerX digital library and search engine"""

    BASE_URL = "https://citeseerx.ist.psu.edu"

    # CiteSeerX API endpoints
    SEARCH_API = f"{BASE_URL}/api/search"
    PAPERS_API = f"{BASE_URL}/api/papers"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize CiteSeerX searcher.

        Args:
            api_key: Optional API key (CiteSeerX API is generally open access)
        """
        self.api_key = api_key or get_env("CITESEERX_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/1.0 (https://github.com/openags/paper-search-mcp)',
            'Accept': 'application/json'
        })
        if self.api_key:
            self.session.headers.update({'Authorization': f'Bearer {self.api_key}'})

    def _get(self, url: str, **kwargs) -> requests.Response:
        """GET wrapper with SSL fallback and archive-redirect detection."""
        kwargs.setdefault('timeout', 30)
        try:
            resp = self.session.get(url, **kwargs)
        except SSLError:
            logger.warning("CiteSeerX SSL verification failed; retrying without cert verification")
            kwargs['verify'] = False
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = self.session.get(url, **kwargs)

        # If the endpoint redirected to the Wayback Machine the original host
        # is no longer serving live API responses; treat this as unavailable.
        if "web.archive.org" in resp.url:
            raise requests.HTTPError(
                f"CiteSeerX endpoint redirected to web archive ({resp.url}); "
                "the live API is currently unavailable.",
                response=resp,
            )
        return resp

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """
        Search CiteSeerX for computer science papers.

        Args:
            query: Search query string
            max_results: Maximum results to return (default: 10)
            **kwargs: Additional parameters:
                - year: Filter by publication year
                - author: Filter by author name
                - venue: Filter by conference/journal venue
                - min_citations: Minimum citation count
                - sort: Sort by 'relevance', 'date', 'citations'

        Returns:
            List of Paper objects
        """
        papers = []

        try:
            # Prepare parameters for CiteSeerX API
            params = {
                'q': query,
                'max': min(max_results, 100),  # CiteSeerX default max
                'start': 0,
                'sort': kwargs.get('sort', 'relevance')
            }

            # Add filters
            if 'year' in kwargs:
                year = kwargs['year']
                if isinstance(year, str) and '-' in year:
                    # Handle year range
                    year_range = year.split('-')
                    if len(year_range) == 2:
                        params['year'] = f"{year_range[0]}-{year_range[1]}"
                else:
                    params['year'] = str(year)

            if 'author' in kwargs:
                params['author'] = kwargs['author']

            if 'venue' in kwargs:
                params['venue'] = kwargs['venue']

            if 'min_citations' in kwargs:
                params['minCitations'] = kwargs['min_citations']

            logger.debug(f"Searching CiteSeerX with params: {params}")

            response = self._get(self.SEARCH_API, params=params)
            response.raise_for_status()

            data = response.json()

            # CiteSeerX API returns results in 'result' field
            results = data.get('result', {}).get('hits', {}).get('hit', [])

            # Handle single result (API returns dict instead of list for single result)
            if isinstance(results, dict):
                results = [results]

            for result in results:
                try:
                    paper = self._parse_citeseerx_result(result)
                    if paper:
                        papers.append(paper)
                        if len(papers) >= max_results:
                            break
                except Exception as e:
                    logger.warning(f"Error parsing CiteSeerX result: {e}")
                    continue

            logger.info(f"Found {len(papers)} papers from CiteSeerX for query: {query}")

        except requests.RequestException as e:
            logger.error(f"CiteSeerX API request error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                if e.response.status_code == 429:
                    logger.warning("CiteSeerX rate limit exceeded")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse CiteSeerX JSON response: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in CiteSeerX search: {e}")

        return papers

    def _parse_citeseerx_result(self, result: Dict[str, Any]) -> Optional[Paper]:
        """Parse a CiteSeerX API result into a Paper object."""
        try:
            # Extract paper info from result
            info = result.get('info', {})
            if not info:
                return None

            # Extract title
            title = info.get('title', '').strip()
            if not title:
                return None

            # Extract authors
            authors = []
            author_list = info.get('authors', [])
            if isinstance(author_list, list):
                for author in author_list:
                    if isinstance(author, dict):
                        author_name = author.get('name', '')
                        if author_name:
                            authors.append(author_name)
                    elif isinstance(author, str):
                        authors.append(author)
            elif isinstance(author_list, dict):
                author_name = author_list.get('name', '')
                if author_name:
                    authors.append(author_name)

            # Extract abstract
            abstract = info.get('abstract', '').strip()

            # Extract DOI
            doi = info.get('doi', '')
            if not doi and abstract:
                doi = extract_doi(abstract)

            # Extract publication year
            year = info.get('year', '')
            published_date = None
            if year and year.isdigit():
                try:
                    published_date = datetime(int(year), 1, 1)
                except ValueError:
                    pass

            # Extract venue
            venue = info.get('venue', '')

            # Extract citation count
            citations = int(info.get('citations', 0))

            # Extract paper ID
            paper_id = info.get('id', '')
            if not paper_id:
                # Generate ID from DOI or title
                if doi:
                    paper_id = f"citeseerx_{doi.replace('/', '_')}"
                else:
                    paper_id = f"citeseerx_{hash(title) & 0xffffffff:08x}"

            # Construct URLs
            url = info.get('url', '')
            if not url and paper_id:
                url = f"{self.BASE_URL}/paper?id={paper_id}"

            pdf_url = info.get('pdf', '')
            if not pdf_url and doi:
                # Try to construct PDF URL from DOI
                pdf_url = f"https://doi.org/{doi}"

            # Extract keywords
            keywords = []
            keyword_list = info.get('keywords', [])
            if isinstance(keyword_list, list):
                keywords = [kw for kw in keyword_list if isinstance(kw, str)]
            elif isinstance(keyword_list, str):
                keywords = [keyword_list]

            # Extract additional metadata
            publisher = info.get('publisher', '')
            volume = info.get('volume', '')
            issue = info.get('issue', '')
            pages = info.get('pages', '')

            # Create Paper object
            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url,
                source='citeseerx',
                citations=citations,
                keywords=keywords[:10],
                extra={
                    'venue': venue,
                    'year': year,
                    'publisher': publisher,
                    'volume': volume,
                    'issue': issue,
                    'pages': pages,
                    'citation_count': citations,
                    'source_db': info.get('source', ''),
                    'type': info.get('type', ''),
                }
            )

        except Exception as e:
            logger.warning(f"Error parsing CiteSeerX result data: {e}")
            return None

    def get_paper_details(self, paper_id: str) -> Optional[Paper]:
        """
        Get detailed information for a specific paper.

        Args:
            paper_id: CiteSeerX paper ID

        Returns:
            Paper object with detailed information, or None if not found
        """
        try:
            url = f"{self.PAPERS_API}/{paper_id}"
            response = self._get(url)
            response.raise_for_status()

            data = response.json()
            paper = self._parse_citeseerx_result({'info': data})

            return paper

        except requests.RequestException as e:
            logger.error(f"Error fetching paper details: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting paper details: {e}")
            return None

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download PDF for a CiteSeerX paper.

        Args:
            paper_id: CiteSeerX paper identifier
            save_path: Directory to save the PDF

        Returns:
            Path to the saved PDF file

        Raises:
            Exception: If download fails or no PDF available
        """
        # First get paper details to find PDF URL
        paper = self.get_paper_details(paper_id)
        if not paper or not paper.pdf_url:
            raise Exception(f"No PDF available for paper {paper_id}")

        try:
            # Download PDF
            response = self._get(paper.pdf_url, stream=True)
            response.raise_for_status()

            # Create save directory if it doesn't exist
            os.makedirs(save_path, exist_ok=True)

            # Generate filename
            filename = f"{paper_id.replace('/', '_')}.pdf"
            if paper.doi:
                filename = f"{paper.doi.replace('/', '_')}.pdf"
            filepath = os.path.join(save_path, filename)

            # Save PDF
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"Downloaded PDF to {filepath}")
            return filepath

        except requests.RequestException as e:
            logger.error(f"Error downloading PDF: {e}")
            raise Exception(f"Failed to download PDF: {e}")
        except Exception as e:
            logger.error(f"Unexpected error downloading PDF: {e}")
            raise

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from a CiteSeerX paper.

        Note: CiteSeerX provides abstracts but not always full text.
        This method tries to download PDF and extract text if available.

        Args:
            paper_id: CiteSeerX paper identifier
            save_path: Directory where PDF is/will be saved

        Returns:
            Extracted text content of the paper (abstract if PDF not available)

        Raises:
            Exception: If paper reading fails
        """
        try:
            # First get paper details
            paper = self.get_paper_details(paper_id)
            if not paper:
                raise Exception(f"Paper {paper_id} not found")

            # Return abstract if available
            if paper.abstract:
                return paper.abstract

            # Try to download PDF and extract text
            try:
                pdf_path = self.download_pdf(paper_id, save_path)
                # Extract text from PDF (simplified - would need PDF text extraction library)
                return f"PDF downloaded to {pdf_path}. Text extraction not implemented."
            except Exception as e:
                logger.warning(f"Could not download PDF: {e}")
                return f"Abstract: {paper.abstract}" if paper.abstract else "No content available"

        except Exception as e:
            logger.error(f"Error reading paper: {e}")
            return f"Error reading paper: {e}"


# For testing
if __name__ == "__main__":
    import sys

    searcher = CiteSeerXSearcher()

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = "machine learning"

    print(f"Searching CiteSeerX for: {query}")
    papers = searcher.search(query, max_results=5)

    print(f"Found {len(papers)} papers:")
    for i, paper in enumerate(papers):
        print(f"\n{i+1}. {paper.title}")
        print(f"   Authors: {', '.join(paper.authors[:3])}{'...' if len(paper.authors) > 3 else ''}")
        print(f"   DOI: {paper.doi}")
        print(f"   Year: {paper.extra.get('year', 'N/A')}")
        print(f"   Venue: {paper.extra.get('venue', 'N/A')}")
        print(f"   Citations: {paper.citations}")
        print(f"   URL: {paper.url}")