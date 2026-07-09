# paper_search_mcp/sources/arxiv.py
from typing import List
from datetime import datetime
import requests
import feedparser
import time
from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource
from pypdf import PdfReader
import os
from ..file_naming import paper_output_path

class ArxivSearcher(PaperSource):
    """Searcher for arXiv papers"""
    BASE_URL = "http://export.arxiv.org/api/query"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/1.0 (mailto:openags@example.com)',
            'Accept': 'application/atom+xml, application/xml;q=0.9, */*;q=0.8',
        })

    def search(self, query: str, max_results: int = 10, sort_by: str = 'relevance', sort_order: str = 'descending') -> List[Paper]:
        params = {
            'search_query': f'all:{query}',
            'max_results': max_results,
            'sortBy': sort_by,
            'sortOrder': sort_order,
        }
        response = None
        for attempt in range(4):
            try:
                response = self.session.get(self.BASE_URL, params=params, timeout=30)
            except requests.RequestException:
                time.sleep(3 ** attempt)
                continue
            if response.status_code == 200:
                break
            if response.status_code == 429:
                # arXiv rate limit — back off significantly
                time.sleep(10 * (attempt + 1))
                continue
            if response.status_code in (500, 502, 503, 504):
                time.sleep(3 ** attempt)
                continue
            break

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
        response = self.session.get(pdf_url, timeout=60)
        response.raise_for_status()
        metadata = self._metadata_for_id(paper_id)
        output_file = paper_output_path(
            save_path,
            title=metadata.get("title", ""),
            authors=metadata.get("authors", []),
            published_date=metadata.get("published_date", ""),
            identifier=paper_id,
            extension=".pdf",
        )
        with open(output_file, 'wb') as f:
            f.write(response.content)
        return str(output_file)

    def _metadata_for_id(self, paper_id: str) -> dict:
        try:
            response = self.session.get(self.BASE_URL, params={"id_list": paper_id}, timeout=30)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if not feed.entries:
                return {}
            entry = feed.entries[0]
            published = datetime.strptime(entry.published, '%Y-%m-%dT%H:%M:%SZ')
            return {
                "title": entry.title,
                "authors": [author.name for author in entry.authors],
                "published_date": published,
            }
        except Exception:
            return {}

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read a paper and convert it to text format.
        
        Args:
            paper_id: arXiv paper ID
            save_path: Directory where the PDF is/will be saved
            
        Returns:
            str: The extracted text content of the paper
        """
        # First ensure we have the PDF. The filename is metadata-based, so
        # always ask the downloader for the actual path.
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
