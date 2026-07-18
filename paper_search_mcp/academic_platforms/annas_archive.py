import os
import httpx
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)

class AnnasArchiveFetcher:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }

    def download_pdf(self, doi: str, save_path: str) -> str:
        doi = doi.strip()
        if not doi:
            return ""
            
        try:
            # Step 1: Search the DOI
            search_url = f"https://annas-archive.org/search?q={doi}"
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(search_url, headers=self.headers)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # Step 2: Find the MD5 page link (first search result)
                link = soup.find('a', href=lambda x: x and '/md5/' in x)
                if not link:
                    logger.warning(f"Anna's Archive: No MD5 link found for {doi}")
                    return ""
                
                md5_url = f"https://annas-archive.org{link['href']}"
                md5_resp = client.get(md5_url, headers=self.headers)
                md5_resp.raise_for_status()
                # md5_soup = BeautifulSoup(md5_resp.text, 'html.parser')
                
                # Step 3: Placeholder for resolving slow/fast mirrors
                logger.warning(f"Anna's Archive: MD5 page found, but download automation requires explicit mirror handling for {doi}")
                return ""
                
        except Exception as e:
            logger.error(f"Anna's Archive download error for {doi}: {e}")
            return ""
