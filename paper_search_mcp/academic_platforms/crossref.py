# paper_search_mcp/academic_platforms/crossref.py
from typing import List, Optional, Dict, Any
from datetime import datetime
import requests
import time
import random
from ..paper import Paper
import logging

logger = logging.getLogger(__name__)

class PaperSource:
    """Abstract base class for paper sources"""
    def search(self, query: str, **kwargs) -> List[Paper]:
        raise NotImplementedError

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

    def read_paper(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

class CrossRefSearcher(PaperSource):
    """Searcher for CrossRef database papers"""
    
    BASE_URL = "https://api.crossref.org"
    
    # User agent for polite API usage as per CrossRef etiquette
    USER_AGENT = "paper-search-mcp/0.1.3 (https://github.com/Dragonatorul/paper-search-mcp; mailto:paper-search@example.org)"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.USER_AGENT,
            'Accept': 'application/json'
        })
    
    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """
        Search CrossRef database for papers.
        
        Args:
            query: Search query string
            max_results: Maximum number of results to return (default: 10)
            **kwargs: Additional parameters like filters, sort, etc.
            
        Returns:
            List of Paper objects
        """
        try:
            params = {
                'query': query,
                'rows': min(max_results, 1000),  # CrossRef API max is 1000
                'sort': 'relevance',
                'order': 'desc'
            }
            
            # Add any additional filters from kwargs
            if 'filter' in kwargs:
                params['filter'] = kwargs['filter']
            if 'sort' in kwargs:
                params['sort'] = kwargs['sort']
            if 'order' in kwargs:
                params['order'] = kwargs['order']
                
            # Add polite pool parameter
            params['mailto'] = 'paper-search@example.org'
            
            url = f"{self.BASE_URL}/works"
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 429:
                # Rate limited - wait and retry once
                logger.warning("Rate limited by CrossRef API, waiting 2 seconds...")
                time.sleep(2)
                response = self.session.get(url, params=params, timeout=30)
            
            response.raise_for_status()
            data = response.json()
            
            papers = []
            items = data.get('message', {}).get('items', [])
            
            for item in items:
                try:
                    paper = self._parse_crossref_item(item)
                    if paper:
                        papers.append(paper)
                except Exception as e:
                    logger.warning(f"Error parsing CrossRef item: {e}")
                    continue
                    
            return papers
            
        except requests.RequestException as e:
            logger.error(f"Error searching CrossRef: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in CrossRef search: {e}")
            return []
    
    def _parse_crossref_item(self, item: Dict[str, Any]) -> Optional[Paper]:
        """Parse a CrossRef API item into a Paper object."""
        try:
            # Extract basic information
            doi = item.get('DOI', '')
            title = self._extract_title(item)
            authors = self._extract_authors(item)
            abstract = item.get('abstract', '')
            
            # Extract publication date
            published_date = self._extract_date(item, 'published')
            if not published_date:
                published_date = self._extract_date(item, 'issued')
            if not published_date:
                published_date = self._extract_date(item, 'created')
            
            # Default to epoch if no date found
            if not published_date:
                published_date = datetime(1970, 1, 1)
            
            # Extract URLs
            url = item.get('URL', f"https://doi.org/{doi}" if doi else '')
            pdf_url = self._extract_pdf_url(item)
            
            # Extract additional metadata
            container_title = self._extract_container_title(item)
            publisher = item.get('publisher', '')
            categories = [item.get('type', '')]
            
            # Extract subjects/keywords if available
            subjects = item.get('subject', [])
            if isinstance(subjects, list):
                keywords = subjects
            else:
                keywords = []
            
            return Paper(
                paper_id=doi,
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url,
                source='crossref',
                categories=categories,
                keywords=keywords,
                citations=item.get('is-referenced-by-count', 0),
                extra={
                    'publisher': publisher,
                    'container_title': container_title,
                    'volume': item.get('volume', ''),
                    'issue': item.get('issue', ''),
                    'page': item.get('page', ''),
                    'issn': item.get('ISSN', []),
                    'isbn': item.get('ISBN', []),
                    'crossref_type': item.get('type', ''),
                    'member': item.get('member', ''),
                    'prefix': item.get('prefix', '')
                }
            )
            
        except Exception as e:
            logger.error(f"Error parsing CrossRef item: {e}")
            return None
    
    def _extract_title(self, item: Dict[str, Any]) -> str:
        """Extract title from CrossRef item."""
        titles = item.get('title', [])
        if isinstance(titles, list) and titles:
            return titles[0]
        return str(titles) if titles else ''
    
    def _extract_authors(self, item: Dict[str, Any]) -> List[str]:
        """Extract author names from CrossRef item."""
        authors = []
        author_list = item.get('author', [])
        
        for author in author_list:
            if isinstance(author, dict):
                given = author.get('given', '')
                family = author.get('family', '')
                if given and family:
                    authors.append(f"{given} {family}")
                elif family:
                    authors.append(family)
                elif given:
                    authors.append(given)
                    
        return authors
    
    def _extract_date(self, item: Dict[str, Any], date_field: str) -> Optional[datetime]:
        """Extract date from CrossRef item."""
        date_info = item.get(date_field, {})
        if not date_info:
            return None
            
        date_parts = date_info.get('date-parts', [])
        if not date_parts or not date_parts[0]:
            return None
            
        parts = date_parts[0]
        try:
            year = parts[0] if len(parts) > 0 else 1970
            month = parts[1] if len(parts) > 1 else 1
            day = parts[2] if len(parts) > 2 else 1
            return datetime(year, month, day)
        except (ValueError, IndexError):
            return None
    
    def _extract_container_title(self, item: Dict[str, Any]) -> str:
        """Extract container title (journal/book title) from CrossRef item."""
        container_titles = item.get('container-title', [])
        if isinstance(container_titles, list) and container_titles:
            return container_titles[0]
        return str(container_titles) if container_titles else ''
    
    def _extract_pdf_url(self, item: Dict[str, Any]) -> str:
        """Extract PDF URL from CrossRef item."""
        # Check for link in the resource field
        resource = item.get('resource', {})
        if resource:
            primary = resource.get('primary', {})
            if primary and primary.get('URL', '').endswith('.pdf'):
                return primary['URL']
        
        # Check in links array
        links = item.get('link', [])
        for link in links:
            if isinstance(link, dict):
                content_type = link.get('content-type', '')
                if 'pdf' in content_type.lower():
                    return link.get('URL', '')
                    
        return ''
    
    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        CrossRef doesn't provide direct PDF downloads.
        
        Args:
            paper_id: DOI of the paper
            save_path: Directory to save the PDF
            
        Raises:
            NotImplementedError: Always raises this error as CrossRef doesn't provide direct PDF access
        """
        message = ("CrossRef does not provide direct PDF downloads. "
                  "CrossRef is a citation database that provides metadata about academic papers. "
                  "To access the full text, please use the paper's DOI or URL to visit the publisher's website.")
        raise NotImplementedError(message)
    
    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        CrossRef doesn't provide direct paper content access.
        
        Args:
            paper_id: DOI of the paper
            save_path: Directory for potential PDF storage (unused)
            
        Returns:
            str: Error message indicating PDF reading is not supported
        """
        message = ("CrossRef papers cannot be read directly through this tool. "
                  "CrossRef is a citation database that provides metadata about academic papers. "
                  "Only metadata and abstracts are available through CrossRef's API. "
                  "To access the full text, please use the paper's DOI or URL to visit the publisher's website.")
        return message

    def get_paper_by_doi(self, doi: str) -> Optional[Paper]:
        """
        Get a specific paper by DOI.
        
        Args:
            doi: Digital Object Identifier
            
        Returns:
            Paper object if found, None otherwise
        """
        try:
            url = f"{self.BASE_URL}/works/{doi}"
            params = {'mailto': 'paper-search@example.org'}
            
            response = self.session.get(url, params=params, timeout=30)
            
            if response.status_code == 404:
                logger.warning(f"DOI not found in CrossRef: {doi}")
                return None
                
            response.raise_for_status()
            data = response.json()
            
            item = data.get('message', {})
            return self._parse_crossref_item(item)
            
        except requests.RequestException as e:
            logger.error(f"Error fetching DOI {doi} from CrossRef: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error fetching DOI {doi}: {e}")
            return None

if __name__ == "__main__":
    # Test CrossRefSearcher functionality
    # 测试CrossRefSearcher功能
    searcher = CrossRefSearcher()
    
    # Test search functionality
    # 测试搜索功能
    print("Testing search functionality...")
    query = "machine learning"
    max_results = 5
    papers = []
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"Found {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (DOI: {paper.doi})")
            print(f"   Authors: {', '.join(paper.authors[:3])}{'...' if len(paper.authors) > 3 else ''}")
            print(f"   Published: {paper.published_date.year}")
            print(f"   Citations: {paper.citations}")
            publisher = paper.extra.get('publisher', 'N/A') if paper.extra else 'N/A'
            print(f"   Publisher: {publisher}")
            print()
    except Exception as e:
        print(f"Error during search: {e}")
    
    # Test DOI lookup functionality
    # 测试DOI查找功能
    if papers:
        print("Testing DOI lookup functionality...")
        test_doi = papers[0].doi
        try:
            paper = searcher.get_paper_by_doi(test_doi)
            if paper:
                print(f"Successfully retrieved paper by DOI: {paper.title}")
            else:
                print("Failed to retrieve paper by DOI")
        except Exception as e:
            print(f"Error during DOI lookup: {e}")
    
    # Test PDF download functionality (will return unsupported message)
    # 测试PDF下载功能（会返回不支持的提示）
    if papers:
        print("\nTesting PDF download functionality...")
        paper_id = papers[0].doi
        try:
            pdf_path = searcher.download_pdf(paper_id, "./downloads")
        except NotImplementedError as e:
            print(f"Expected error: {e}")
    
    # Test paper reading functionality (will return unsupported message)
    # 测试论文阅读功能（会返回不支持的提示）
    if papers:
        print("\nTesting paper reading functionality...")
        paper_id = papers[0].doi
        message = searcher.read_paper(paper_id)
        print(f"Message: {message}")