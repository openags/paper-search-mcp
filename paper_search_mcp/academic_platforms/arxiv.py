# paper_search_mcp/sources/arxiv.py
import os
import re
import time
from datetime import datetime
from threading import Lock
from typing import List

import feedparser
import requests
from pypdf import PdfReader

from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource


class ArxivSearcher(PaperSource):
    """Searcher for arXiv papers.

    arXiv TOU requires no more than 1 request per 3 seconds with a single
    concurrent connection (https://info.arxiv.org/help/api/tou.html). A shared
    lock and timestamp enforce that policy across all instances in this Python
    process. Cross-process and cross-machine pacing remain the caller's
    responsibility.
    """
    BASE_URL = "https://export.arxiv.org/api/query"
    MIN_INTERVAL_SEC = 3.0  # arXiv TOU minimum
    MAX_ATTEMPTS = 3
    RETRYABLE_STATUS_CODES = frozenset((429, 500, 502, 503, 504))
    _request_lock = Lock()
    _last_request_at = 0.0
    _FIELD_PREFIX_RE = re.compile(
        r"(?:^|\s)(ti|au|abs|co|jr|cat|rn|id|all):",
        re.IGNORECASE,
    )
    _BOOLEAN_OP_RE = re.compile(r"(?:^|\s)(AND|OR|ANDNOT)(?:\s|$)")

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/1.0 (mailto:openags@example.com)',
            'Accept': 'application/atom+xml, application/xml;q=0.9, */*;q=0.8',
        })

    @classmethod
    def _pace_locked(cls):
        """Pace a request while the process-wide request lock is held."""
        now = time.monotonic()
        elapsed = now - cls._last_request_at
        if cls._last_request_at > 0 and elapsed < cls.MIN_INTERVAL_SEC:
            time.sleep(cls.MIN_INTERVAL_SEC - elapsed)
        cls._last_request_at = time.monotonic()

    @staticmethod
    def _is_soft_rate_limit(response: requests.Response) -> bool:
        """Detect arXiv's HTTP-200 ``Rate exceeded.`` response."""
        body_head = (response.content or b"")[:64]
        if isinstance(body_head, str):
            body_head = body_head.encode("utf-8", errors="ignore")
        return body_head.strip().lower().startswith(b"rate exceeded")

    def _request_with_retries(self, params):
        """Issue one serialized, paced arXiv request sequence."""
        response = None
        with self._request_lock:
            for attempt in range(self.MAX_ATTEMPTS):
                self._pace_locked()
                try:
                    response = self.session.get(
                        self.BASE_URL,
                        params=params,
                        timeout=30,
                    )
                except requests.RequestException:
                    response = None
                    if attempt < self.MAX_ATTEMPTS - 1:
                        time.sleep((attempt + 1) * 1.5)
                    continue

                if response.status_code == 200:
                    if not self._is_soft_rate_limit(response):
                        return response
                    if attempt < self.MAX_ATTEMPTS - 1:
                        time.sleep((attempt + 1) * 5.0)
                        continue
                    raise requests.RequestException(
                        "arxiv rate-limited: 'Rate exceeded.' body persisted "
                        f"across {self.MAX_ATTEMPTS} attempts"
                    )

                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    if attempt < self.MAX_ATTEMPTS - 1:
                        time.sleep((attempt + 1) * 1.5)
                        continue
                    if response.status_code == 429:
                        raise requests.RequestException(
                            "arxiv rate-limited: HTTP 429 persisted "
                            f"across {self.MAX_ATTEMPTS} attempts"
                        )
                return response
        return response

    @staticmethod
    def _build_search_query(query: str) -> str:
        """Quote plain phrases while preserving arXiv's structured query syntax."""
        normalized = " ".join((query or "").split())
        if (
            '"' in normalized
            or ArxivSearcher._FIELD_PREFIX_RE.search(normalized)
            or ArxivSearcher._BOOLEAN_OP_RE.search(normalized)
        ):
            return normalized
        if re.search(r"\s", normalized):
            return f'all:"{normalized}"'
        return f'all:{normalized}'

    def search(self, query: str, max_results: int = 10, sort_by: str = 'relevance', sort_order: str = 'descending') -> List[Paper]:
        params = {
            'search_query': self._build_search_query(query),
            'max_results': max_results,
            'sortBy': sort_by,
            'sortOrder': sort_order,
        }
        response = self._request_with_retries(params)

        if response is None or response.status_code != 200:
            return []

        feed = feedparser.parse(response.content)
        papers = []
        for entry in feed.entries:
            try:
                authors = [author.name for author in entry.authors]
                published = datetime.strptime(entry.published, '%Y-%m-%dT%H:%M:%SZ')
                updated = datetime.strptime(entry.updated, '%Y-%m-%dT%H:%M:%SZ')
                pdf_url = next((link.href for link in entry.links if link.type == 'application/pdf'), '')
                
                # Try to extract DOI from entry.doi or links or summary
                doi = entry.get('doi', '') or extract_doi(entry.summary) or extract_doi(entry.id)
                for link in entry.links:
                    if link.get('title') == 'doi':
                        doi = doi or extract_doi(link.href)

                papers.append(Paper(
                    paper_id=entry.id.split('/')[-1],
                    title=entry.title,
                    authors=authors,
                    abstract=entry.summary,
                    url=entry.id,
                    pdf_url=pdf_url,
                    published_date=published,
                    updated_date=updated,
                    source='arxiv',
                    categories=[tag.term for tag in entry.tags],
                    keywords=[],
                    doi=doi
                ))
            except Exception as e:
                print(f"Error parsing arXiv entry: {e}")
        return papers

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        pdf_url = f"https://arxiv.org/pdf/{paper_id}.pdf"
        response = requests.get(pdf_url)
        os.makedirs(save_path, exist_ok=True)
        output_file = f"{save_path}/{paper_id}.pdf"
        with open(output_file, 'wb') as f:
            f.write(response.content)
        return output_file

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read a paper and convert it to text format.
        
        Args:
            paper_id: arXiv paper ID
            save_path: Directory where the PDF is/will be saved
            
        Returns:
            str: The extracted text content of the paper
        """
        # First ensure we have the PDF
        pdf_path = f"{save_path}/{paper_id}.pdf"
        if not os.path.exists(pdf_path):
            pdf_path = self.download_pdf(paper_id, save_path)
        
        # Read the PDF
        try:
            reader = PdfReader(pdf_path)
            text = ""
            
            # Extract text from each page
            for page in reader.pages:
                text += page.extract_text() + "\n"
            
            return text.strip()
        except Exception as e:
            print(f"Error reading PDF for paper {paper_id}: {e}")
            return ""

if __name__ == "__main__":
    # 测试 ArxivSearcher 的功能
    searcher = ArxivSearcher()
    
    # 测试搜索功能
    print("Testing search functionality...")
    query = "machine learning"
    max_results = 5
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"Found {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id})")
    except Exception as e:
        print(f"Error during search: {e}")
    
    # 测试 PDF 下载功能
    if papers:
        print("\nTesting PDF download functionality...")
        paper_id = papers[0].paper_id
        save_path = "./downloads"  # 确保此目录存在
        try:
            os.makedirs(save_path, exist_ok=True)
            pdf_path = searcher.download_pdf(paper_id, save_path)
            print(f"PDF downloaded successfully: {pdf_path}")
        except Exception as e:
            print(f"Error during PDF download: {e}")

    # 测试论文阅读功能
    if papers:
        print("\nTesting paper reading functionality...")
        paper_id = papers[0].paper_id
        try:
            text_content = searcher.read_paper(paper_id)
            print(f"\nFirst 500 characters of the paper content:")
            print(text_content[:500] + "...")
            print(f"\nTotal length of extracted text: {len(text_content)} characters")
        except Exception as e:
            print(f"Error during paper reading: {e}")
