from typing import List, Optional
from datetime import datetime
import time
import logging
from playwright.sync_api import sync_playwright
from ..paper import Paper

logger = logging.getLogger(__name__)


class PaperSource:
    """Abstract base class for paper sources"""

    def search(self, query: str, **kwargs) -> List[Paper]:
        raise NotImplementedError

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

    def read_paper(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError


class JstorSearcher(PaperSource):
    """Searcher for JSTOR papers using Playwright to handle Vue.js components"""
    
    BASE_URL = "https://www.jstor.org/action/doBasicSearch"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/115.0.0 Safari/537.36"
    )

    def __init__(self, headless: bool = True):
        """Search for JSTOR papers thorugh Playwright"""
        self.headless = headless

    def _extract_all_search_results(self, page, max_results: int = 20) -> List[dict]:
        """Extract all search results efficiently using page locators"""
        search_results = []
        
        try:
            logger.info("Extracting search results efficiently...")
            
            # Wait for content to load
            page.wait_for_selector('search-results-vue-pharos-heading', timeout=15000)
            
            # Get all titles at once
            titles = page.locator('[data-qa^="search-result-title-heading"]')
            title_count = titles.count()
            logger.info(f"Found {title_count} titles")
            
            # Get all authors at once  
            authors = page.locator('search-results-vue-pharos-link[data-qa*="search-result-authors-link"]')
            author_count = authors.count()
            logger.info(f"Found {author_count} authors")
            
            # Get all journal metadata at once
            journals = page.locator('span.metadata')
            journal_count = journals.count()
            logger.info(f"Found {journal_count} journal metadata")
            
            # Get all JSTOR URLs at once
            jstor_links = page.locator('[data-qa*="read-online"][href*="/stable/"]')
            jstor_count = jstor_links.count()
            logger.info(f"Found {jstor_count} JSTOR links")
            
            # Process results (take the minimum count to avoid index errors)
            result_count = min(title_count, max_results)
            
            for i in range(result_count):
                paper_data = {
                    'index': i + 1,
                    'title': '',
                    'author': '',
                    'journal': '',
                    'jstor_url': ''
                }
                
                # Extract title
                try:
                    if i < title_count:
                        paper_data['title'] = titles.nth(i).inner_text().strip()
                except Exception as e:
                    logger.warning(f"Error getting title {i}: {e}")
                
                # Extract author
                try:
                    if i < author_count:
                        paper_data['author'] = authors.nth(i).inner_text().strip()
                except Exception as e:
                    logger.warning(f"Error getting author {i}: {e}")
                
                # Extract journal - get ALL text content regardless of internal tags
                try:
                    if i < journal_count:
                        journal_text = journals.nth(i).inner_text().strip()
                        paper_data['journal'] = journal_text
                except Exception as e:
                    logger.warning(f"Error getting journal {i}: {e}")
                
                # Extract JSTOR URL
                try:
                    if i < jstor_count:
                        href = jstor_links.nth(i).get_attribute('href')
                        if href:
                            if href.startswith('/'):
                                paper_data['jstor_url'] = f"https://www.jstor.org{href}"
                            else:
                                paper_data['jstor_url'] = href
                except Exception as e:
                    logger.warning(f"Error getting JSTOR URL {i}: {e}")
                
                # Only add if we have at least a title or URL
                if paper_data['title'] or paper_data['jstor_url']:
                    search_results.append(paper_data)
            
            logger.info(f"Successfully extracted {len(search_results)} complete papers!")
            return search_results
            
        except Exception as e:
            logger.error(f"Error in efficient extraction: {e}")
            return []

    def _convert_to_paper_objects(self, raw_results: List[dict], query: str) -> List[Paper]:
        """Convert raw search results to Paper objects"""
        papers = []
        
        for result in raw_results:
            # Parse authors - split by common separators
            author_str = result['author'] or ""
            author_list = [a.strip() for a in author_str.replace(" and ", ",").split(",") if a.strip()]

            # Create Paper object
            paper = Paper(
                paper_id=result['jstor_url'].split('/stable/')[-1] if result['jstor_url'] else "",
                title=result['title'] or "No title available",
                authors=author_list,
                abstract="",  # JSTOR search doesn't provide abstracts in results
                doi="",  # usually no DOI for some search results
                published_date=None,  # Could be extracted from journal metadata if needed
                pdf_url="",  # JSTOR doesn't provide direct PDF URLs
                url=result['jstor_url'],
                source="jstor",
                categories=[],
                keywords=[],  # Add search query as keyword
                citations=0,
                extra={
                    'journal': result['journal']
                }
            )
            papers.append(paper)

        return papers

    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        """
        Search JSTOR for papers using Playwright to handle Vue.js components
        
        Args:
            query: Search query string
            max_results: Maximum number of results to return (default: 10)
            
        Returns:
            List[Paper]: List of Paper objects found
        """
        papers = []
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page(user_agent=self.USER_AGENT)
            
            try:
                logger.info(f"Searching JSTOR for: {query}")
                page.goto(f"{self.BASE_URL}?Query={query}", timeout=60000)
                
                # Handle CAPTCHA if present
                try:
                    captcha_element = page.wait_for_selector("#px-captcha", timeout=10000)
                    if captcha_element:
                        if self.headless:
                            logger.warning("CAPTCHA detected but running in headless mode. Consider setting headless=False")
                            return []
                        else:
                            pass
                except:
                    logger.info("No CAPTCHA detected, proceeding...")
                
                # Wait for search results to load
                logger.info("Waiting for search results to load...")
                try:
                    page.wait_for_selector('.search-results-layout', timeout=60000)
                    time.sleep(5)  # Give Vue.js time to render
                    page.wait_for_selector('.search-results-layout__content', timeout=30000)
                except Exception as e:
                    logger.warning(f"Timeout waiting for search results: {e}")
                
                # Wait for network idle and Vue.js rendering
                try:
                    page.wait_for_load_state("networkidle", timeout=30000)
                    time.sleep(8)  # Additional time for Vue.js components
                except:
                    logger.warning("Network didn't reach idle state, continuing...")
                
                # Extract search results
                raw_results = self._extract_all_search_results(page, max_results)
                
                # Convert to Paper objects
                if raw_results:
                    papers = self._convert_to_paper_objects(raw_results, query)
                    logger.info(f"Successfully found {len(papers)} papers")
                else:
                    logger.warning("No search results found")
                
            except Exception as e:
                logger.error(f"Error during JSTOR search: {e}")
            finally:
                browser.close()
        
        return papers

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        JSTOR requires institutional access for PDF downloads
        
        Raises:
            NotImplementedError: JSTOR doesn't provide direct PDF downloads
        """
        raise NotImplementedError(
            "JSTOR requires institutional access for PDF downloads. "
            "Please use the paper URL to access the article through your institution."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        JSTOR requires institutional access for full-text reading
        
        Returns:
            str: Message indicating the feature requires institutional access
        """
        return (
            "JSTOR requires institutional access for full-text reading. "
            "Please use the paper URL to access the article through your institution."
        )


if __name__ == "__main__":
    # Test JSTOR searcher
    searcher = JstorSearcher(headless=False)  # Set to False for CAPTCHA handling
    
    print("Testing JSTOR search functionality...")
    query = "frantz fanon and feminism"
    max_results = 20
    
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"\nFound {len(papers)} papers for query '{query}':")
        
        for i, paper in enumerate(papers, 1):
            print(f"\n{i}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors) if paper.authors else 'No authors'}")
            print(f"   Journal: {paper.extra.get('journal', 'No journal info')}")
            print(f"   JSTOR URL: {paper.url}")
            print(f"   Paper ID: {paper.paper_id}")
            
    except Exception as e:
        print(f"Error during search: {e}")
        import traceback
        traceback.print_exc()
