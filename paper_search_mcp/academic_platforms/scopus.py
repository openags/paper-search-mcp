# paper_search_mcp/academic_platforms/scopus.py
from typing import List, Optional
from datetime import datetime
import requests
import time
import logging
import urllib.parse
from ..paper import Paper
from ..config import get_env
from .base import PaperSource

logger = logging.getLogger(__name__)


class ScopusSearcher(PaperSource):
    """Scopus paper search implementation using REST API"""

    SCOPUS_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
    USER_AGENT = "paper-search-mcp/0.1.3 (https://github.com/openags/paper-search-mcp)"

    def __init__(self, api_key: str = None):
        """
        Initialize Scopus searcher
        
        Args:
            api_key: Scopus API key. If not provided, will try to get from the
                     PAPER_SEARCH_MCP_SCOPUS_API_KEY (or legacy SCOPUS_API_KEY)
                     environment variable.
        """
        self.api_key = api_key or get_env("SCOPUS_API_KEY", "")

        if not self.api_key:
            raise ValueError("Scopus API key not provided. Set the PAPER_SEARCH_MCP_SCOPUS_API_KEY (or SCOPUS_API_KEY) environment variable or pass it during instantiation.")
        
        self._setup_session()

    def _setup_session(self):
        """Initialize session identifying this client honestly"""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json",
                "X-ELS-APIKey": self.api_key,
            }
        )

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date from Scopus format (e.g., '2025-06-02')"""
        try:
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            try:
                # Try parsing year only format
                return datetime.strptime(date_str.strip(), "%Y")
            except ValueError:
                logger.warning(f"Could not parse date: {date_str}")
                return None

    def _parse_paper(self, item) -> Optional[Paper]:
        """Parse single paper entry from Scopus API response"""
        try:
            # Extract basic metadata
            paper_id = item.get('dc:identifier', '').replace('SCOPUS_ID:', '')
            title = item.get('dc:title', '')
            
            # Parse authors
            authors = []
            if 'author' in item and item['author']:
                for author in item['author']:
                    if isinstance(author, dict) and 'authname' in author:
                        authors.append(author['authname'])
                    elif isinstance(author, str):
                        authors.append(author)
            
            # Extract other metadata
            abstract = item.get('dc:description', '')
            doi = item.get('prism:doi', '')
            url = item.get('prism:url', '')
            pdf_url = ''  # Scopus API does not typically provide direct PDF links

            # Parse publication date
            published_date = None
            published_date_str = item.get('prism:coverDate', '')
            if published_date_str:
                published_date = self._parse_date(published_date_str)

            # Extract subject areas
            categories = []
            if 'subject-area' in item and item['subject-area']:
                for subject in item['subject-area']:
                    if isinstance(subject, dict) and '@abbrev' in subject:
                        categories.append(subject['@abbrev'])

            # Extract citation count
            citations = 0
            if 'citedby-count' in item:
                try:
                    citations = int(item['citedby-count'])
                except (ValueError, TypeError):
                    citations = 0

            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=abstract,
                url=url,
                pdf_url=pdf_url,
                published_date=published_date,
                updated_date=None,  # Scopus API might not provide this directly
                source='scopus',
                categories=categories,
                keywords=[],  # Scopus API might provide keywords in other fields
                doi=doi,
                citations=citations
            )

        except Exception as e:
            logger.warning(f"Failed to parse Scopus paper: {e}")
            return None

    def request_api(self, params: dict) -> dict:
        """
        Make a request to the Scopus Search API with rate limiting
        """
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                response = self.session.get(self.SCOPUS_SEARCH_URL, params=params, timeout=(10, 30))

                # Check if it's a 429 error (rate limited)
                if response.status_code == 429:
                    # Elsevier quotas reset weekly; if the quota is exhausted,
                    # retrying with backoff cannot help.
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    if remaining == "0":
                        reset = response.headers.get("X-RateLimit-Reset")
                        reset_info = f" Quota resets at: {reset}." if reset else ""
                        logger.error(f"Rate limited (429): weekly API quota exhausted.{reset_info} Retrying will not help until the quota resets.")
                        return {"error": "rate_limited", "status_code": 429, "message": f"Weekly API quota exhausted. Requests will fail until the quota resets.{reset_info}"}

                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)  # exponential backoff
                        # Honor a short Retry-After header if the server provided one
                        retry_after = response.headers.get("Retry-After")
                        if retry_after is not None:
                            try:
                                retry_after_secs = int(retry_after)
                                if retry_after_secs <= 60:
                                    wait_time = retry_after_secs
                            except ValueError:
                                pass
                        logger.warning(f"Rate limited (429). Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"Rate limited (429) after {max_retries} attempts. Please wait before making more requests.")
                        return {"error": "rate_limited", "status_code": 429, "message": "Too many requests. Please wait before retrying."}

                # Subscriber views (e.g. view=COMPLETE) are entitled by network/IP,
                # so an authorization error usually means the wrong network, not a
                # bad key. Surface a hint instead of a bare 401/403.
                if response.status_code in (401, 403):
                    detail = ""
                    try:
                        detail = response.json().get("service-error", {}).get("status", {}).get("statusText", "")
                    except Exception:
                        pass
                    message = f"Not authorized ({response.status_code}): {detail or 'authorization error'}."
                    if params.get("view"):
                        message += (
                            f" The '{params['view']}' view requires subscriber entitlement,"
                            " which Elsevier grants by network/IP. If you have institutional"
                            " access, connect to your institution's network or VPN and retry."
                        )
                    logger.error(f"Scopus API authorization error: {message}")
                    return {"error": "unauthorized", "status_code": response.status_code, "message": message}

                response.raise_for_status()
                return response

            except requests.exceptions.HTTPError as e:
                # 429 is intercepted before raise_for_status(), so this branch
                # only handles other HTTP errors.
                status_code = e.response.status_code if e.response is not None else None
                logger.error(f"HTTP Error requesting Scopus API: {e}")
                return {"error": "http_error", "status_code": status_code, "message": str(e)}
            except Exception as e:
                logger.error(f"Error requesting Scopus API: {e}")
                return {"error": "general_error", "message": str(e)}

        # Safety net: every loop path either returns or continues, so this is
        # only reachable if the loop logic changes.
        return {"error": "max_retries_exceeded", "message": "Maximum retry attempts exceeded"}

    def _get_paper_details_by_id(self, paper_id: str) -> Optional[dict]:
        """
        Get paper details from Scopus by paper ID
        
        Args:
            paper_id: Scopus paper ID
            
        Returns:
            dict: Paper details or None if not found
        """
        # Validate paper_id before interpolating it into the URL path
        paper_id = paper_id.strip()
        if paper_id.startswith("SCOPUS_ID:"):
            paper_id = paper_id[len("SCOPUS_ID:"):]
        if not paper_id.isdigit():
            logger.error(f"Invalid Scopus paper ID: {paper_id!r}. Expected a numeric Scopus ID.")
            return None

        try:
            # Use Scopus Abstract Retrieval API to get paper details
            abstract_url = f"https://api.elsevier.com/content/abstract/scopus_id/{paper_id}"

            params = {
                "httpAccept": "application/json",
                "view": "FULL"
            }

            response = self.session.get(abstract_url, params=params, timeout=(10, 30))
            response.raise_for_status()
            
            data = response.json()
            
            if 'abstracts-retrieval-response' in data:
                abstract_data = data['abstracts-retrieval-response']
                
                # Extract title
                title = ""
                if 'coredata' in abstract_data and 'dc:title' in abstract_data['coredata']:
                    title = abstract_data['coredata']['dc:title']
                
                # Extract DOI
                doi = ""
                if 'coredata' in abstract_data and 'prism:doi' in abstract_data['coredata']:
                    doi = abstract_data['coredata']['prism:doi']
                
                # Extract authors
                authors = []
                if 'authors' in abstract_data and 'author' in abstract_data['authors']:
                    for author in abstract_data['authors']['author']:
                        if 'ce:indexed-name' in author:
                            authors.append(author['ce:indexed-name'])
                
                # Extract publication date
                published_date = ""
                if 'coredata' in abstract_data and 'prism:coverDate' in abstract_data['coredata']:
                    published_date = abstract_data['coredata']['prism:coverDate']
                
                return {
                    'title': title,
                    'doi': doi,
                    'authors': authors,
                    'published_date': published_date
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting paper details for {paper_id}: {e}")
            return None

    def _search_sciencedirect_by_doi(self, doi: str) -> Optional[dict]:
        """
        Search ScienceDirect using DOI with PUT method
        
        Args:
            doi: Paper DOI
            
        Returns:
            dict: ScienceDirect search result or None
        """
        try:
            sciencedirect_url = "https://api.elsevier.com/content/search/sciencedirect"
            
            # Prepare JSON request body for PUT method
            request_body = {
                "qs": f"DOI({doi})",
                "display": {
                    "show": 10,
                    "sortBy": "relevance"
                }
            }
            
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-ELS-APIKey": self.api_key,
            }
            
            response = self.session.put(sciencedirect_url, json=request_body, headers=headers, timeout=(10, 30))
            response.raise_for_status()

            data = response.json()

            if 'resultsFound' in data and data['resultsFound'] > 0 and 'results' in data:
                return data['results'][0]  # Return first result

            return None

        except Exception as e:
            logger.error(f"Error searching ScienceDirect by DOI {doi}: {e}")
            return None

    def _search_sciencedirect_by_title(self, title: str) -> Optional[dict]:
        """
        Search ScienceDirect using title with PUT method
        
        Args:
            title: Paper title
            
        Returns:
            dict: ScienceDirect search result or None
        """
        try:
            sciencedirect_url = "https://api.elsevier.com/content/search/sciencedirect"
            
            # Prepare JSON request body for PUT method
            request_body = {
                "title": title,
                "display": {
                    "show": 10,
                    "sortBy": "relevance"
                }
            }
            
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-ELS-APIKey": self.api_key,
            }
            
            response = self.session.put(sciencedirect_url, json=request_body, headers=headers, timeout=(10, 30))
            response.raise_for_status()

            data = response.json()

            if 'resultsFound' in data and data['resultsFound'] > 0 and 'results' in data:
                return data['results'][0]  # Return first result

            return None

        except Exception as e:
            logger.error(f"Error searching ScienceDirect by title '{title}': {e}")
            return None

    def _extract_full_text_from_sciencedirect(self, result: dict) -> Optional[str]:
        """
        Extract full-text content from ScienceDirect result
        
        Args:
            result: ScienceDirect search result
            
        Returns:
            str: Full-text content or None
        """
        try:
            # Get the PII (Publisher Item Identifier) from the result
            pii = result.get('pii', '')
            if not pii:
                logger.error("No PII found in ScienceDirect result")
                return None
            
            # Use Article Retrieval API to get full text
            article_url = f"https://api.elsevier.com/content/article/pii/{urllib.parse.quote(pii, safe='')}"
            
            params = {
                "httpAccept": "text/plain",  # Request plain text format
                "view": "FULL"
            }
            
            headers = {
                "X-ELS-APIKey": self.api_key,
            }
            
            response = self.session.get(article_url, params=params, headers=headers, timeout=(10, 30))
            response.raise_for_status()

            # Check if we got text content
            if response.headers.get('content-type', '').startswith('text/plain'):
                return response.text
            else:
                # If we didn't get plain text, try JSON format and extract what we can
                params['httpAccept'] = 'application/json'
                response = self.session.get(article_url, params=params, headers=headers, timeout=(10, 30))
                response.raise_for_status()
                
                data = response.json()
                
                # Extract available text content from JSON
                text_content = []
                
                if 'full-text-retrieval-response' in data:
                    full_text_data = data['full-text-retrieval-response']
                    
                    # Extract abstract
                    if 'coredata' in full_text_data and 'dc:description' in full_text_data['coredata']:
                        text_content.append("ABSTRACT:\n" + full_text_data['coredata']['dc:description'])
                    
                    # Extract article body if available
                    if 'originalText' in full_text_data:
                        text_content.append("FULL TEXT:\n" + full_text_data['originalText'])
                    elif 'objects' in full_text_data:
                        # Try to extract text from objects section
                        for obj in full_text_data['objects']:
                            if isinstance(obj, dict) and 'text' in obj:
                                text_content.append(obj['text'])
                
                if text_content:
                    return "\n\n".join(text_content)
                else:
                    return "Full-text content not available in accessible format"
            
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            if status_code == 403:
                return "Access to full-text content requires subscription or institutional access"
            elif status_code == 404:
                return "Full-text article not found on ScienceDirect"
            else:
                logger.error(f"HTTP error extracting full text: {e}")
                return f"Error accessing full text: HTTP {status_code if status_code is not None else 'unknown'}"
        except Exception as e:
            logger.error(f"Error extracting full text: {e}")
            return f"Error extracting full text: {e}"

    def search(self, query: str, max_results: int = 10, sort: str = "relevance", 
               field: Optional[str] = None, date: Optional[str] = None) -> List[Paper]:
        """
        Search Scopus database

        Args:
            query: Search query string. Supports advanced search syntax:
                   - Field searches: TITLE("machine learning") or ABS("neural networks") 
                   - Boolean operators: AND, OR, NOT
                   - Proximity operators: W/n (within n words), PRE/n (preceding within n words)
                   - Wildcards: * (multiple chars), ? (single char)
                   - Exact phrases: "machine learning"
                   - Grouping with parentheses: (TITLE(AI) OR TITLE(ML)) AND ABS(deep)
            max_results: Maximum number of results to return (default: 10)
            sort: Sort results by: "relevance" (default), "coverDate", "citedby-count", "creator"
            field: Limit search to specific field: "TITLE", "ABS", "KEY", "AUTH", "AFFILORG", or None for all fields
            date: Date range filter (e.g., "2020-2023", "2020", "2020-") for publication date

        Returns:
            List[Paper]: List of paper objects
        """
        papers = []

        try:
            # Construct search query with field restriction if specified
            search_query = query
            if field:
                # If field is specified, wrap the query in field syntax
                search_query = f"{field}({query})"
            
            # Construct search parameters
            if max_results > 25:
                logger.warning("Scopus COMPLETE view caps results at 25 per request; results will be capped at 25")
            params = {
                "query": search_query,
                "count": min(max_results, 25),  # COMPLETE view allows at most 25 results per page
                "view": "COMPLETE",  # COMPLETE view returns richer fields than STANDARD
                "httpAccept": "application/json",
                "sort": sort,  # Add sort parameter
            }
            
            # Add date range filter if provided
            if date:
                params["date"] = date

            # Make request
            response = self.request_api(params)
            
            # Check for errors
            if isinstance(response, dict) and "error" in response:
                error_msg = response.get("message", "Unknown error")
                if response.get("error") == "rate_limited":
                    logger.error(f"Rate limited by Scopus API: {error_msg}")
                else:
                    logger.error(f"Scopus API error: {error_msg}")
                return papers

            data = response.json()
            
            # Check if there are search results
            if 'search-results' not in data:
                logger.info("No search-results found in response")
                return papers
                
            search_results = data['search-results']
            
            # Check if there are entries
            if 'entry' not in search_results or not search_results['entry']:
                logger.info("No results found for the query")
                return papers

            entries = search_results['entry']
            
            # Sometimes the API returns a single entry as dict instead of list
            if isinstance(entries, dict):
                entries = [entries]

            # An empty result set is reported as a sentinel entry like
            # {"@_fa": "true", "error": "Result set was empty"} rather than an
            # absent 'entry' key — drop such entries instead of parsing them
            # into empty Paper objects.
            entries = [e for e in entries if not (isinstance(e, dict) and "error" in e)]
            if not entries:
                logger.info("No results found for the query")
                return papers

            # Process each result
            for i, item in enumerate(entries):
                if len(papers) >= max_results:
                    break

                logger.info(f"Processing paper {i+1}/{min(len(entries), max_results)}")
                paper = self._parse_paper(item)
                if paper:
                    papers.append(paper)

        except Exception as e:
            logger.error(f"Scopus search error: {e}")

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Scopus API does not provide direct PDF download links.
        This method raises NotImplementedError to indicate that PDF download
        is not supported through the Scopus API.
        """
        raise NotImplementedError("Direct PDF download from Scopus is not supported via this API. Please use the paper's DOI or URL to access the publisher's website.")

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Read paper content using ScienceDirect Search API v2 to find full-text articles.
        
        Args:
            paper_id: Scopus paper ID
            save_path: Directory to save downloaded content (optional)
            
        Returns:
            str: The extracted text content of the paper or error message
        """
        try:
            # First, get paper details from Scopus to get DOI and title
            paper_details = self._get_paper_details_by_id(paper_id)
            if not paper_details:
                return f"Error: Could not find paper details for Scopus ID {paper_id}"
            
            # Try to find the paper on ScienceDirect using DOI first, then title
            sciencedirect_result = None
            
            if paper_details.get('doi'):
                sciencedirect_result = self._search_sciencedirect_by_doi(paper_details['doi'])
            
            if not sciencedirect_result and paper_details.get('title'):
                sciencedirect_result = self._search_sciencedirect_by_title(paper_details['title'])
            
            if not sciencedirect_result:
                return f"Error: Paper not found on ScienceDirect. Paper may not be available in full-text format."
            
            # Extract full-text content
            full_text = self._extract_full_text_from_sciencedirect(sciencedirect_result)
            
            if full_text:
                # Add paper metadata at the beginning
                metadata = f"Title: {paper_details.get('title', 'N/A')}\n"
                metadata += f"Authors: {', '.join(paper_details.get('authors', []))}\n"
                metadata += f"DOI: {paper_details.get('doi', 'N/A')}\n"
                metadata += f"Published Date: {paper_details.get('published_date', 'N/A')}\n"
                metadata += f"Source: Scopus + ScienceDirect\n"
                metadata += "=" * 80 + "\n\n"
                
                return metadata + full_text
            else:
                return f"Error: Could not extract full-text content from ScienceDirect"
                
        except Exception as e:
            logger.error(f"Error reading paper {paper_id}: {e}")
            return f"Error reading paper: {e}"


if __name__ == "__main__":
    # Test Scopus searcher
    try:
        searcher = ScopusSearcher()
        print("✓ Successfully initialized ScopusSearcher")
    except Exception as e:
        print(f"✗ Failed to initialize ScopusSearcher: {e}")
        print("Make sure you have set the SCOPUS_API_KEY environment variable")
        exit(1)

    print("\n" + "=" * 60)
    print("Testing Scopus Search functionality")
    print("=" * 60)

    # Test search functionality
    print("\n1. Testing search functionality...")
    query = "machine learning neural networks"
    max_results = 3

    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"✓ Search completed successfully!")
        print(f"✓ Found {len(papers)} papers for query: '{query}'")

        if papers:
            print("\nPaper Results:")
            print("-" * 40)
            for i, paper in enumerate(papers, 1):
                print(f"\n{i}. Title: {paper.title}")
                print(f"   Paper ID: {paper.paper_id}")
                print(f"   Authors: {', '.join(paper.authors) if paper.authors else 'N/A'}")
                print(f"   Source: {paper.source}")
                print(f"   URL: {paper.url}")
                print(f"   DOI: {paper.doi if paper.doi else 'N/A'}")
                print(f"   Published: {paper.published_date if paper.published_date else 'N/A'}")
                print(f"   Categories: {', '.join(paper.categories) if paper.categories else 'N/A'}")
                print(f"   Citations: {paper.citations}")
                if paper.abstract:
                    print(f"   Abstract: {paper.abstract[:200]}...")
        else:
            print("✗ No papers found")

    except Exception as e:
        print(f"✗ Search failed: {e}")

    # Test year range search
    print(f"\n2. Testing year range search...")
    try:
        papers_2020 = searcher.search("artificial intelligence", max_results=2, date="2020-2023")
        print(f"✓ Year range search completed! Found {len(papers_2020)} papers from 2020-2023")
        
        if papers_2020:
            for i, paper in enumerate(papers_2020, 1):
                print(f"   {i}. {paper.title} ({paper.published_date})")
                
    except Exception as e:
        print(f"✗ Year range search failed: {e}")

    # Test field-specific search
    print(f"\n3. Testing field-specific search...")
    try:
        title_papers = searcher.search("deep learning", max_results=2, field="TITLE")
        print(f"✓ Title search completed! Found {len(title_papers)} papers with 'deep learning' in title")
        
        if title_papers:
            for i, paper in enumerate(title_papers, 1):
                print(f"   {i}. {paper.title}")
                
    except Exception as e:
        print(f"✗ Field search failed: {e}")

    # Test different sorting
    print(f"\n4. Testing citation-based sorting...")
    try:
        cited_papers = searcher.search("machine learning", max_results=2, sort="citedby-count")
        print(f"✓ Citation sort completed! Found {len(cited_papers)} papers sorted by citations")
        
        if cited_papers:
            for i, paper in enumerate(cited_papers, 1):
                print(f"   {i}. {paper.title} (Citations: {paper.citations})")
                
    except Exception as e:
        print(f"✗ Citation sort failed: {e}")

    # Test download_pdf (expected to raise NotImplementedError)
    if papers:
        print("\n5. Testing PDF download functionality (expecting NotImplementedError)...")
        paper_id = papers[0].paper_id
        try:
            searcher.download_pdf(paper_id, "./downloads")
        except NotImplementedError as e:
            print(f"✓ Caught expected error: {e}")
        except Exception as e:
            print(f"✗ Caught unexpected error: {e}")

    # Test read_paper (now should actually try to get full text)
    if papers:
        print("\n6. Testing paper reading functionality with ScienceDirect integration...")
        paper_id = papers[0].paper_id
        try:
            content = searcher.read_paper(paper_id)
            if content.startswith("Error:"):
                print(f"⚠ Expected result: {content}")
            else:
                print(f"✓ Successfully retrieved content! Length: {len(content)} characters")
                print(f"   Preview: {content[:300]}...")
        except Exception as e:
            print(f"✗ Caught unexpected error: {e}")

    print("\n" + "=" * 60)
    print("Test completed!")
    print("=" * 60)
