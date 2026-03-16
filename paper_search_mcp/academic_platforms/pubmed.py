# paper_search_mcp/sources/pubmed.py
from typing import List
import requests
from xml.etree import ElementTree as ET
from datetime import datetime
from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource
import os

class PubMedSearcher(PaperSource):
    """Searcher for PubMed papers"""
    SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        search_params = {
            'db': 'pubmed',
            'term': query,
            'retmax': max_results,
            'retmode': 'xml'
        }
        search_response = requests.get(self.SEARCH_URL, params=search_params)
        search_root = ET.fromstring(search_response.content)
        ids = [id.text for id in search_root.findall('.//Id') if id.text]
        if not ids:
            return []
        
        fetch_params = {
            'db': 'pubmed',
            'id': ','.join(ids),
            'retmode': 'xml'
        }
        fetch_response = requests.get(self.FETCH_URL, params=fetch_params)
        fetch_root = ET.fromstring(fetch_response.content)
        
        papers = []
        for article in fetch_root.findall('.//PubmedArticle'):
            try:
                pmid_elem = article.find('.//PMID')
                pmid = pmid_elem.text.strip() if pmid_elem is not None and pmid_elem.text else ''
                if not pmid:
                    continue

                title_elem = article.find('.//ArticleTitle')
                title = ''.join(title_elem.itertext()).strip() if title_elem is not None else ''
                if not title:
                    continue

                authors = []
                for author in article.findall('.//Author'):
                    last_name = author.find('LastName')
                    initials = author.find('Initials')
                    if last_name is not None and last_name.text:
                        name = last_name.text.strip()
                        if initials is not None and initials.text:
                            name = f"{name} {initials.text.strip()}"
                        authors.append(name)

                abstract_parts = []
                for abstract_elem in article.findall('.//AbstractText'):
                    text = ''.join(abstract_elem.itertext()).strip()
                    if text:
                        abstract_parts.append(text)
                abstract = ' '.join(abstract_parts)

                year_elem = article.find('.//PubDate/Year')
                pub_date = year_elem.text if year_elem is not None else None
                published = datetime.strptime(pub_date, '%Y') if pub_date else None
                doi_elem = article.find('.//ELocationID[@EIdType="doi"]')
                doi = doi_elem.text if doi_elem is not None else ''

                if not doi and abstract:
                    doi = extract_doi(abstract)

                papers.append(Paper(
                    paper_id=pmid,
                    title=title,
                    authors=authors,
                    abstract=abstract,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    pdf_url='',  # PubMed 无直接 PDF
                    published_date=published,
                    updated_date=published,
                    source='pubmed',
                    categories=[],
                    keywords=[],
                    doi=doi
                ))
            except Exception:
                continue
        return papers

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """Attempt to download a paper's PDF from PubMed.

        Args:
            paper_id: PubMed ID (PMID)
            save_path: Directory to save the PDF

        Returns:
            str: Error message indicating PDF download is not supported
        
        Raises:
            NotImplementedError: Always raises this error as PubMed doesn't provide direct PDF access
        """
        message = ("PubMed does not provide direct PDF downloads. "
                  "Please use the paper's DOI or URL to access the publisher's website.")
        raise NotImplementedError(message)

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Attempt to read and extract text from a PubMed paper.

        Args:
            paper_id: PubMed ID (PMID)
            save_path: Directory for potential PDF storage (unused)

        Returns:
            str: Error message indicating PDF reading is not supported
        """
        message = ("PubMed papers cannot be read directly through this tool. "
                  "Only metadata and abstracts are available through PubMed's API. "
                  "Please use the paper's DOI or URL to access the full text on the publisher's website.")
        return message

if __name__ == "__main__":
    # 测试 PubMedSearcher 的功能
    searcher = PubMedSearcher()
    
    # 测试搜索功能
    print("Testing search functionality...")
    query = "machine learning"
    max_results = 5
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"Found {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors)}")
            print(f"   DOI: {paper.doi}")
            print(f"   URL: {paper.url}\n")
    except Exception as e:
        print(f"Error during search: {e}")
    
    # 测试 PDF 下载功能（会返回不支持的提示）
    if papers:
        print("\nTesting PDF download functionality...")
        paper_id = papers[0].paper_id
        try:
            pdf_path = searcher.download_pdf(paper_id, "./downloads")
        except NotImplementedError as e:
            print(f"Expected error: {e}")
    
    # 测试论文阅读功能（会返回不支持的提示）
    if papers:
        print("\nTesting paper reading functionality...")
        paper_id = papers[0].paper_id
        try:
            message = searcher.read_paper(paper_id)
            print(f"Response: {message}")
        except Exception as e:
            print(f"Error during paper reading: {e}")