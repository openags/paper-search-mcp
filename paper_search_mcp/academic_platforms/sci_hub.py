"""Sci-Hub downloader integration.

Simple wrapper adapted from scihub.py for downloading PDFs via Sci-Hub.
"""
from pathlib import Path
import re
import hashlib
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup


class SciHubFetcher:
    """Simple Sci-Hub PDF downloader."""

    def __init__(self, base_url: str = "https://sci-hub.se", output_dir: str = "./downloads"):
        """Initialize with Sci-Hub URL and output directory."""
        self.base_url = base_url.rstrip("/")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def download_pdf(self, identifier: str) -> Optional[str]:
        """Download a PDF from Sci-Hub using a DOI, PMID, or URL.

        Args:
            identifier: DOI, PMID, or URL to the paper

        Returns:
            Path to saved PDF or None on failure
        """
        if not identifier.strip():
            return None

        try:
            # Get direct URL to PDF
            pdf_url = self._get_direct_url(identifier)
            if not pdf_url:
                logging.error(f"Could not find PDF URL for identifier: {identifier}")
                return None

            # Download the PDF
            response = self.session.get(pdf_url, verify=False, timeout=30)
            
            if response.status_code != 200:
                logging.error(f"Failed to download PDF, status {response.status_code}")
                return None

            if response.headers.get('Content-Type') != 'application/pdf':
                logging.error("Response is not a PDF")
                return None

            # Generate filename and save
            filename = self._generate_filename(response, identifier)
            file_path = self.output_dir / filename
            
            with open(file_path, 'wb') as f:
                f.write(response.content)
                
            return str(file_path)

        except Exception as e:
            logging.error(f"Error downloading PDF for {identifier}: {e}")
            return None

    def _get_direct_url(self, identifier: str) -> Optional[str]:
        """Get the direct PDF URL from Sci-Hub."""
        try:
            # If it's already a direct PDF URL, return it
            if identifier.endswith('.pdf'):
                return identifier

            # Search on Sci-Hub
            search_url = f"{self.base_url}/{identifier}"
            response = self.session.get(search_url, verify=False, timeout=20)
            
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Check for article not found
            if "article not found" in response.text.lower():
                logging.warning("Article not found on Sci-Hub")
                return None

            # Look for embed tag with PDF (most common in modern Sci-Hub)
            embed = soup.find('embed', {'type': 'application/pdf'})
            logging.debug(f"Found embed tag: {embed}")
            if embed:
                src = embed.get('src') if hasattr(embed, 'get') else None
                logging.debug(f"Embed src: {src}")
                if src and isinstance(src, str):
                    if src.startswith('//'):
                        pdf_url = 'https:' + src
                        logging.debug(f"Returning PDF URL: {pdf_url}")
                        return pdf_url
                    elif src.startswith('/'):
                        pdf_url = self.base_url + src
                        logging.debug(f"Returning PDF URL: {pdf_url}")
                        return pdf_url
                    else:
                        logging.debug(f"Returning PDF URL: {src}")
                        return src

            # Look for iframe with PDF (fallback)
            iframe = soup.find('iframe')
            if iframe:
                src = iframe.get('src') if hasattr(iframe, 'get') else None
                if src and isinstance(src, str):
                    if src.startswith('//'):
                        return 'https:' + src
                    elif src.startswith('/'):
                        return self.base_url + src
                    else:
                        return src

            # Look for download button with onclick
            for button in soup.find_all('button'):
                onclick = button.get('onclick', '') if hasattr(button, 'get') else ''
                if isinstance(onclick, str) and 'pdf' in onclick.lower():
                    # Extract URL from onclick JavaScript
                    url_match = re.search(r"location\.href='([^']+)'", onclick)
                    if url_match:
                        url = url_match.group(1)
                        if url.startswith('//'):
                            return 'https:' + url
                        elif url.startswith('/'):
                            return self.base_url + url
                        else:
                            return url

            # Look for direct download links
            for link in soup.find_all('a'):
                href = link.get('href', '') if hasattr(link, 'get') else ''
                if isinstance(href, str) and href and ('pdf' in href.lower() or href.endswith('.pdf')):
                    if href.startswith('//'):
                        return 'https:' + href
                    elif href.startswith('/'):
                        return self.base_url + href
                    elif href.startswith('http'):
                        return href

            return None

        except Exception as e:
            logging.error(f"Error getting direct URL for {identifier}: {e}")
            return None

    def _generate_filename(self, response: requests.Response, identifier: str) -> str:
        """Generate a unique filename for the PDF."""
        # Try to get filename from URL
        url_parts = response.url.split('/')
        if url_parts:
            name = url_parts[-1]
            # Remove view parameters
            name = re.sub(r'#view=(.+)', '', name)
            if name.endswith('.pdf'):
                # Generate hash for uniqueness
                pdf_hash = hashlib.md5(response.content).hexdigest()[:8]
                base_name = name[:-4]  # Remove .pdf
                return f"{pdf_hash}_{base_name}.pdf"

        # Fallback: use identifier
        clean_identifier = re.sub(r'[^\w\-_.]', '_', identifier)
        pdf_hash = hashlib.md5(response.content).hexdigest()[:8]
        return f"{pdf_hash}_{clean_identifier}.pdf"