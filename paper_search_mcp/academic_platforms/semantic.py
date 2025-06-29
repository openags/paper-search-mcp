from typing import List, Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import time
import random
from ..paper import Paper
import logging
from PyPDF2 import PdfReader
import os
import re

logger = logging.getLogger(__name__)


class PaperSource:
    """Abstract base class for paper sources"""

    def search(self, query: str, **kwargs) -> List[Paper]:
        raise NotImplementedError

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

    def read_paper(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError


class SemanticSearcher(PaperSource):
    """Semantic Scholar paper search implementation"""

    SEMANTIC_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    SEMANTIC_BASE_URL = "https://api.semanticscholar.org/graph/v1"
    BROWSERS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]

    def __init__(self):
        self._setup_session()

    def _setup_session(self):
        """Initialize session with random user agent"""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": random.choice(self.BROWSERS),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date from Semantic Scholar format (e.g., '2025-06-02')"""
        try:
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            logger.warning(f"Could not parse date: {date_str}")
            return None

    def _extract_url_from_disclaimer(self, disclaimer: str) -> str:
        """Extract URL from disclaimer text"""
        # 匹配常见的 URL 模式
        url_patterns = [
            r'https?://[^\s,)]+',  # 基本的 HTTP/HTTPS URL
            r'https?://arxiv\.org/abs/[^\s,)]+',  # arXiv 链接
            r'https?://[^\s,)]*\.pdf',  # PDF 文件链接
        ]
        
        all_urls = []
        for pattern in url_patterns:
            matches = re.findall(pattern, disclaimer)
            all_urls.extend(matches)
        
        if not all_urls:
            return ""
        
        doi_urls = [url for url in all_urls if 'doi.org' in url]
        if doi_urls:
            return doi_urls[0]
        
        non_unpaywall_urls = [url for url in all_urls if 'unpaywall.org' not in url]
        if non_unpaywall_urls:
            url = non_unpaywall_urls[0]
            if 'arxiv.org/abs/' in url:
                pdf_url = url.replace('/abs/', '/pdf/')
                return pdf_url
            return url
        
        if all_urls:
            url = all_urls[0]
            if 'arxiv.org/abs/' in url:
                pdf_url = url.replace('/abs/', '/pdf/')
                return pdf_url
            return url
        
        return ""

    def _parse_paper(self, item) -> Optional[Paper]:
        """Parse single paper entry from Semantic Scholar HTML and optionally fetch detailed info"""
        try:
            authors = [author['name'] for author in item.get('authors', [])]
            
            # Parse the publication date
            published_date = self._parse_date(item.get('publicationDate', ''))
            
            # Safely get PDF URL - 支持从 disclaimer 中提取
            pdf_url = ""
            if item.get('openAccessPdf'):
                open_access_pdf = item['openAccessPdf']
                # 首先尝试直接获取 URL
                if open_access_pdf.get('url'):
                    pdf_url = open_access_pdf['url']
                # 如果 URL 为空但有 disclaimer，尝试从 disclaimer 中提取
                elif open_access_pdf.get('disclaimer'):
                    pdf_url = self._extract_url_from_disclaimer(open_access_pdf['disclaimer'])
            
            # Safely get DOI
            doi = ""
            if item.get('externalIds') and item['externalIds'].get('DOI'):
                doi = item['externalIds']['DOI']
            
            # Safely get categories
            categories = item.get('fieldsOfStudy', [])
            if not categories:
                categories = []
            
            return Paper(
                paper_id=item['paperId'],
                title=item['title'],
                authors=authors,
                abstract=item.get('abstract', ''),
                url=item.get('url', ''),
                pdf_url=pdf_url,
                published_date=published_date,
                source="semantic",
                categories=categories,
                doi=doi,
                citations=item.get('citationCount', 0),
            )

        except Exception as e:
            logger.warning(f"Failed to parse Semantic paper: {e}")
            return None
        
    @staticmethod
    def get_api_key() -> Optional[str]:
        """
        Get the Semantic Scholar API key from environment variables.
        Returns None if no API key is set or if it's empty, enabling unauthenticated access.
        """
        api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
        if not api_key or api_key.strip() == "":
            logger.warning("No SEMANTIC_SCHOLAR_API_KEY set or it's empty. Using unauthenticated access with lower rate limits.")
            return None
        return api_key.strip()
    
    def request_api(self, path: str, params: dict) -> dict:
        """
        Make a request to the Semantic Scholar API with optional API key.
        """
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                api_key = self.get_api_key()
                headers = {"x-api-key": api_key} if api_key else {}
                url = f"{self.SEMANTIC_BASE_URL}/{path}"
                response = self.session.get(url, params=params, headers=headers)
                
                # 检查是否是429错误（限流）
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)  # 指数退避
                        logger.warning(f"Rate limited (429). Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"Rate limited (429) after {max_retries} attempts. Please wait before making more requests.")
                        return {"error": "rate_limited", "status_code": 429, "message": "Too many requests. Please wait before retrying."}
                
                response.raise_for_status()
                return response
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"Rate limited (429). Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"Rate limited (429) after {max_retries} attempts. Please wait before making more requests.")
                        return {"error": "rate_limited", "status_code": 429, "message": "Too many requests. Please wait before retrying."}
                else:
                    logger.error(f"HTTP Error requesting API: {e}")
                    return {"error": "http_error", "status_code": e.response.status_code, "message": str(e)}
            except Exception as e:
                logger.error(f"Error requesting API: {e}")
                return {"error": "general_error", "message": str(e)}
        
        return {"error": "max_retries_exceeded", "message": "Maximum retry attempts exceeded"}

    def search(self, query: str, year: Optional[str] = None, max_results: int = 10) -> List[Paper]:
        """
        Search Semantic Scholar

        Args:
            query: Search query string
            year (Optional[str]): Filter by publication year. Supports several formats:
            - Single year: "2019"
            - Year range: "2016-2020"
            - Since year: "2010-"
            - Until year: "-2015"
            max_results: Maximum number of results to return

        Returns:
            List[Paper]: List of paper objects
        """
        papers = []

        try:
            fields = ["title", "abstract", "year", "citationCount", "authors", "url","publicationDate","externalIds","fieldsOfStudy"]
            # Construct search parameters
            params = {
                "query": query,
                "limit": max_results,
                "fields": ",".join(fields),
            }
            if year:
                params["year"] = year
            # Make request
            response = self.request_api("paper/search", params)
            
            # Check for errors
            if isinstance(response, dict) and "error" in response:
                error_msg = response.get("message", "Unknown error")
                if response.get("error") == "rate_limited":
                    logger.error(f"Rate limited by Semantic Scholar API: {error_msg}")
                else:
                    logger.error(f"Semantic Scholar API error: {error_msg}")
                return papers
            
            # Check response status code
            if not hasattr(response, 'status_code') or response.status_code != 200:
                status_code = getattr(response, 'status_code', 'unknown')
                logger.error(f"Semantic Scholar search failed with status {status_code}")
                return papers
                
            data = response.json()
            results = data['data']

            if not results:
                logger.info("No results found for the query")
                return papers

            # Process each result
            for i, item in enumerate(results):
                if len(papers) >= max_results:
                    break

                logger.info(f"Processing paper {i+1}/{min(len(results), max_results)}")
                paper = self._parse_paper(item)
                if paper:
                    papers.append(paper)

        except Exception as e:
            logger.error(f"Semantic Scholar search error: {e}")

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download PDF from Semantic Scholar

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
            save_path: Path to save the PDF
            
        Returns:
            str: Path to downloaded file or error message
        """
        try:
            paper = self.get_paper_details(paper_id)
            if not paper or not paper.pdf_url:
                return f"Error: Could not find PDF URL for paper {paper_id}"
            pdf_url = paper.pdf_url
            pdf_response = requests.get(pdf_url, timeout=30)
            pdf_response.raise_for_status()
            
            # Create download directory if it doesn't exist
            os.makedirs(save_path, exist_ok=True)
            
            filename = f"semantic_{paper_id.replace('/', '_')}.pdf"
            pdf_path = os.path.join(save_path, filename)
            
            with open(pdf_path, "wb") as f:
                f.write(pdf_response.content)
            return pdf_path
        except Exception as e:
            logger.error(f"PDF download error: {e}")
            return f"Error downloading PDF: {e}"

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from Semantic Scholar paper PDF

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
            save_path: Directory to save downloaded PDF

        Returns:
            str: Extracted text from the PDF or error message
        """
        try:
            # First get paper details to get the PDF URL
            paper = self.get_paper_details(paper_id)
            if not paper or not paper.pdf_url:
                return f"Error: Could not find PDF URL for paper {paper_id}"

            # Download the PDF
            pdf_response = requests.get(paper.pdf_url, timeout=30)
            pdf_response.raise_for_status()

            # Create download directory if it doesn't exist
            os.makedirs(save_path, exist_ok=True)

            # Save the PDF
            filename = f"semantic_{paper_id.replace('/', '_')}.pdf"
            pdf_path = os.path.join(save_path, filename)

            with open(pdf_path, "wb") as f:
                f.write(pdf_response.content)

            # Extract text using PyPDF2
            reader = PdfReader(pdf_path)
            text = ""

            for page_num, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += f"\n--- Page {page_num + 1} ---\n"
                        text += page_text + "\n"
                except Exception as e:
                    logger.warning(
                        f"Failed to extract text from page {page_num + 1}: {e}"
                    )
                    continue

            if not text.strip():
                return (
                    f"PDF downloaded to {pdf_path}, but unable to extract readable text"
                )

            # Add paper metadata at the beginning
            metadata = f"Title: {paper.title}\n"
            metadata += f"Authors: {', '.join(paper.authors)}\n"
            metadata += f"Published Date: {paper.published_date}\n"
            metadata += f"URL: {paper.url}\n"
            metadata += f"PDF downloaded to: {pdf_path}\n"
            metadata += "=" * 80 + "\n\n"

            return metadata + text.strip()

        except requests.RequestException as e:
            logger.error(f"Error downloading PDF: {e}")
            return f"Error downloading PDF: {e}"
        except Exception as e:
            logger.error(f"Read paper error: {e}")
            return f"Error reading paper: {e}"

    def get_paper_details(self, paper_id: str) -> Optional[Paper]:
        """
        Fetch detailed information for a specific Semantic Scholar paper

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")

        Returns:
            Paper: Detailed paper object with full metadata
        """
        try:
            fields = ["title", "abstract", "year", "citationCount", "authors", "url","publicationDate","externalIds","fieldsOfStudy"]
            params = {
                "fields": ",".join(fields),
            }
            
            response = self.request_api(f"paper/{paper_id}", params)
            
            # Check for errors
            if isinstance(response, dict) and "error" in response:
                error_msg = response.get("message", "Unknown error")
                if response.get("error") == "rate_limited":
                    logger.error(f"Rate limited by Semantic Scholar API: {error_msg}")
                else:
                    logger.error(f"Semantic Scholar API error: {error_msg}")
                return None
            
            # Check response status code
            if not hasattr(response, 'status_code') or response.status_code != 200:
                status_code = getattr(response, 'status_code', 'unknown')
                logger.error(f"Semantic Scholar paper details fetch failed with status {status_code}")
                return None
                
            results = response.json()
            paper = self._parse_paper(results)
            if paper:
                return paper
            else:
                return None
        except Exception as e:
            logger.error(f"Error fetching paper details for {paper_id}: {e}")
            return None


if __name__ == "__main__":
    # Test Semantic searcher
    searcher = SemanticSearcher()

    print("Testing Semantic search functionality...")
    query = "secret sharing"
    max_results = 2

    print("\n" + "=" * 60)
    print("1. Testing search with detailed information")
    print("=" * 60)
    try:
        papers = searcher.search(query, year=None, max_results=max_results)
        print(f"\nFound {len(papers)} papers for query '{query}' (with details):")
        for i, paper in enumerate(papers, 1):
            print(f"\n{i}. {paper.title}")
            print(f"   Paper ID: {paper.paper_id}")
            print(f"   Authors: {', '.join(paper.authors)}")
            print(f"   Categories: {', '.join(paper.categories)}")
            print(f"   URL: {paper.url}")
            if paper.pdf_url:
                print(f"   PDF: {paper.pdf_url}")
            if paper.published_date:
                print(f"   Published Date: {paper.published_date}")
            if paper.abstract:
                print(f"   Abstract: {paper.abstract[:200]}...")
    except Exception as e:
        print(f"Error during detailed search: {e}")

    print("\n" + "=" * 60)
    print("2. Testing manual paper details fetching")
    print("=" * 60)
    test_paper_id = "5bbfdf2e62f0508c65ba6de9c72fe2066fd98138"
    try:
        paper_details = searcher.get_paper_details(test_paper_id)
        if paper_details:
            print(f"\nManual fetch for paper {test_paper_id}:")
            print(f"Title: {paper_details.title}")
            print(f"Authors: {', '.join(paper_details.authors)}")
            print(f"Categories: {', '.join(paper_details.categories)}")
            print(f"URL: {paper_details.url}")
            if paper_details.pdf_url:
                print(f"PDF: {paper_details.pdf_url}")
            if paper_details.published_date:
                print(f"Published Date: {paper_details.published_date}")
            print(f"DOI: {paper_details.doi}")
            print(f"Citations: {paper_details.citations}")
            print(f"Abstract: {paper_details.abstract[:200]}...")
        else:
            print(f"Could not fetch details for paper {test_paper_id}")
    except Exception as e:
        print(f"Error fetching paper details: {e}")
    