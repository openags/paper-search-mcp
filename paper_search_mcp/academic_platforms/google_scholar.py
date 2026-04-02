from typing import List, Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import time
import random
import re
from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource
import logging

logger = logging.getLogger(__name__)

class GoogleScholarSearcher(PaperSource):
    """Custom implementation of Google Scholar paper search"""
    
    SCHOLAR_URL = "https://scholar.google.com/scholar"
    BROWSERS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    ]

    def __init__(self, max_retries: int = 3, retry_delay: float = 2.0, proxy_url: Optional[str] = None):
        self.max_retries = max(1, max_retries)
        self.retry_delay = max(0.5, retry_delay)
        self.proxy_url = (proxy_url or get_env("GOOGLE_SCHOLAR_PROXY_URL", "")).strip()
        self._setup_session()

    def _setup_session(self):
        """Initialize session with random user agent"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': random.choice(self.BROWSERS),
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'en-US,en;q=0.9'
        })

        if self.proxy_url:
            self.session.proxies.update({
                'http': self.proxy_url,
                'https': self.proxy_url
            })

    def _rotate_user_agent(self):
        self.session.headers.update({'User-Agent': random.choice(self.BROWSERS)})

    @staticmethod
    def _is_captcha_page(soup: BeautifulSoup) -> bool:
        return bool(
            soup.find('form', {'id': 'gs_captcha_f'})
            or soup.find('input', {'name': 'captcha'})
            or 'please show you\'re not a robot' in soup.get_text(' ', strip=True).lower()
        )

    def _extract_year(self, text: str) -> Optional[int]:
        """Extract year from publication info"""
        for word in text.split():
            if word.isdigit() and 1900 <= int(word) <= datetime.now().year:
                return int(word)
        return None

    def _parse_paper(self, item) -> Optional[Paper]:
        """Parse single paper entry from HTML"""
        try:
            # Extract main paper elements
            title_elem = item.find('h3', class_='gs_rt')
            info_elem = item.find('div', class_='gs_a')
            abstract_elem = item.find('div', class_='gs_rs')

            if not title_elem or not info_elem:
                return None

            # Process title and URL
            title = title_elem.get_text(strip=True).replace('[PDF]', '').replace('[HTML]', '')
            link = title_elem.find('a', href=True)
            url = link['href'] if link else ''

            # Process author info
            info_text = info_elem.get_text()
            authors = [a.strip() for a in info_text.split('-')[0].split(',')]
            year = self._extract_year(info_text)
            doi = (
                extract_doi(url)
                or extract_doi(title)
                or extract_doi(info_text)
                or extract_doi(abstract_elem.get_text() if abstract_elem else "")
            )

            # Create paper object
            return Paper(
                paper_id=f"gs_{hash(url)}",
                title=title,
                authors=authors,
                abstract=abstract_elem.get_text() if abstract_elem else "",
                url=url,
                pdf_url="",
                published_date=datetime(year, 1, 1) if year else None,
                updated_date=None,
                source="google_scholar",
                categories=[],
                keywords=[],
                doi=doi,
                citations=0
            )
        except Exception as e:
            logger.warning(f"Failed to parse paper: {e}")
            return None

    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        """
        Search Google Scholar with custom parameters
        """
        papers = []
        start = 0
        results_per_page = min(10, max_results)

        while len(papers) < max_results:
            try:
                # Construct search parameters
                params = {
                    'q': query,
                    'start': start,
                    'hl': 'en',
                    'as_sdt': '0,5'  # Include articles and citations
                }

                response = None
                for attempt in range(self.max_retries):
                    self._rotate_user_agent()
                    time.sleep(random.uniform(1.0, 2.5))

                    response = self.session.get(self.SCHOLAR_URL, params=params, timeout=30)
                    if response.status_code == 200:
                        break

                    if response.status_code in (403, 429, 503):
                        wait_time = self.retry_delay * (2 ** attempt)
                        wait_time += random.uniform(0, 0.5)
                        logger.warning(
                            "Google Scholar returned %s (attempt %s/%s). Backing off %.1fs",
                            response.status_code,
                            attempt + 1,
                            self.max_retries,
                            wait_time,
                        )
                        time.sleep(wait_time)
                        continue

                    logger.error("Search failed with non-retryable status %s", response.status_code)
                    break

                if response is None or response.status_code != 200:
                    logger.error("Google Scholar search aborted after retries")
                    break

                # Parse results
                soup = BeautifulSoup(response.text, 'html.parser')

                if self._is_captcha_page(soup):
                    logger.warning(
                        "Google Scholar returned a bot-detection/captcha page. "
                        "Set PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL/GOOGLE_SCHOLAR_PROXY_URL "
                        "or reduce request frequency."
                    )
                    break

                results = soup.find_all('div', class_='gs_ri')

                if not results:
                    break

                # Process each result
                for item in results:
                    if len(papers) >= max_results:
                        break
                        
                    paper = self._parse_paper(item)
                    if paper:
                        papers.append(paper)

                start += results_per_page

            except Exception as e:
                logger.error(f"Search error: {e}")
                break

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Google Scholar doesn't support direct PDF downloads
        
        Raises:
            NotImplementedError: Always raises this error
        """
        raise NotImplementedError(
            "Google Scholar doesn't provide direct PDF downloads. "
            "Please use the paper URL to access the publisher's website."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Google Scholar doesn't support direct paper reading
        
        Returns:
            str: Message indicating the feature is not supported
        """
        return (
            "Google Scholar doesn't support direct paper reading. "
            "Please use the paper URL to access the full text on the publisher's website."
        )

if __name__ == "__main__":
    # Test Google Scholar searcher
    searcher = GoogleScholarSearcher()
    
    print("Testing search functionality...")
    query = "machine learning"
    max_results = 5
    
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"\nFound {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"\n{i}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors)}")
            print(f"   Citations: {paper.citations}")
            print(f"   URL: {paper.url}")
    except Exception as e:
        print(f"Error during search: {e}")