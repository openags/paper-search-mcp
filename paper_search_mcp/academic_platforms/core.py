# paper_search_mcp/academic_platforms/core.py
from typing import List, Optional, Dict, Any
import requests
import logging
import os
from datetime import datetime
from pathlib import Path
import time
from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource
from pypdf import PdfReader

logger = logging.getLogger(__name__)


class CORESearcher(PaperSource):
    """Searcher for CORE (global open access research papers)"""

    BASE_URL = "https://api.core.ac.uk/v3"
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize CORE searcher.

        Args:
            api_key: CORE API key (optional, can also be set via CORE_API_KEY env var)
        """
        self.api_key = api_key or get_env("CORE_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/1.0 (mailto:openags@example.com)',
            'Accept': 'application/json'
        })
        if self.api_key:
            self.session.headers.update({'Authorization': f'Bearer {self.api_key}'})
        else:
            logger.warning("No CORE API key provided. Searches may be rate-limited or return limited results.")

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """
        Search CORE for open access research papers.

        Args:
            query: Search query string
            max_results: Maximum results to return (CORE API default: 10, max: 100)
            **kwargs: Additional parameters:
                - year: Filter by year
                - language: Filter by language (e.g., 'en')
                - repository: Filter by repository
                - has_fulltext: Filter by full text availability (True/False)

        Returns:
            List[Paper]: List of found papers with metadata
        """
        papers = []

        try:
            # Prepare search parameters
            params = {
                'q': query,
                'limit': min(max_results, 100),  # CORE API max limit is 100
                'offset': 0,
            }

            # Add optional filters
            if 'year' in kwargs:
                params['year'] = kwargs['year']
            if 'language' in kwargs:
                params['language'] = kwargs['language']
            if 'repository' in kwargs:
                params['repository'] = kwargs['repository']
            if 'has_fulltext' in kwargs:
                params['has_fulltext'] = str(kwargs['has_fulltext']).lower()

            # Add other supported parameters
            supported_params = ['publishedAfter', 'publishedBefore', 'doi', 'issn', 'isbn']
            for param in supported_params:
                if param in kwargs:
                    params[param] = kwargs[param]

            response = None
            for attempt in range(3):
                try:
                    candidate = self.session.get(f"{self.BASE_URL}/search/works", params=params, timeout=30)

                    if candidate.status_code in self.RETRYABLE_STATUS_CODES:
                        wait_seconds = min(8, 2 ** attempt)
                        logger.warning(
                            "CORE request returned %s (attempt %s/3). Retrying in %ss",
                            candidate.status_code,
                            attempt + 1,
                            wait_seconds,
                        )
                        time.sleep(wait_seconds)
                        continue

                    if candidate.status_code in {401, 403} and self.api_key:
                        logger.warning(
                            "CORE API key was rejected (status=%s). Retrying once without key.",
                            candidate.status_code,
                        )
                        fallback_headers = {
                            'User-Agent': self.session.headers.get('User-Agent', ''),
                            'Accept': self.session.headers.get('Accept', 'application/json'),
                        }
                        candidate = requests.get(
                            f"{self.BASE_URL}/search/works",
                            params=params,
                            headers=fallback_headers,
                            timeout=30,
                        )

                    candidate.raise_for_status()
                    response = candidate
                    break
                except requests.Timeout:
                    wait_seconds = min(8, 2 ** attempt)
                    logger.warning(
                        "CORE request timed out (attempt %s/3). Retrying in %ss",
                        attempt + 1,
                        wait_seconds,
                    )
                    time.sleep(wait_seconds)

            if response is None:
                return papers

            data = response.json()

            # Parse results
            results = data.get('results', [])
            for item in results:
                try:
                    paper = self._parse_item(item)
                    if paper:
                        papers.append(paper)
                        if len(papers) >= max_results:
                            break
                except Exception as e:
                    logger.warning(f"Error parsing CORE item: {e}")
                    continue

            logger.info(f"CORE search returned {len(papers)} papers for query: {query}")

        except requests.RequestException as e:
            status_code = getattr(getattr(e, 'response', None), 'status_code', None)
            if status_code == 401:
                logger.error("CORE API authentication failed. Check your API key.")
            elif status_code == 429:
                logger.error("CORE API rate limit exceeded. Consider adding API key or reducing frequency.")
            else:
                logger.error(f"CORE search request error (status={status_code}): {e}")
        except Exception as e:
            logger.error(f"Unexpected error in CORE search: {e}")

        return papers

    def _parse_item(self, item: Dict[str, Any]) -> Optional[Paper]:
        """Parse a single CORE API result item into a Paper object."""
        try:
            # Extract core ID
            core_id = item.get('id', '')
            if not core_id:
                return None

            # Extract title
            title = item.get('title', '').strip()
            if not title:
                return None

            # Extract authors
            authors = []
            authors_data = item.get('authors', [])
            for author in authors_data:
                if isinstance(author, dict):
                    name = author.get('name', '')
                    if name:
                        authors.append(name)
                elif isinstance(author, str):
                    authors.append(author)

            # Extract abstract
            abstract = item.get('abstract', '')

            # Extract DOI
            doi = item.get('doi', '')
            if not doi and abstract:
                doi = extract_doi(abstract)

            # Extract publication date
            pub_date = None
            published_date = item.get('publishedDate')
            if published_date:
                try:
                    # CORE date format: "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SSZ"
                    if 'T' in published_date:
                        pub_date = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
                    else:
                        pub_date = datetime.strptime(published_date, '%Y-%m-%d')
                except ValueError:
                    try:
                        # Try just year
                        year = published_date[:4]
                        if year.isdigit():
                            pub_date = datetime(int(year), 1, 1)
                    except Exception:
                        pass

            # Extract URLs
            url = item.get('url', '')
            if not url and doi:
                url = f"https://doi.org/{doi}"

            # Extract PDF URL
            pdf_url = ''
            download_url = item.get('downloadUrl')
            if download_url and isinstance(download_url, str) and download_url.lower().endswith('.pdf'):
                pdf_url = download_url
            else:
                # Check full text URLs
                full_text_urls = item.get('fullTextUrls', [])
                for ft_url in full_text_urls:
                    if isinstance(ft_url, str) and ft_url.lower().endswith('.pdf'):
                        pdf_url = ft_url
                        break

            # Extract categories/subjects
            categories = []
            subjects = item.get('subjects', [])
            for subject in subjects:
                if isinstance(subject, dict):
                    subject_name = subject.get('name', '')
                    if subject_name:
                        categories.append(subject_name)
                elif isinstance(subject, str):
                    categories.append(subject)

            # Extract keywords
            keywords = []
            tags = item.get('tags', [])
            for tag in tags:
                if isinstance(tag, dict):
                    tag_name = tag.get('name', '')
                    if tag_name:
                        keywords.append(tag_name)
                elif isinstance(tag, str):
                    keywords.append(tag)

            # Extract repository information
            repository = item.get('repository', {})
            repository_name = repository.get('name', '') if isinstance(repository, dict) else ''

            # Create Paper object
            return Paper(
                paper_id=core_id,
                title=title,
                authors=authors,
                abstract=abstract,
                url=url,
                pdf_url=pdf_url,
                published_date=pub_date,
                source='core',
                categories=categories[:10],
                keywords=keywords[:10],
                doi=doi,
                extra={
                    'repository': repository_name,
                    'language': item.get('language', ''),
                    'citation_count': item.get('citationCount', 0),
                    'download_count': item.get('downloadCount', 0),
                }
            )

        except Exception as e:
            logger.warning(f"Error parsing CORE item data: {e}")
            return None

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download PDF of a CORE paper.

        Args:
            paper_id: CORE paper ID
            save_path: Directory to save the PDF

        Returns:
            str: Path to the downloaded PDF file

        Raises:
            Exception: If download fails
        """
        try:
            paper_details = self._get_paper_details(paper_id)
            paper_title = 'paper'

            # First try detail endpoint (preferred when API key is available)
            pdf_url = ''
            if paper_details:
                paper_title = paper_details.get('title', 'paper')
                download_url = paper_details.get('downloadUrl')
                if download_url and isinstance(download_url, str) and download_url.lower().endswith('.pdf'):
                    pdf_url = download_url
                else:
                    full_text_urls = paper_details.get('fullTextUrls', [])
                    for ft_url in full_text_urls:
                        if isinstance(ft_url, str) and ft_url.lower().endswith('.pdf'):
                            pdf_url = ft_url
                            break

            # Fallback: without API key, infer from public search metadata
            if not pdf_url:
                candidates = self.search(str(paper_id), max_results=5)
                preferred = next(
                    (
                        candidate for candidate in candidates
                        if str(getattr(candidate, 'paper_id', '')) == str(paper_id)
                        and getattr(candidate, 'pdf_url', '')
                    ),
                    None,
                )
                selected = preferred or next((candidate for candidate in candidates if getattr(candidate, 'pdf_url', '')), None)
                if selected:
                    pdf_url = selected.pdf_url
                    paper_title = selected.title or paper_title

            if not pdf_url:
                raise ValueError(f"CORE paper {paper_id} does not have an accessible PDF")

            # Create save directory
            save_dir = Path(save_path)
            save_dir.mkdir(parents=True, exist_ok=True)

            # Download PDF
            response = self.session.get(pdf_url, timeout=60)
            response.raise_for_status()

            # Check if response is actually a PDF
            content_type = response.headers.get('Content-Type', '')
            if 'pdf' not in content_type.lower():
                raise ValueError(f"URL does not point to a PDF file: {pdf_url}")

            # Generate filename
            title = paper_title.replace(' ', '_')[:50]
            filename = f"core_{paper_id}_{title}.pdf"
            filename = ''.join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))
            filepath = save_dir / filename

            # Save PDF
            with open(filepath, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded CORE PDF to {filepath}")
            return str(filepath)

        except requests.RequestException as e:
            error_msg = f"Failed to download CORE PDF for {paper_id}: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Error downloading CORE PDF for {paper_id}: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def _get_paper_details(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information for a CORE paper by ID."""
        try:
            response = self.session.get(f"{self.BASE_URL}/works/{paper_id}", timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.warning(f"Failed to get CORE paper details for {paper_id}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error getting CORE paper details: {e}")
            return None

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from a CORE paper.

        Args:
            paper_id: CORE paper ID
            save_path: Directory where the PDF is/will be saved

        Returns:
            str: Extracted text content of the paper, or full text if available
        """
        try:
            # First, try to get full text directly from API
            paper_details = self._get_paper_details(paper_id)
            if paper_details:
                full_text = paper_details.get('fullText')
                if full_text and len(full_text) > 500:
                    logger.info(f"Using full text from CORE API for {paper_id}")
                    return full_text

            # Fall back to PDF download and extraction
            pdf_path = self.download_pdf(paper_id, save_path)

            # Extract text from PDF
            with open(pdf_path, 'rb') as f:
                pdf_reader = PdfReader(f)
                text_parts = []
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)

                text = '\n'.join(text_parts)

            if not text or len(text.strip()) < 100:
                logger.warning(f"Extracted text from {paper_id} is too short")
                return f"Text extraction from CORE paper {paper_id} produced minimal content."

            return text

        except Exception as e:
            error_msg = f"Failed to read CORE paper {paper_id}: {e}"
            logger.error(error_msg)
            return error_msg


if __name__ == "__main__":
    # Test the CORESearcher
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    # Check for API key
    api_key = get_env("CORE_API_KEY", "")
    if not api_key:
        print("Warning: CORE_API_KEY environment variable not set. Some functionality may be limited.")

    searcher = CORESearcher(api_key)

    print("Testing CORE search...")
    query = "machine learning"
    papers = searcher.search(query, max_results=2)
    print(f"Found {len(papers)} papers for query '{query}':")

    for i, paper in enumerate(papers, 1):
        print(f"\n{i}. {paper.title}")
        print(f"   CORE ID: {paper.paper_id}")
        print(f"   DOI: {paper.doi}")
        print(f"   Authors: {', '.join(paper.authors[:3])}")
        print(f"   PDF URL: {paper.pdf_url}")
        if paper.abstract:
            print(f"   Abstract preview: {paper.abstract[:150]}...")

    # Test PDF download if we have papers and API key
    if papers and api_key:
        print("\n\nTesting CORE PDF download...")
        test_core_id = papers[0].paper_id
        try:
            pdf_path = searcher.download_pdf(test_core_id, "/tmp/core_test")
            print(f"PDF downloaded to: {pdf_path}")

            # Test text extraction
            print("\nTesting text extraction...")
            text = searcher.read_paper(test_core_id, "/tmp/core_test")
            print(f"Extracted text length: {len(text)} characters")
            if len(text) > 200:
                print(f"Text preview: {text[:200]}...")
        except Exception as e:
            print(f"PDF download/test failed: {e}")
    else:
        print("\nSkipping PDF download test (no API key or no papers)")