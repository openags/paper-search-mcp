# paper_search_mcp/academic_platforms/europepmc.py
from typing import List, Optional, Dict, Any
import requests
import logging
from datetime import datetime
from pathlib import Path
from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)


class EuropePMCSearcher(PaperSource):
    """Searcher for Europe PMC (European biomedical literature database)"""

    BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/1.0 (mailto:openags@example.com)',
            'Accept': 'application/json'
        })

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """
        Search Europe PMC for biomedical literature.

        Args:
            query: Search query string
            max_results: Maximum results to return (Europe PMC default: 25, max: 1000)
            **kwargs: Additional parameters:
                - year: Filter by publication year
                - has_fulltext: Filter by full text availability (True/False)
                - open_access: Filter by open access status (True/False)
                - source: Filter by source (e.g., 'MED', 'PMC', 'AGR')

        Returns:
            List[Paper]: List of found papers with metadata
        """
        papers = []

        try:
            # Prepare search parameters
            params = {
                'query': query,
                'pageSize': min(max_results, 100),  # Use pageSize parameter
                'format': 'json',
                'resultType': 'core',
            }

            # Add optional filters
            if 'year' in kwargs:
                params['year'] = kwargs['year']
            if 'has_fulltext' in kwargs:
                params['has_fulltext'] = 'y' if kwargs['has_fulltext'] else 'n'
            if 'open_access' in kwargs:
                params['open_access'] = 'y' if kwargs['open_access'] else 'n'
            if 'source' in kwargs:
                params['source'] = kwargs['source']

            # Europe PMC supports sorting
            if 'sort' in kwargs:
                params['sort'] = kwargs['sort']

            # Make API request
            response = self.session.get(f"{self.BASE_URL}/search", params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            # Parse results
            result_list = data.get('resultList', {}).get('result', [])
            for item in result_list:
                try:
                    paper = self._parse_item(item)
                    if paper:
                        papers.append(paper)
                        if len(papers) >= max_results:
                            break
                except Exception as e:
                    logger.warning(f"Error parsing Europe PMC item: {e}")
                    continue

            logger.info(f"Europe PMC search returned {len(papers)} papers for query: {query}")

        except requests.RequestException as e:
            logger.error(f"Europe PMC search request error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in Europe PMC search: {e}")

        return papers

    def _parse_item(self, item: Dict[str, Any]) -> Optional[Paper]:
        """Parse a single Europe PMC API result item into a Paper object."""
        try:
            # Extract ID (could be PMID, PMCID, or DOI)
            paper_id = item.get('id', '')
            if not paper_id:
                return None

            # Determine ID type and set appropriate fields
            id_type = item.get('source', '')
            if id_type == 'MED':
                # PubMed ID
                paper_id = f"PMID:{paper_id}"
            elif id_type == 'PMC':
                # PubMed Central ID
                if not paper_id.startswith('PMC'):
                    paper_id = f"PMC{paper_id}"
            # For other types (DOI, etc.), use as is

            # Extract title
            title = item.get('title', '').strip()
            if not title:
                return None

            # Extract authors
            authors = []
            author_list = item.get('authorList', {}).get('author', [])
            if isinstance(author_list, list):
                for author in author_list:
                    if isinstance(author, dict):
                        full_name = author.get('fullName', '')
                        if full_name:
                            authors.append(full_name)
                    elif isinstance(author, str):
                        authors.append(author)
            elif isinstance(author_list, str):
                authors = [author_list]

            # Extract abstract
            abstract = item.get('abstractText', '')

            # Extract DOI
            doi = item.get('doi', '')
            if not doi:
                # Europe PMC sometimes puts DOI in other fields
                doi = item.get('doiId', '')
            if not doi and abstract:
                doi = extract_doi(abstract)

            # Extract publication date
            pub_date = None
            pub_year = item.get('pubYear')
            pub_month = item.get('pubMonth', '1')
            pub_day = item.get('pubDay', '1')
            if pub_year:
                try:
                    date_str = f"{pub_year}-{str(pub_month).zfill(2)}-{str(pub_day).zfill(2)}"
                    pub_date = datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    try:
                        pub_date = datetime.strptime(pub_year, '%Y')
                    except ValueError:
                        pass

            # Extract URLs
            url = item.get('fullTextUrlList', {}).get('fullTextUrl', [])
            landing_url = ''
            pdf_url = ''

            if isinstance(url, list):
                for url_item in url:
                    if isinstance(url_item, dict):
                        url_type = url_item.get('documentStyle', '')
                        url_value = url_item.get('url', '')
                        if url_type == 'html' and not landing_url:
                            landing_url = url_value
                        elif url_type == 'pdf' and not pdf_url:
                            pdf_url = url_value
            elif isinstance(url, dict):
                url_type = url.get('documentStyle', '')
                url_value = url.get('url', '')
                if url_type == 'html':
                    landing_url = url_value
                elif url_type == 'pdf':
                    pdf_url = url_value

            # If no landing URL found, construct one
            if not landing_url:
                if doi:
                    landing_url = f"https://doi.org/{doi}"
                elif id_type == 'MED':
                    landing_url = f"https://pubmed.ncbi.nlm.nih.gov/{paper_id.replace('PMID:', '')}/"
                elif id_type == 'PMC':
                    pmcid = paper_id.replace('PMC', '')
                    landing_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"

            # Extract journal information
            journal_title = item.get('journalTitle', '')
            journal_issn = item.get('journalISSN', '')

            # Extract keywords
            keywords = []
            keyword_list = item.get('keywordList', {}).get('keyword', [])
            if isinstance(keyword_list, list):
                keywords = [kw for kw in keyword_list if isinstance(kw, str)]
            elif isinstance(keyword_list, str):
                keywords = [keyword_list]

            # Extract open access information
            is_open_access = item.get('isOpenAccess', 'N') == 'Y'
            open_access_licence = item.get('openAccessLicence', '')

            # Create Paper object
            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=abstract,
                url=landing_url,
                pdf_url=pdf_url,
                published_date=pub_date,
                source='europepmc',
                categories=[journal_title] if journal_title else [],
                keywords=keywords[:10],
                doi=doi,
                extra={
                    'journal': journal_title,
                    'issn': journal_issn,
                    'is_open_access': is_open_access,
                    'open_access_licence': open_access_licence,
                    'citation_count': item.get('citedByCount', 0),
                    'pmid': item.get('pmid', ''),
                    'pmcid': item.get('pmcid', ''),
                }
            )

        except Exception as e:
            logger.warning(f"Error parsing Europe PMC item data: {e}")
            return None

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download PDF of an Europe PMC open access article.

        Args:
            paper_id: Europe PMC paper ID (can be PMID:..., PMC..., or DOI)
            save_path: Directory to save the PDF

        Returns:
            str: Path to the downloaded PDF file

        Raises:
            Exception: If download fails
        """
        try:
            # First, get paper details to find PDF URL
            paper_details = self._get_paper_details(paper_id)
            if not paper_details:
                raise ValueError(f"Could not retrieve details for Europe PMC paper {paper_id}")

            # Find PDF URL from fullTextUrlList
            pdf_url = ''
            full_text_urls = paper_details.get('fullTextUrlList', {}).get('fullTextUrl', [])
            if isinstance(full_text_urls, list):
                for url_item in full_text_urls:
                    if isinstance(url_item, dict):
                        url_type = url_item.get('documentStyle', '')
                        url_value = url_item.get('url', '')
                        if url_type == 'pdf':
                            pdf_url = url_value
                            break

            if not pdf_url:
                # Check if paper has a PMCID and try standard PMC PDF URL
                pmcid = paper_details.get('pmcid', '')
                if pmcid:
                    if not pmcid.startswith('PMC'):
                        pmcid = f"PMC{pmcid}"
                    pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"

            if not pdf_url:
                raise ValueError(f"Europe PMC paper {paper_id} does not have an accessible PDF")

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
            title = paper_details.get('title', 'paper').replace(' ', '_')[:50]
            filename = f"europepmc_{paper_id}_{title}.pdf"
            filename = ''.join(c for c in filename if c.isalnum() or c in ('_', '-', '.'))
            filepath = save_dir / filename

            # Save PDF
            with open(filepath, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded Europe PMC PDF to {filepath}")
            return str(filepath)

        except requests.RequestException as e:
            error_msg = f"Failed to download Europe PMC PDF for {paper_id}: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"Error downloading Europe PMC PDF for {paper_id}: {e}"
            logger.error(error_msg)
            raise Exception(error_msg)

    def _get_paper_details(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information for an Europe PMC paper by ID."""
        try:
            # Europe PMC API for fetching a single record
            params = {
                'format': 'json',
                'resultType': 'core',
            }

            # Determine query based on ID type
            if paper_id.startswith('PMID:'):
                query = f"ext_id:{paper_id.replace('PMID:', '')} src:med"
            elif paper_id.startswith('PMC'):
                query = f"ext_id:{paper_id} src:pmc"
            elif paper_id.startswith('DOI:'):
                query = f"doi:{paper_id.replace('DOI:', '')}"
            else:
                # Assume it's a DOI or other identifier
                if '/' in paper_id and paper_id.startswith('10.'):
                    query = f"doi:{paper_id}"
                else:
                    query = f"ext_id:{paper_id}"

            params['query'] = query

            response = self.session.get(f"{self.BASE_URL}/search", params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            result_list = data.get('resultList', {}).get('result', [])
            if result_list and len(result_list) > 0:
                return result_list[0]

            return None

        except requests.RequestException as e:
            logger.warning(f"Failed to get Europe PMC paper details for {paper_id}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error getting Europe PMC paper details: {e}")
            return None

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from an Europe PMC paper.

        Args:
            paper_id: Europe PMC paper ID
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
                return f"Text extraction from Europe PMC article {paper_id} produced minimal content."

            return text

        except Exception as e:
            error_msg = f"Failed to read Europe PMC paper {paper_id}: {e}"
            logger.error(error_msg)
            return error_msg


if __name__ == "__main__":
    # Test the EuropePMCSearcher
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    searcher = EuropePMCSearcher()

    print("Testing Europe PMC search...")
    query = "cancer genomics"
    papers = searcher.search(query, max_results=2)
    print(f"Found {len(papers)} papers for query '{query}':")

    for i, paper in enumerate(papers, 1):
        print(f"\n{i}. {paper.title}")
        print(f"   ID: {paper.paper_id}")
        print(f"   DOI: {paper.doi}")
        print(f"   Authors: {', '.join(paper.authors[:3])}")
        print(f"   PDF URL: {paper.pdf_url}")
        if paper.abstract:
            print(f"   Abstract preview: {paper.abstract[:150]}...")

    # Test PDF download if we have papers
    if papers:
        print("\n\nTesting Europe PMC PDF download...")
        test_id = papers[0].paper_id
        try:
            pdf_path = searcher.download_pdf(test_id, "/tmp/europepmc_test")
            print(f"PDF downloaded to: {pdf_path}")

            # Test text extraction
            print("\nTesting text extraction...")
            text = searcher.read_paper(test_id, "/tmp/europepmc_test")
            print(f"Extracted text length: {len(text)} characters")
            if len(text) > 200:
                print(f"Text preview: {text[:200]}...")
        except Exception as e:
            print(f"PDF download/test failed: {e}")