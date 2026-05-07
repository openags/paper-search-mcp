"""Sci-Hub downloader integration.

Simple wrapper adapted from scihub.py for downloading PDFs via Sci-Hub.
"""
from pathlib import Path
import re
import hashlib
import logging
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..config import get_env
from ..utils import is_pdf_content


HARDCODED_MIRRORS = [
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
    "https://sci-hub.wf",
    "https://sci-hub.al",
    "https://sci-hub.mk",
    "https://sci-hub.ee",
    "https://sci-hub.shop",
]


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
        self.cache_file = Path(
            get_env("SCIHUB_MIRROR_CACHE_FILE", "/tmp/paper-search-mcp-scihub-mirrors.json")
        )
        self.cache_ttl = int(get_env("SCIHUB_MIRROR_CACHE_TTL", "21600"))
        self.probe_timeout = float(get_env("SCIHUB_MIRROR_PROBE_TIMEOUT", "4"))
        self.discovery_timeout = float(get_env("SCIHUB_MIRROR_DISCOVERY_TIMEOUT", "5"))
        self.probe_workers = int(get_env("SCIHUB_MIRROR_PROBE_WORKERS", "8"))

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
            pdf_url = ""
            mirror_used = self.base_url
            for mirror in self.get_candidate_mirrors():
                pdf_url = self._get_direct_url(identifier, base_url=mirror)
                if pdf_url:
                    mirror_used = mirror
                    break

            if not pdf_url:
                logging.error("Could not find PDF URL for identifier: %s", identifier)
                return None

            # Download the PDF
            response = self.session.get(pdf_url, verify=False, timeout=30)
            
            if response.status_code != 200:
                logging.error(f"Failed to download PDF, status {response.status_code}")
                return None

            if not is_pdf_content(
                response.content,
                content_type=response.headers.get("Content-Type", ""),
                url=pdf_url,
            ):
                logging.error("Response is not a PDF")
                return None

            # Generate filename and save
            filename = self._generate_filename(response, identifier)
            file_path = self.output_dir / filename
            
            with open(file_path, 'wb') as f:
                f.write(response.content)
                
            logging.info("Downloaded %s via Sci-Hub mirror %s", identifier, mirror_used)
            return str(file_path)

        except Exception as e:
            logging.error(f"Error downloading PDF for {identifier}: {e}")
            return None

    def _get_direct_url(self, identifier: str, base_url: Optional[str] = None) -> Optional[str]:
        """Get the direct PDF URL from Sci-Hub."""
        try:
            # If it's already a direct PDF URL, return it
            if identifier.endswith('.pdf'):
                return identifier

            # Search on Sci-Hub
            active_base_url = (base_url or self.base_url).rstrip("/")
            search_url = f"{active_base_url}/{identifier}"
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
                        pdf_url = active_base_url + src
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
                        return active_base_url + src
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
                            return active_base_url + url
                        else:
                            return url

            # Look for direct download links
            for link in soup.find_all('a'):
                href = link.get('href', '') if hasattr(link, 'get') else ''
                if isinstance(href, str) and href and ('pdf' in href.lower() or href.endswith('.pdf')):
                    if href.startswith('//'):
                        return 'https:' + href
                    elif href.startswith('/'):
                        return active_base_url + href
                    elif href.startswith('http'):
                        return href

            return None

        except Exception as e:
            logging.error(f"Error getting direct URL for {identifier}: {e}")
            return None

    def get_candidate_mirrors(self, force_refresh: bool = False) -> list[str]:
        """Return responsive mirrors, prioritizing configured and cached mirrors.

        Mirror discovery from sci-hub.now.sh is inspired by the MIT-licensed
        OpenByteDev/scihub-scraper-cli project.
        """
        configured = [
            mirror.strip().rstrip("/")
            for mirror in get_env("SCIHUB_MIRRORS", "").split(",")
            if mirror.strip()
        ]
        candidates = configured or [self.base_url]

        if not force_refresh:
            cached = self._load_cached_mirrors()
            if cached:
                return self._dedupe(candidates + cached + HARDCODED_MIRRORS)

        discovered = self._discover_mirrors()
        healthy = self._health_check(self._dedupe(candidates + discovered + HARDCODED_MIRRORS))
        if healthy:
            self._save_cached_mirrors(healthy)
            return healthy
        return self._dedupe(candidates + HARDCODED_MIRRORS)

    def _discover_mirrors(self) -> list[str]:
        """Fetch current Sci-Hub mirror candidates from public mirror lists."""
        mirrors: set[str] = set()
        try:
            response = self.session.get(
                "https://sci-hub.now.sh",
                timeout=self.discovery_timeout,
            )
            response.raise_for_status()
        except Exception:
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        for link in soup.find_all("a", href=True):
            href = str(link.get("href", "")).strip()
            if "sci-hub" in href or "scihub" in href:
                mirrors.add(self._normalize_mirror(href))

        for text_node in soup.find_all(string=True):
            for word in str(text_node).split():
                cleaned = word.strip(".,;()[]{}<>\"'")
                if cleaned.startswith(("sci-hub.", "scihub.")):
                    mirrors.add(self._normalize_mirror(cleaned))

        return sorted(mirrors)

    def _health_check(self, mirrors: list[str]) -> list[str]:
        """Return mirrors that respond, sorted by observed latency."""
        if not mirrors:
            return []

        results: list[tuple[float, str]] = []
        workers = max(1, min(self.probe_workers, len(mirrors)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_mirror = {executor.submit(self._probe_mirror, mirror): mirror for mirror in mirrors}
            for future in as_completed(future_to_mirror):
                mirror = future_to_mirror[future]
                try:
                    latency = future.result()
                except Exception:
                    latency = None
                if latency is not None:
                    results.append((latency, mirror))

        results.sort(key=lambda item: item[0])
        return [mirror for _, mirror in results]

    def _probe_mirror(self, mirror: str) -> Optional[float]:
        start = time.time()
        try:
            response = self.session.head(
                mirror,
                timeout=self.probe_timeout,
                allow_redirects=True,
                verify=False,
            )
            if response.status_code < 500:
                return time.time() - start
        except Exception:
            return None

        if response.status_code not in (403, 405):
            return None

        try:
            start = time.time()
            response = self.session.get(
                mirror,
                timeout=self.probe_timeout,
                allow_redirects=True,
                stream=True,
                verify=False,
            )
            if response.status_code < 500:
                return time.time() - start
        except Exception:
            return None

        return None

    def _load_cached_mirrors(self) -> list[str]:
        try:
            if not self.cache_file.exists():
                return []
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) <= self.cache_ttl:
                return [str(m).rstrip("/") for m in data.get("mirrors", []) if m]
        except Exception:
            return []
        return []

    def _save_cached_mirrors(self, mirrors: list[str]) -> None:
        try:
            self.cache_file.write_text(
                json.dumps({"timestamp": time.time(), "mirrors": mirrors}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def _normalize_mirror(url: str) -> str:
        normalized = url.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            normalized = "https://" + normalized
        return normalized

    @staticmethod
    def _dedupe(mirrors: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for mirror in mirrors:
            normalized = SciHubFetcher._normalize_mirror(mirror)
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique

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
