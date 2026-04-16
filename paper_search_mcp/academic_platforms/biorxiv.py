from typing import List, Optional, Tuple
import requests
import os
import re
from datetime import datetime, timedelta
from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource
from PyPDF2 import PdfReader

class BioRxivSearcher(PaperSource):
    """Searcher for bioRxiv papers"""
    BASE_URL = "https://api.biorxiv.org/details/biorxiv"
    # Supports "YYYY-MM-DD/YYYY-MM-DD", "YYYY-MM-DD:YYYY-MM-DD",
    # "YYYY-MM-DD..YYYY-MM-DD", and "YYYY-MM-DD to YYYY-MM-DD".
    DATE_RANGE_PATTERN = re.compile(
        r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:/|:|\.\.|to)\s*(\d{4}-\d{2}-\d{2})\s*$",
        re.IGNORECASE
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.proxies = {'http': None, 'https': None}
        self.timeout = 30
        self.max_retries = 3

    def _resolve_query_mode(self, query: str, days: int) -> Tuple[str, str, str, Optional[str]]:
        normalized_query = (query or "").strip()
        doi = extract_doi(normalized_query)
        if doi:
            return "doi", doi, "na", None

        date_match = self.DATE_RANGE_PATTERN.match(normalized_query)
        if date_match:
            return "interval", date_match.group(1), date_match.group(2), None

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        category = normalized_query.lower().replace(' ', '_') if normalized_query else None
        return "interval", start_date, end_date, category

    def _request_json(self, url: str) -> Optional[dict]:
        tries = 0
        while tries < self.max_retries:
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                tries += 1
                if tries == self.max_retries:
                    print(f"Failed to connect to bioRxiv API after {self.max_retries} attempts: {e}")
                    return None
                print(f"Attempt {tries} failed, retrying...")
        return None

    def _parse_papers(self, collection: list) -> List[Paper]:
        papers = []
        for item in collection:
            try:
                date = datetime.strptime(item['date'], '%Y-%m-%d')
                papers.append(Paper(
                    paper_id=item['doi'],
                    title=item['title'],
                    authors=item['authors'].split('; '),
                    abstract=item['abstract'],
                    url=f"https://www.biorxiv.org/content/{item['doi']}v{item.get('version', '1')}",
                    pdf_url=f"https://www.biorxiv.org/content/{item['doi']}v{item.get('version', '1')}.full.pdf",
                    published_date=date,
                    updated_date=date,
                    source="biorxiv",
                    categories=[item['category']],
                    keywords=[],
                    doi=item['doi']
                ))
            except Exception as e:
                print(f"Error parsing bioRxiv entry: {e}")
        return papers

    def search(self, query: str, max_results: int = 10, days: int = 30) -> List[Paper]:
        mode, start, end, category = self._resolve_query_mode(query, days)
        papers: List[Paper] = []

        if mode == "doi":
            data = self._request_json(f"{self.BASE_URL}/{start}/{end}/json")
            if data:
                papers.extend(self._parse_papers(data.get('collection', [])))
            return papers[:max_results]

        cursor = 0
        while len(papers) < max_results:
            url = f"{self.BASE_URL}/{start}/{end}/{cursor}/json"
            if category:
                url += f"?category={category}"
            data = self._request_json(url)
            if not data:
                break

            collection = data.get('collection', [])
            if not collection:
                break

            papers.extend(self._parse_papers(collection))
            if len(collection) < 100:
                break
            cursor += 100

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download a PDF for a given paper ID from bioRxiv.

        Args:
            paper_id: The DOI of the paper.
            save_path: Directory to save the PDF.

        Returns:
            Path to the downloaded PDF file.
        """
        if not paper_id:
            raise ValueError("Invalid paper_id: paper_id is empty")

        pdf_url = f"https://www.biorxiv.org/content/{paper_id}v1.full.pdf"
        tries = 0
        while tries < self.max_retries:
            try:
                # Add User-Agent to avoid potential 403 errors
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                response = self.session.get(pdf_url, timeout=self.timeout, headers=headers)
                response.raise_for_status()
                os.makedirs(save_path, exist_ok=True)
                output_file = f"{save_path}/{paper_id.replace('/', '_')}.pdf"
                with open(output_file, 'wb') as f:
                    f.write(response.content)
                return output_file
            except requests.exceptions.RequestException as e:
                tries += 1
                if tries == self.max_retries:
                    raise Exception(f"Failed to download PDF after {self.max_retries} attempts: {e}")
                print(f"Attempt {tries} failed, retrying...")
    
    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Read a paper and convert it to text format.
        
        Args:
            paper_id: bioRxiv DOI
            save_path: Directory where the PDF is/will be saved
            
        Returns:
            str: The extracted text content of the paper
        """
        pdf_path = f"{save_path}/{paper_id.replace('/', '_')}.pdf"
        if not os.path.exists(pdf_path):
            pdf_path = self.download_pdf(paper_id, save_path)
        
        try:
            reader = PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text.strip()
        except Exception as e:
            print(f"Error reading PDF for paper {paper_id}: {e}")
            return ""
