"""Sci-Hub downloader integration.

Simple wrapper adapted from scihub.py for downloading PDFs via Sci-Hub.
"""
from pathlib import Path
import re
import logging
from typing import Optional, List

import requests
from bs4 import BeautifulSoup

from ..config import get_env
from ..crossref_resolver import metadata_for_identifier
from ..file_naming import paper_filename, paper_output_path
from .base import PaperSource
from ..paper import Paper

DEFAULT_MIRRORS: List[str] = [
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
    "https://sci-hub.ren",
]


def _get_proxy() -> Optional[dict]:
    proxy = get_env("HTTP_PROXY", "") or get_env("HTTPS_PROXY", "")
    return {"http": proxy, "https": proxy} if proxy else None


class SciHubFetcher:
    """Single-mirror Sci-Hub PDF downloader."""

    def __init__(self, base_url: str = "https://sci-hub.se", output_dir: str = "./downloads"):
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
        proxy = _get_proxy()
        if proxy:
            self.session.proxies.update(proxy)

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

            # Check by magic bytes first, then Content-Type (some servers add charset)
            content_type = response.headers.get('Content-Type', '')
            if 'application/pdf' not in content_type and not response.content[:4] == b'%PDF':
                logging.error(f"Response is not a PDF (Content-Type: {content_type})")
                return None

            # Generate filename and save
            file_path = self._generate_output_path(identifier)
            
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

    def _generate_output_path(self, identifier: str) -> Path:
        metadata = metadata_for_identifier(identifier)
        return paper_output_path(
            str(self.output_dir),
            title=metadata.get("title", ""),
            authors=metadata.get("authors", []),
            published_date=metadata.get("published_date", ""),
            identifier=identifier,
            extension=".pdf",
        )

    def _generate_filename(self, response: requests.Response, identifier: str) -> str:
        """Generate a FirstAuthor_Year_ShortTitle filename for compatibility tests."""
        metadata = metadata_for_identifier(identifier)
        return paper_filename(
            title=metadata.get("title", ""),
            authors=metadata.get("authors", []),
            published_date=metadata.get("published_date", ""),
            identifier=identifier,
            extension=".pdf",
        )


class SciHubSource(PaperSource):
    """PaperSource wrapper around SciHubFetcher with mirror fallback and proxy support."""

    def __init__(self):
        custom = get_env("SCIHUB_URL", "").strip()
        self._mirrors: List[str] = ([custom] if custom else []) + DEFAULT_MIRRORS

    def search(self, query: str, **kwargs):
        # Sci-Hub does not support search
        return []

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        last_error: Optional[Exception] = None
        for mirror in self._mirrors:
            try:
                fetcher = SciHubFetcher(base_url=mirror, output_dir=save_path)
                result = fetcher.download_pdf(paper_id)
                if result:
                    logging.info(f"[Sci-Hub] Downloaded via {mirror}: {result}")
                    return result
            except Exception as e:
                logging.warning(f"[Sci-Hub] Mirror {mirror} failed: {e}")
                last_error = e
        raise RuntimeError(
            f"Sci-Hub: all mirrors failed for '{paper_id}'. Last error: {last_error}"
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        pdf_path = self.download_pdf(paper_id, save_path)
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                return "\n\n".join(
                    page.extract_text() or "" for page in pdf.pages
                ).strip()
        except ImportError:
            pass
        try:
            import pypdf
            reader = pypdf.PdfReader(pdf_path)
            return "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            ).strip()
        except ImportError:
            pass
        return f"[PDF saved to {pdf_path}. Install pdfplumber or pypdf to extract text.]"
