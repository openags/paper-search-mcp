# paper_search_mcp/academic_platforms/oaipmh.py
"""Base searcher for OAI-PMH compatible repositories.

OAI-PMH (Open Archives Initiative Protocol for Metadata Harvesting) is a
standard protocol for harvesting metadata from digital repositories.
This base class provides common functionality for platforms that support
OAI-PMH, such as BASE and CiteSeerX.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import requests
import xml.etree.ElementTree as ET
import time
import logging
from ..paper import Paper
from .base import PaperSource

logger = logging.getLogger(__name__)

# XML namespaces for OAI-PMH
OAI_NS = 'http://www.openarchives.org/OAI/2.0/'
DC_NS = 'http://purl.org/dc/elements/1.1/'
NS_MAP = {
    'oai': OAI_NS,
    'dc': DC_NS
}

def _register_namespaces():
    """Register XML namespaces for pretty printing."""
    for prefix, uri in NS_MAP.items():
        ET.register_namespace(prefix, uri)

_register_namespaces()


class OAIPMHSearcher(PaperSource):
    """Base searcher for OAI-PMH compatible repositories.

    This class implements the OAI-PMH protocol for searching and retrieving
    metadata records. Subclasses should specify the repository URL and
    may override parsing methods for repository-specific metadata.

    Attributes:
        base_url: OAI-PMH endpoint URL
        metadata_prefix: Metadata format (default: oai_dc for Dublin Core)
        session: HTTP session for requests
    """

    def __init__(self, base_url: str, metadata_prefix: str = "oai_dc"):
        """Initialize OAI-PMH searcher.

        Args:
            base_url: OAI-PMH endpoint URL (e.g., https://api.base-search.net/oai)
            metadata_prefix: Metadata format prefix (default: oai_dc)
        """
        self.base_url = base_url
        self.metadata_prefix = metadata_prefix
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/0.1.3 (OAI-PMH client; https://github.com/openags/paper-search-mcp)',
            'Accept': 'application/xml'
        })

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """Search repository using OAI-PMH ListRecords.

        Args:
            query: Search query string
            max_results: Maximum number of results to return
            **kwargs: Additional parameters:
                - set: OAI-PMH set specification (optional)
                - from_date: Date from which to harvest (YYYY-MM-DD)
                - until_date: Date until which to harvest (YYYY-MM-DD)

        Returns:
            List of Paper objects
        """
        papers = []
        params = {
            'verb': 'ListRecords',
            'metadataPrefix': self.metadata_prefix,
        }

        # Add optional OAI-PMH parameters
        if 'set' in kwargs and kwargs['set']:
            params['set'] = kwargs['set']
        if 'from_date' in kwargs and kwargs['from_date']:
            params['from'] = kwargs['from_date']
        if 'until_date' in kwargs and kwargs['until_date']:
            params['until'] = kwargs['until_date']

        # For simple text queries, we need to filter after retrieval
        # OAI-PMH doesn't support full-text search directly
        # We'll retrieve records and filter locally
        retry_count = 0
        max_retries = 3
        resumption_token = None

        try:
            while len(papers) < max_results:
                # Prepare request parameters
                request_params = params.copy()
                if resumption_token:
                    request_params = {
                        'verb': 'ListRecords',
                        'resumptionToken': resumption_token
                    }

                # Make OAI-PMH request
                response = self.session.get(
                    self.base_url,
                    params=request_params,
                    timeout=30
                )
                response.raise_for_status()

                # Parse XML response
                root = ET.fromstring(response.content)

                # Check for OAI-PMH errors
                error_elem = root.find(f'.//{{{OAI_NS}}}error')
                if error_elem is not None:
                    error_code = error_elem.get('code', 'unknown')
                    error_msg = error_elem.text or f"OAI-PMH error: {error_code}"
                    logger.warning(f"OAI-PMH error: {error_code} - {error_msg}")
                    break

                # Process records
                records = root.findall(f'.//{{{OAI_NS}}}record')
                for record in records:
                    if len(papers) >= max_results:
                        break

                    try:
                        paper = self._parse_oai_record(record)
                        if paper:
                            # Apply query filter (OAI-PMH doesn't support search natively)
                            if query and self._matches_query(paper, query):
                                papers.append(paper)
                            elif not query:  # If no query, include all records
                                papers.append(paper)
                    except Exception as e:
                        logger.warning(f"Error parsing OAI-PMH record: {e}")
                        continue

                # Check for resumption token
                token_elem = root.find(f'.//{{{OAI_NS}}}resumptionToken')
                if token_elem is not None and token_elem.text:
                    resumption_token = token_elem.text
                    # Respect requested delay if specified
                    cursor = token_elem.get('cursor')
                    if cursor and token_elem.get('completeListSize'):
                        total = int(token_elem.get('completeListSize'))
                        logger.debug(f"OAI-PMH pagination: {cursor}/{total}")
                else:
                    break  # No more records

                # Rate limiting - small delay between requests
                time.sleep(0.5)

        except requests.exceptions.RequestException as e:
            logger.error(f"OAI-PMH request failed: {e}")
        except ET.ParseError as e:
            logger.error(f"Failed to parse OAI-PMH XML response: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in OAI-PMH search: {e}")

        return papers[:max_results]

    def _parse_oai_record(self, record: ET.Element) -> Optional[Paper]:
        """Parse OAI-PMH record to Paper object.

        This base implementation handles Dublin Core (oai_dc) metadata.
        Subclasses can override for repository-specific metadata formats.

        Args:
            record: OAI-PMH record XML element

        Returns:
            Paper object or None if parsing fails
        """
        try:
            # Extract header info
            header = record.find(f'{{{OAI_NS}}}header')
            if header is None:
                return None

            identifier_elem = header.find(f'{{{OAI_NS}}}identifier')
            paper_id = identifier_elem.text if identifier_elem is not None else ''

            # Check if record is deleted
            if header.get('status') == 'deleted':
                return None

            # Extract metadata (Dublin Core)
            metadata = record.find(f'.//{{{OAI_NS}}}metadata')
            if metadata is None:
                return None

            # Dublin Core elements
            dc_root = metadata.find(f'.//{{{DC_NS}}}')
            if dc_root is None:
                # Try without namespace
                dc_root = metadata.find('.//')
                if dc_root is None:
                    return None

            # Extract Dublin Core fields
            title_elem = dc_root.find(f'{{{DC_NS}}}title') or dc_root.find('title')
            title = title_elem.text if title_elem is not None else ''

            author_elems = dc_root.findall(f'{{{DC_NS}}}creator') or dc_root.findall('creator')
            authors = [elem.text for elem in author_elems if elem.text]

            description_elem = dc_root.find(f'{{{DC_NS}}}description') or dc_root.find('description')
            abstract = description_elem.text if description_elem is not None else ''

            date_elem = dc_root.find(f'{{{DC_NS}}}date') or dc_root.find('date')
            date_str = date_elem.text if date_elem is not None else ''
            published_date = self._parse_date(date_str)

            # Extract DOI from identifier or description
            doi = ''
            identifier_elems = dc_root.findall(f'{{{DC_NS}}}identifier') or dc_root.findall('identifier')
            for elem in identifier_elems:
                if elem.text and 'doi.org' in elem.text.lower():
                    doi = elem.text
                    break

            # If no DOI found, try to extract from text
            if not doi and abstract:
                from ..utils import extract_doi
                doi = extract_doi(abstract)

            # Build URL - use identifier if it's a URL, otherwise construct
            url = ''
            if identifier_elem and identifier_elem.text:
                if identifier_elem.text.startswith(('http://', 'https://')):
                    url = identifier_elem.text
                else:
                    url = f"http://hdl.handle.net/{identifier_elem.text}"

            # Try to find PDF link
            pdf_url = ''
            format_elems = dc_root.findall(f'{{{DC_NS}}}format') or dc_root.findall('format')
            for elem in format_elems:
                if elem.text and 'pdf' in elem.text.lower():
                    # Check if there's a related identifier with PDF
                    for id_elem in identifier_elems:
                        if id_elem.text and '.pdf' in id_elem.text.lower():
                            pdf_url = id_elem.text
                            break

            # Create Paper object
            paper = Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url,
                source=self.__class__.__name__.replace('Searcher', '').lower()
            )

            # Extract additional metadata
            self._enrich_paper_from_oai(paper, dc_root)

            return paper

        except Exception as e:
            logger.warning(f"Error parsing OAI-PMH record: {e}")
            return None

    def _enrich_paper_from_oai(self, paper: Paper, dc_root: ET.Element):
        """Enrich Paper object with additional OAI-DC metadata.

        Subclasses can override to extract repository-specific fields.

        Args:
            paper: Paper object to enrich
            dc_root: Dublin Core XML element
        """
        # Extract subjects/categories
        subject_elems = dc_root.findall(f'{{{DC_NS}}}subject') or dc_root.findall('subject')
        if subject_elems:
            paper.categories = [elem.text for elem in subject_elems if elem.text]

        # Extract publisher
        publisher_elem = dc_root.find(f'{{{DC_NS}}}publisher') or dc_root.find('publisher')
        if publisher_elem and publisher_elem.text:
            if not paper.extra:
                paper.extra = {}
            paper.extra['publisher'] = publisher_elem.text

        # Extract language
        language_elem = dc_root.find(f'{{{DC_NS}}}language') or dc_root.find('language')
        if language_elem and language_elem.text:
            if not paper.extra:
                paper.extra = {}
            paper.extra['language'] = language_elem.text

        # Extract type
        type_elem = dc_root.find(f'{{{DC_NS}}}type') or dc_root.find('type')
        if type_elem and type_elem.text:
            if not paper.extra:
                paper.extra = {}
            paper.extra['type'] = type_elem.text

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date string to datetime object.

        Handles various date formats commonly found in OAI-PMH repositories.

        Args:
            date_str: Date string

        Returns:
            datetime object or None if parsing fails
        """
        if not date_str:
            return None

        date_formats = [
            '%Y-%m-%d',      # 2023-10-15
            '%Y-%m',         # 2023-10
            '%Y',            # 2023
            '%Y-%m-%dT%H:%M:%SZ',  # ISO format with time
            '%Y-%m-%d %H:%M:%S',   # SQL datetime
        ]

        for fmt in date_formats:
            try:
                # Adjust format length if needed
                if fmt == '%Y-%m-%d' and len(date_str) == 10:
                    return datetime.strptime(date_str, fmt)
                elif fmt == '%Y-%m' and len(date_str) == 7:
                    return datetime.strptime(date_str, fmt)
                elif fmt == '%Y' and len(date_str) == 4:
                    return datetime.strptime(date_str, fmt)
                elif fmt == '%Y-%m-%dT%H:%M:%SZ' and 'T' in date_str:
                    return datetime.strptime(date_str, fmt)
                elif fmt == '%Y-%m-%d %H:%M:%S' and ' ' in date_str:
                    return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        # Try to extract year from any string
        import re
        year_match = re.search(r'\b(19|20)\d{2}\b', date_str)
        if year_match:
            try:
                year = int(year_match.group())
                return datetime(year, 1, 1)
            except ValueError:
                pass

        return None

    def _matches_query(self, paper: Paper, query: str) -> bool:
        """Check if paper matches search query.

        Since OAI-PMH doesn't support native search, we filter locally.
        Subclasses can override for more sophisticated matching.

        Args:
            paper: Paper object
            query: Search query (lowercase)

        Returns:
            True if paper matches query
        """
        query_lower = query.lower()
        return (query_lower in paper.title.lower() or
                query_lower in paper.abstract.lower() or
                any(query_lower in author.lower() for author in paper.authors))

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """Download PDF for a paper.

        OAI-PMH repositories often provide PDF links in metadata.
        Subclasses should override if they can provide direct PDF access.

        Args:
            paper_id: Paper identifier
            save_path: Directory to save PDF

        Returns:
            Path to saved PDF file

        Raises:
            NotImplementedError: If PDF download not supported
        """
        # Try to find PDF URL via search
        papers = self.search(paper_id, max_results=1)
        if papers and papers[0].pdf_url:
            import os
            response = self.session.get(papers[0].pdf_url, timeout=30)
            response.raise_for_status()
            os.makedirs(save_path, exist_ok=True)
            filename = f"{paper_id.replace('/', '_')}.pdf"
            output_file = os.path.join(save_path, filename)
            with open(output_file, 'wb') as f:
                f.write(response.content)
            return output_file

        raise NotImplementedError(
            f"{self.__class__.__name__} does not support direct PDF downloads."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Read paper text from PDF.

        Args:
            paper_id: Paper identifier
            save_path: Directory where PDF is/will be saved

        Returns:
            Extracted text content

        Raises:
            NotImplementedError: If PDF reading not supported
        """
        try:
            # Try to download PDF first
            pdf_path = self.download_pdf(paper_id, save_path)

            # Extract text from PDF
            from PyPDF2 import PdfReader
            reader = PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text.strip()
        except Exception as e:
            logger.error(f"Error reading paper {paper_id}: {e}")
            raise NotImplementedError(
                f"Cannot read paper from {self.__class__.__name__}: {e}"
            )


if __name__ == "__main__":
    """Test the OAIPMHSearcher base class."""
    import logging
    logging.basicConfig(level=logging.INFO)

    # Test with a known OAI-PMH repository
    class TestOAISearcher(OAIPMHSearcher):
        def __init__(self):
            super().__init__(
                base_url="https://api.base-search.net/oai",
                metadata_prefix="oai_dc"
            )

    searcher = TestOAISearcher()

    # Test search
    print("Testing OAI-PMH search...")
    papers = searcher.search("machine learning", max_results=3)
    print(f"Found {len(papers)} papers")
    for i, paper in enumerate(papers):
        print(f"{i+1}. {paper.title}")
        print(f"   Authors: {', '.join(paper.authors[:3])}")
        print(f"   DOI: {paper.doi}")
        print()