from typing import List, Optional
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import time
import random
import re # Added for regex in date parsing
from ..paper import Paper # Assuming Paper class is in parent directory
import logging

logger = logging.getLogger(__name__)

# Define a base class for paper sources, similar to what's seen in other modules.
# If a central PaperSource exists, this searcher should inherit from it.
class PaperSource:
    """Abstract base class for paper sources"""
    def search(self, query: str, **kwargs) -> List[Paper]:
        raise NotImplementedError

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

    def read_paper(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

class ShodhgangaSearcher(PaperSource):
    """
    Searcher for theses and dissertations on Shodhganga (https://shodhganga.inflibnet.ac.in/).

    NOTE: This implementation is based on assumed HTML structures and search parameter patterns
    due to limitations in directly accessing the website during development.
    It will require validation and potential adjustments with actual website responses.
    """

    BASE_URL = "https://shodhganga.inflibnet.ac.in"
    SEARCH_PATH = "/simple-search" # Assuming this is the correct path for simple search

    # Common browser user agents to rotate
    BROWSERS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.84 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.84 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.84 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:98.0) Gecko/20100101 Firefox/98.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:98.0) Gecko/20100101 Firefox/98.0"
    ]

    def __init__(self):
        """Initialize the session with a random user agent."""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': random.choice(self.BROWSERS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5'
        })

    def _parse_single_item(self, item_soup: BeautifulSoup, base_url: str) -> Optional[Paper]:
        """
        Parses a single search result item from Shodhganga.
        ASSUMPTION: This method is based on a hypothetical HTML structure.
        Actual class names and tags will need to be verified.
        """
        try:
            # --- ASSUMED HTML STRUCTURE ---
            title_tag = item_soup.select_one('h4.discovery-result-title a') # Example: <h4 class="discovery-result-title"><a href="...">Title</a></h4>
            if not title_tag:
                logger.warning("Could not find title tag in item.")
                return None

            title = title_tag.get_text(strip=True)
            item_url = title_tag.get('href')
            if not item_url:
                logger.warning(f"Could not find URL for title: {title}")
                return None

            # Ensure URL is absolute
            if item_url.startswith('/'):
                item_url = base_url + item_url

            # Authors - Example: <div class="author">Author One, Author Two</div>
            # Or authors might be in <span class="author"> or <p class="author-style">
            # We will look for a div with "author" in its class name or specific metadata fields
            author_tags = item_soup.select('div.authors span[title="author"], meta[name="DC.creator"]')
            authors = []
            if author_tags:
                authors = [tag.get_text(strip=True) if tag.name == 'span' else tag.get('content', '') for tag in author_tags]
                authors = [a for a in authors if a] # Filter out empty strings

            if not authors: # Fallback if specific tags not found
                 author_div = item_soup.select_one('div[class*="author"]') # Generic author div
                 if author_div:
                     authors = [a.strip() for a in author_div.get_text(strip=True).split(';')]


            # Abstract/Description - Example: <div class="abstract">This is the abstract...</div>
            # Or <div class="item-description">
            abstract_tag = item_soup.select_one('div.abstract-full, div.item-abstract')
            abstract = abstract_tag.get_text(strip=True) if abstract_tag else "No abstract available."

            # Publication Date (Year) - Example: <div class="date">2023</div> or <span class="date">
            # Or <meta name="DC.date.issued" content="YYYY-MM-DD">
            date_tag = item_soup.select_one('div.dateinfo, span.date, meta[name="DC.date.issued"]')
            year = None
            if date_tag:
                date_text = date_tag.get_text(strip=True) if date_tag.name != 'meta' else date_tag.get('content', '')
                # Try to extract a 4-digit year
                match = re.search(r'\b(\d{4})\b', date_text)
                if match:
                    year = int(match.group(1))

            published_date = datetime(year, 1, 1) if year else None

            # Paper ID - can be derived from the URL or a specific metadata field
            paper_id = f"shodhganga_{item_url.split('/')[-1]}" if item_url else f"shodhganga_{hash(title)}"

            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors if authors else ["Unknown Author"],
                abstract=abstract,
                url=item_url,
                pdf_url="", # Shodhganga links to landing pages, not direct PDFs
                published_date=published_date,
                updated_date=None, # Shodhganga may not provide this
                source='shodhganga',
                categories=[], # May need to parse if available
                keywords=[],   # May need to parse if available
                doi=""         # Shodhganga items are theses, may not always have DOIs
            )
        except Exception as e:
            logger.error(f"Error parsing Shodhganga item: {e}", exc_info=True)
            return None

    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        """
        Search Shodhganga for theses and dissertations.

        ASSUMPTION: This method relies on assumed URL parameters and HTML structure
        for Shodhganga's search results. These need to be verified.
        """
        papers: List[Paper] = []
        # ASSUMPTION: Search parameters. Common ones are 'query' or 'rpp' (results per page).
        # Shodhganga's simple search form uses 'query', 'filter_field_1', 'filter_type_1', 'filter_value_1'
        # For a simple keyword search, 'query' might be enough, or it might be 'filter_value_1' with 'filter_field_1=all'

        # Let's try a structure based on typical DSpace simple search
        # Example: /simple-search?query=myquery&sort_by=score&order=desc&rpp=10&etal=0&start=0
        search_url = self.BASE_URL + self.SEARCH_PATH

        # Pagination: DSpace typically uses 'start' for the offset.
        # 'rpp' for results per page.
        results_to_fetch_this_page = min(max_results, 20) # Shodhganga might cap results per page (e.g. at 20)
        current_start_index = 0

        while len(papers) < max_results:
            params = {
                'query': query,
                'rpp': results_to_fetch_this_page,
                'sort_by': 'score', # Or 'dc.date.issued' for newest
                'order': 'desc',
                'start': current_start_index
            }

            logger.info(f"Searching Shodhganga: {search_url} with params: {params}")

            try:
                # Add a small delay to be polite to the server
                time.sleep(random.uniform(1.0, 3.0))
                response = self.session.get(search_url, params=params)
                response.raise_for_status() # Raise an exception for HTTP errors

                soup = BeautifulSoup(response.content, 'html.parser')

                # --- ASSUMED HTML STRUCTURE for results list ---
                # Example: <div class="discovery-result-results"> <div class="ds-artifact-item">...</div> <div class="ds-artifact-item">...</div> </div>
                # Or <ul id="results"> <li class="item">...</li> </ul>
                # Looking for elements that seem to contain individual search results.
                # Common DSpace class for a list of items: 'ds-artifact-list' or 'discovery-result-results'
                # Common DSpace class for one item: 'ds-artifact-item' or 'artifact-description'
                result_items = soup.select('div.ds-artifact-item, div.artifact-description, li.ds-artifact-item')

                if not result_items:
                    logger.info("No more results found on Shodhganga or page structure not recognized.")
                    break

                found_on_page = 0
                for item_soup in result_items:
                    if len(papers) >= max_results:
                        break
                    paper = self._parse_single_item(item_soup, self.BASE_URL)
                    if paper:
                        papers.append(paper)
                        found_on_page +=1

                logger.info(f"Found {found_on_page} items on this page. Total papers collected: {len(papers)}.")

                if found_on_page == 0: # No items parsed on this page, stop.
                    logger.info("No parsable items found on this page, stopping pagination.")
                    break

                # Pagination: Look for a 'next' link
                # ASSUMPTION: Next page link has class 'next-page' or text 'Next'.
                # DSpace pagination usually updates the 'start' parameter.
                # If we successfully got `results_to_fetch_this_page` items, we assume there might be more.
                # A more robust way is to check for an explicit "next" link.
                # For DSpace, if current_start_index + results_to_fetch_this_page < total_hits, there's a next page.
                # Total hits might be displayed as: <span class="discovery-result-count">1-10 of 123</span>

                # Simple pagination: increment start index
                current_start_index += results_to_fetch_this_page

                # Check if there's a clear 'next' button to decide if we should continue
                next_page_tag = soup.select_one('a.next-page, a:contains("Next"), a[title="next"]')
                if not next_page_tag and found_on_page < results_to_fetch_this_page :
                    logger.info("No 'next page' link found or fewer results than requested, assuming end of results.")
                    break


            except requests.exceptions.RequestException as e:
                logger.error(f"HTTP request to Shodhganga failed: {e}")
                break # Stop searching if there's a request error
            except Exception as e:
                logger.error(f"An error occurred during Shodhganga search: {e}", exc_info=True)
                break # Stop on other errors

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Shodhganga typically links to thesis pages which may contain PDFs.
        Direct PDF download via a simple ID is not assumed to be supported.
        """
        raise NotImplementedError(
            "Shodhganga does not provide direct PDF downloads via this interface. "
            "Please use the paper URL from the search results to navigate to the thesis page and find download options."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Reading papers directly from Shodhganga is not supported.
        Metadata and links are provided; full text access is via the website.
        """
        return (
            "Shodhganga papers cannot be read directly through this tool. "
            "Please use the paper's URL to access the full text on the Shodhganga website."
        )

if __name__ == '__main__':
    # This section can be used for basic testing once the search method is implemented.
    # For now, it will just demonstrate class instantiation.
    searcher = ShodhgangaSearcher()
    print("ShodhgangaSearcher initialized.")

    # Example of how search might be called (will currently return empty list and warning):
    # try:
    #     papers = searcher.search("artificial intelligence", max_results=5)
    #     if not papers:
    #         print("Search returned no results (as expected for now).")
    #     for paper in papers:
    #         print(paper.title)
    # except Exception as e:
    #     print(f"Error during search: {e}")

    # Test not implemented methods
    try:
        searcher.download_pdf("some_id", "./")
    except NotImplementedError as e:
        print(f"Caught expected error for download_pdf: {e}")

    try:
        message = searcher.read_paper("some_id")
        print(f"Response from read_paper: {message}")
    except Exception as e: # Should not happen if it returns a message
        print(f"Error during read_paper: {e}")
