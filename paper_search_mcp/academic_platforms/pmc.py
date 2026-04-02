# paper_search_mcp/academic_platforms/pmc.py
from typing import List, Optional
import requests
from xml.etree import ElementTree as ET
from datetime import datetime
import logging
import os
from pathlib import Path
from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)


class PMCSearcher(PaperSource):
    """Searcher for PubMed Central (PMC) open access papers"""

    # PMC OA Web Service for listing open access articles
    OA_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
    # E-utilities API for fetching metadata
    EUTILS_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EUTILS_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    EUTILS_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/1.0 (mailto:openags@example.com)',
            'Accept': 'application/xml'
        })

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """
        Search PMC open access articles.

        Args:
            query: Search query string
            max_results: Maximum results to return
            **kwargs: Additional parameters (e.g., from_date, to_date)

        Returns:
            List[Paper]: List of found papers with metadata
        """
        papers = []

        try:
            # Step 1: Use E-utilities to search PMC database
            search_params = {
                'db': 'pmc',
                'term': query,
                'retmax': max_results,
                'retmode': 'xml',
                'tool': 'paper-search-mcp',
                'email': 'openags@example.com'
            }

            search_response = self.session.get(self.EUTILS_SEARCH_URL, params=search_params, timeout=30)
            search_response.raise_for_status()
            search_root = ET.fromstring(search_response.content)

            # Get PMC IDs
            pmcids = [id_elem.text for id_elem in search_root.findall('.//Id') if id_elem.text]
            if not pmcids:
                logger.info(f"No PMC results found for query: {query}")
                return papers

            # Step 2: Fetch compact summaries (more stable than full-text efetch)
            summary_params = {
                'db': 'pmc',
                'id': ','.join(pmcids),
                'retmode': 'xml',
                'tool': 'paper-search-mcp',
                'email': 'openags@example.com'
            }

            summary_response = self.session.get(self.EUTILS_SUMMARY_URL, params=summary_params, timeout=30)
            summary_response.raise_for_status()
            summary_root = ET.fromstring(summary_response.content)

            # Step 3: Parse each summary record
            for docsum in summary_root.findall('.//DocSum'):
                try:
                    paper = self._parse_docsum(docsum)
                    if paper:
                        papers.append(paper)
                        if len(papers) >= max_results:
                            break
                except Exception as e:
                    logger.warning(f"Error parsing PMC summary: {e}")
                    continue

        except requests.RequestException as e:
            logger.error(f"PMC search request error: {e}")
        except ET.ParseError as e:
            logger.error(f"PMC XML parsing error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in PMC search: {e}")

        return papers

    def _parse_docsum(self, docsum: ET.Element) -> Optional[Paper]:
        """Parse a single PMC eSummary DocSum element into a Paper object."""
        try:
            def _item_text(name: str) -> str:
                item = docsum.find(f"./Item[@Name='{name}']")
                if item is None:
                    return ''
                return ''.join(item.itertext()).strip()

            doc_id = ''.join((docsum.findtext('Id') or '').split())
            if not doc_id:
                return None

            title = _item_text('Title')
            if not title:
                return None

            # Parse authors list
            authors: List[str] = []
            author_list_item = docsum.find("./Item[@Name='AuthorList']")
            if author_list_item is not None:
                for sub_item in author_list_item.findall('./Item'):
                    value = ''.join(sub_item.itertext()).strip()
                    if value:
                        authors.append(value)

            article_ids_text = _item_text('ArticleIds')
            article_ids = [line.strip() for line in article_ids_text.splitlines() if line.strip()]

            pmcid = next((value for value in article_ids if value.upper().startswith('PMC')), f"PMC{doc_id}")
            if not pmcid.upper().startswith('PMC'):
                pmcid = f"PMC{pmcid}"

            doi = _item_text('DOI')
            if not doi:
                doi = next((value for value in article_ids if value.startswith('10.')), '')

            pub_date = None
            pub_date_raw = _item_text('PubDate')
            for fmt in ('%Y %b %d', '%Y %b', '%Y'):
                try:
                    pub_date = datetime.strptime(pub_date_raw, fmt)
                    break
                except ValueError:
                    continue

            journal = _item_text('FullJournalName') or _item_text('Source')

            return Paper(
                paper_id=pmcid,
                title=title,
                authors=authors,
                abstract='',
                url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
                pdf_url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/",
                published_date=pub_date,
                source='pmc',
                categories=[journal] if journal else [],
                keywords=[],
                doi=doi,
            )
        except Exception as e:
            logger.warning(f"Error parsing PMC DocSum: {e}")
            return None

    def _parse_article(self, article: ET.Element) -> Optional[Paper]:
        """Parse a single PMC article XML element into a Paper object."""
        try:
            def elem_text(elem: Optional[ET.Element]) -> str:
                if elem is None:
                    return ''
                return ''.join(elem.itertext()).strip()

            # Extract PMCID
            pmcid_elem = (
                article.find('.//article-id[@pub-id-type="pmcid"]')
                or article.find('.//article-id[@pub-id-type="pmc"]')
            )
            pmcid = elem_text(pmcid_elem)
            if not pmcid:
                return None
            if not pmcid.startswith('PMC'):
                pmcid = f"PMC{pmcid}"

            # Extract DOI
            doi_elem = article.find('.//article-id[@pub-id-type="doi"]')
            doi = elem_text(doi_elem)

            # Extract title
            title_elem = article.find('.//article-title')
            title = elem_text(title_elem)
            if not title:
                # Try alternative title paths
                title_elem = article.find('.//title-group/article-title')
                title = elem_text(title_elem)

            # Extract authors
            authors = []
            for author_elem in article.findall('.//contrib[@contrib-type="author"]'):
                surname_elem = author_elem.find('.//surname')
                given_names_elem = author_elem.find('.//given-names')
                if surname_elem is not None:
                    surname = elem_text(surname_elem)
                    given_names = elem_text(given_names_elem)
                    author_name = f"{given_names} {surname}".strip()
                    if author_name:
                        authors.append(author_name)

            # Extract abstract
            abstract_parts = []
            for abstract_elem in article.findall('.//abstract//p'):
                part = elem_text(abstract_elem)
                if part:
                    abstract_parts.append(part)
            if not abstract_parts:
                abstract_elem = article.find('.//abstract')
                abstract_text = elem_text(abstract_elem)
                if abstract_text:
                    abstract_parts.append(abstract_text)
            abstract = ' '.join(abstract_parts)

            # Extract publication date
            pub_date = None
            pub_date_elem = article.find('.//pub-date[@pub-type="epub"]') or article.find('.//pub-date')
            if pub_date_elem is not None:
                year_elem = pub_date_elem.find('year')
                month_elem = pub_date_elem.find('month')
                day_elem = pub_date_elem.find('day')
                year = year_elem.text if year_elem is not None else None
                month = month_elem.text if month_elem is not None else '01'
                day = day_elem.text if day_elem is not None else '01'
                if year:
                    try:
                        date_str = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                        pub_date = datetime.strptime(date_str, '%Y-%m-%d')
                    except ValueError:
                        try:
                            pub_date = datetime.strptime(year, '%Y')
                        except ValueError:
                            pass

            # Construct URLs
            url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
            pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"

            # Extract categories (subjects)
            categories = []
            for subject_elem in article.findall('.//subject'):
                subject_text = elem_text(subject_elem)
                if subject_text:
                    categories.append(subject_text)

            # Extract keywords
            keywords = []
            for kwd_elem in article.findall('.//kwd'):
                kwd_text = elem_text(kwd_elem)
                if kwd_text:
                    keywords.append(kwd_text)

            # If DOI not found in XML, try to extract from abstract
            if not doi and abstract:
                doi = extract_doi(abstract)

            return Paper(
                paper_id=pmcid,
                title=title,
                authors=authors,
                abstract=abstract,
                url=url,
                pdf_url=pdf_url,
                published_date=pub_date,
                source='pmc',
                categories=categories[:10],  # Limit to 10 categories
                keywords=keywords[:10],      # Limit to 10 keywords
                doi=doi
            )

        except Exception as e:
            logger.warning(f"Error parsing article element: {e}")
            return None

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download PDF of a PMC open access article.

        Args:
            paper_id: PMCID (e.g., 'PMC1234567')
            save_path: Directory to save the PDF

        Returns:
            str: Path to the downloaded PDF file

        Raises:
            Exception: If download fails
        """
        try:
            # Ensure PMCID format
            if not paper_id.startswith('PMC'):
                paper_id = f"PMC{paper_id}"

            pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{paper_id}/pdf/"

            # Create save directory if it doesn't exist
            save_dir = Path(save_path)
            save_dir.mkdir(parents=True, exist_ok=True)

            # Download PDF
            response = self.session.get(pdf_url, timeout=60)
            response.raise_for_status()

            # Check if response is actually a PDF
            content_type = response.headers.get('Content-Type', '')
            if 'pdf' not in content_type.lower():
                # Might be an HTML page indicating no PDF available
                raise ValueError(f"PMC article {paper_id} does not have an open access PDF")

            # Generate filename
            filename = f"{paper_id}.pdf"
            filepath = save_dir / filename

            # Save PDF
            with open(filepath, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded PMC PDF to {filepath}")
            return str(filepath)

        except requests.RequestException as e:
            error_msg = f"Failed to download PMC PDF for {paper_id}: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Error downloading PMC PDF for {paper_id}: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from a PMC open access article.

        Args:
            paper_id: PMCID (e.g., 'PMC1234567')
            save_path: Directory where the PDF is/will be saved

        Returns:
            str: Extracted text content of the paper
        """
        try:
            # Download PDF first
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
                return f"Text extraction from PMC article {paper_id} produced minimal content."

            return text

        except Exception as e:
            error_msg = f"Failed to read PMC paper {paper_id}: {e}"
            logger.error(error_msg)
            return error_msg


if __name__ == "__main__":
    # Test the PMCSearcher
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    searcher = PMCSearcher()

    print("Testing PMC search...")
    query = "cancer immunotherapy"
    papers = searcher.search(query, max_results=3)
    print(f"Found {len(papers)} papers for query '{query}':")

    for i, paper in enumerate(papers, 1):
        print(f"\n{i}. {paper.title}")
        print(f"   PMCID: {paper.paper_id}")
        print(f"   DOI: {paper.doi}")
        print(f"   Authors: {', '.join(paper.authors[:3])}")
        print(f"   PDF URL: {paper.pdf_url}")
        if paper.abstract:
            print(f"   Abstract preview: {paper.abstract[:150]}...")

    # Test PDF download if we have papers
    if papers:
        print("\n\nTesting PMC PDF download...")
        test_pmcid = papers[0].paper_id
        try:
            pdf_path = searcher.download_pdf(test_pmcid, "/tmp/pmc_test")
            print(f"PDF downloaded to: {pdf_path}")

            # Test text extraction
            print("\nTesting text extraction...")
            text = searcher.read_paper(test_pmcid, "/tmp/pmc_test")
            print(f"Extracted text length: {len(text)} characters")
            print(f"Text preview: {text[:200]}...")
        except Exception as e:
            print(f"PDF download/test failed: {e}")