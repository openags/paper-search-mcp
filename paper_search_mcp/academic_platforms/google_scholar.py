from typing import List, Optional
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
import math
import requests
from bs4 import BeautifulSoup
import time
import random
import re
from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource
import logging

logger = logging.getLogger(__name__)


class GoogleScholarSearchError(RuntimeError):
    """An upstream failure must not be mistaken for a successful empty search."""


class GoogleScholarSearcher(PaperSource):
    """Custom implementation of Google Scholar paper search"""
    
    SCHOLAR_URL = "https://scholar.google.com/scholar"
    CONSENT_COOKIE_VALUE = "YES+"
    COOLDOWN_SECONDS = 60.0
    MAX_COOLDOWN_SECONDS = 900.0
    MAX_RETRY_AFTER_SECONDS = 86400.0
    FALLBACK_HINT = (
        "Use search_papers with sources=\"openalex,semantic,crossref\" "
        "for explicitly labeled alternative sources."
    )
    BROWSERS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    ]

    def __init__(self, max_retries: int = 3, retry_delay: float = 2.0, proxy_url: Optional[str] = None):
        self.max_retries = max(1, max_retries)
        self.retry_delay = max(0.5, retry_delay)
        self.proxy_url = (proxy_url or get_env("GOOGLE_SCHOLAR_PROXY_URL", "")).strip()
        # A shared requests.Session and its backoff state are not thread-safe.
        self._search_lock = Lock()
        self._cooldown_until = 0.0
        self._consecutive_blocks = 0
        self._setup_session()

    def _setup_session(self):
        """Initialize session with random user agent"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': random.choice(self.BROWSERS),
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'en-US,en;q=0.9'
        })
        self.session.cookies.set(
            "CONSENT",
            self.CONSENT_COOKIE_VALUE,
            domain=".google.com",
        )

        if self.proxy_url:
            self.session.proxies.update({
                'http': self.proxy_url,
                'https': self.proxy_url
            })

    @classmethod
    def _retry_after(cls, response) -> float:
        """Parse Retry-After delta-seconds or an HTTP date, never response text."""
        value = (getattr(response, "headers", None) or {}).get("Retry-After", "")
        if not isinstance(value, str) or not value.strip():
            return 0.0
        value = value.strip()
        try:
            if value.isascii() and value.isdigit():
                # Bound untrusted numeric conversion and scheduling state.
                digits = value.lstrip("0") or "0"
                delay = float(digits) if len(digits) <= 10 else math.inf
            else:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    return 0.0
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
            # An over-horizon instruction is a deferred state, not permission
            # to contact Scholar early when our bounded horizon expires.
            return math.inf if delay > cls.MAX_RETRY_AFTER_SECONDS else max(0.0, delay)
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _begin_cooldown(self, response=None) -> None:
        self._consecutive_blocks = min(self._consecutive_blocks + 1, 5)
        delay = min(
            self.COOLDOWN_SECONDS * (2 ** (self._consecutive_blocks - 1)),
            self.MAX_COOLDOWN_SECONDS,
        )
        if response is not None:
            delay = max(delay, self._retry_after(response))
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay)

    def _cooldown_hint(self) -> str:
        if math.isinf(self._cooldown_until):
            return (
                "The upstream Retry-After exceeds the supported 24-hour scheduling range; "
                "automatic requests are paused for this process. " + self.FALLBACK_HINT
            )
        remaining = max(0, math.ceil(self._cooldown_until - time.monotonic()))
        return f"Retry after {remaining} seconds. {self.FALLBACK_HINT}"

    @staticmethod
    def _remaining_timeout(deadline: Optional[float], maximum: float) -> Optional[float]:
        if deadline is None:
            return maximum
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        return min(maximum, remaining)

    @staticmethod
    def _sleep_with_deadline(delay: float, deadline: Optional[float]) -> bool:
        if deadline is None:
            time.sleep(delay)
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(delay, remaining))
        return time.monotonic() < deadline

    @staticmethod
    def _is_captcha_page(soup: BeautifulSoup, page_text: Optional[str] = None) -> bool:
        if page_text is None:
            page_text = soup.get_text(' ', strip=True).lower()
        return bool(
            soup.find('form', {'id': 'gs_captcha_f'})
            or soup.find('input', {'name': 'captcha'})
            or 'please show you\'re not a robot' in page_text
            or 'unusual traffic from your computer network' in page_text
        )

    @staticmethod
    def _is_consent_page(soup: BeautifulSoup, page_text: Optional[str] = None) -> bool:
        if page_text is None:
            page_text = soup.get_text(' ', strip=True).lower()
        return bool(
            soup.find("form", {"action": re.compile(r"consent\.google", re.IGNORECASE)})
            or "before you continue to google scholar" in page_text
        )

    def _extract_year(self, text: str) -> Optional[int]:
        """Extract year from publication info"""
        for word in text.split():
            if word.isdigit() and 1900 <= int(word) <= datetime.now().year:
                return int(word)
        return None

    def _parse_paper(self, item) -> Optional[Paper]:
        """Parse single paper entry from HTML"""
        try:
            # Extract main paper elements
            title_elem = item.find('h3', class_='gs_rt')
            info_elem = item.find('div', class_='gs_a')
            abstract_elem = item.find('div', class_='gs_rs')

            if not title_elem or not info_elem:
                return None

            # Process title and URL
            title = title_elem.get_text(strip=True).replace('[PDF]', '').replace('[HTML]', '')
            link = title_elem.find('a', href=True)
            url = link['href'] if link else ''

            # Process author info
            info_text = info_elem.get_text()
            authors = [a.strip() for a in info_text.split('-')[0].split(',')]
            year = self._extract_year(info_text)
            doi = (
                extract_doi(url)
                or extract_doi(title)
                or extract_doi(info_text)
                or extract_doi(abstract_elem.get_text() if abstract_elem else "")
            )

            # Create paper object
            return Paper(
                paper_id=f"gs_{hash(url)}",
                title=title,
                authors=authors,
                abstract=abstract_elem.get_text() if abstract_elem else "",
                url=url,
                pdf_url="",
                published_date=datetime(year, 1, 1) if year else None,
                updated_date=None,
                source="google_scholar",
                categories=[],
                keywords=[],
                doi=doi,
                citations=0
            )
        except Exception as e:
            logger.warning(f"Failed to parse paper: {e}")
            return None

    def search(
        self,
        query: str,
        max_results: int = 10,
        timeout_seconds: Optional[float] = None,
    ) -> List[Paper]:
        """
        Search Google Scholar with custom parameters

        Raises:
            GoogleScholarSearchError: HTTP/network failure, CAPTCHA, or a
                persistent consent interstitial prevents a successful search.
                Already fetched pages are not returned as a complete result.
        """
        if max_results <= 0 or (timeout_seconds is not None and timeout_seconds <= 0):
            return []
        deadline = (
            time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        )
        remaining = self._remaining_timeout(deadline, 30.0)
        if not self._search_lock.acquire(timeout=remaining if remaining is not None else 0):
            raise GoogleScholarSearchError(
                "Google Scholar is busy; search timed out waiting for its session. "
                + self.FALLBACK_HINT
            )
        try:
            if time.monotonic() < self._cooldown_until:
                raise GoogleScholarSearchError(
                    "Google Scholar is cooling down after an upstream access limit. "
                    + self._cooldown_hint()
                )
            return self._search_locked(query, max_results, deadline)
        finally:
            self._search_lock.release()

    def _search_locked(self, query: str, max_results: int, deadline: Optional[float]) -> List[Paper]:
        papers = []
        start = 0
        results_per_page = min(10, max_results)
        consent_retry_attempted = False

        while len(papers) < max_results:
            if deadline is not None and time.monotonic() >= deadline:
                raise GoogleScholarSearchError("Google Scholar search timed out. " + self.FALLBACK_HINT)
            try:
                # Construct search parameters
                params = {
                    'q': query,
                    'start': start,
                    'hl': 'en',
                    'as_sdt': '0,5'  # Include articles and citations
                }

                response = None
                for attempt in range(self.max_retries):
                    if not self._sleep_with_deadline(
                        random.uniform(1.0, 2.5), deadline
                    ):
                        break

                    request_timeout = self._remaining_timeout(deadline, 30.0)
                    if request_timeout is None:
                        break
                    response = self.session.get(
                        self.SCHOLAR_URL,
                        params=params,
                        timeout=request_timeout,
                    )
                    if response.status_code in (200, 403, 429, 503):
                        soup = BeautifulSoup(response.text, "html.parser")
                        if self._is_captcha_page(soup):
                            self._begin_cooldown(response)
                            raise GoogleScholarSearchError(
                                "Google Scholar returned a bot-detection/captcha page. "
                                + self._cooldown_hint()
                            )
                    if response.status_code == 200:
                        break

                    if response.status_code in (403, 429, 503):
                        retry_after = self._retry_after(response)
                        # Do not hold a synchronous caller asleep for a long
                        # upstream delay. Record it across calls and fail fast.
                        if retry_after > 30.0 or attempt == self.max_retries - 1:
                            break
                        wait_time = self.retry_delay * (2 ** attempt)
                        wait_time += random.uniform(0, 0.5)
                        wait_time = max(wait_time, retry_after)
                        logger.warning(
                            "Google Scholar returned %s (attempt %s/%s). Backing off %.1fs",
                            response.status_code,
                            attempt + 1,
                            self.max_retries,
                            wait_time,
                        )
                        if not self._sleep_with_deadline(wait_time, deadline):
                            break
                        continue

                    logger.error("Search failed with non-retryable status %s", response.status_code)
                    break

                # Check a known upstream failure before the deadline: running
                # out of time during backoff must not turn a 429 into success.
                if response is not None and response.status_code != 200:
                    hint = self.FALLBACK_HINT
                    if response.status_code in (403, 429, 503):
                        self._begin_cooldown(response)
                        hint = self._cooldown_hint()
                    raise GoogleScholarSearchError(
                        f"Google Scholar search failed: HTTP {response.status_code}. " + hint
                    )

                if deadline is not None and time.monotonic() >= deadline:
                    raise GoogleScholarSearchError("Google Scholar search timed out. " + self.FALLBACK_HINT)

                if response is None:
                    raise GoogleScholarSearchError("Google Scholar search timed out. " + self.FALLBACK_HINT)

                # The successful response was already parsed for access checks.
                page_text = soup.get_text(' ', strip=True).lower()

                if self._is_consent_page(soup, page_text):
                    if not consent_retry_attempted:
                        consent_retry_attempted = True
                        self.session.cookies.set(
                            "CONSENT",
                            self.CONSENT_COOKIE_VALUE,
                            domain=".google.com",
                        )
                        logger.info(
                            "Google Scholar consent page detected; retrying search"
                        )
                        continue
                    raise GoogleScholarSearchError(
                        "Google Scholar returned a consent page after retry; "
                        "use another source until Scholar is accessible."
                    )

                # Only an actual result/no-result page releases the block streak.
                self._consecutive_blocks = 0
                self._cooldown_until = 0.0
                results = soup.find_all('div', class_='gs_ri')

                if not results:
                    break

                # Process each result
                for item in results:
                    if len(papers) >= max_results:
                        break
                        
                    paper = self._parse_paper(item)
                    if paper:
                        papers.append(paper)

                start += results_per_page

            except GoogleScholarSearchError:
                raise
            except requests.RequestException as e:
                if response is not None and response.status_code in (403, 429, 503):
                    self._begin_cooldown(response)
                # Do not include request/proxy URLs or response bodies in the
                # error presented to MCP clients and CLI consumers.
                raise GoogleScholarSearchError(
                    "Google Scholar request failed; check connectivity or use another source."
                ) from e
            except Exception as e:
                logger.error(f"Search error: {e}")
                break

        return papers[:max_results]

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Google Scholar doesn't support direct PDF downloads
        
        Raises:
            NotImplementedError: Always raises this error
        """
        raise NotImplementedError(
            "Google Scholar doesn't provide direct PDF downloads. "
            "Please use the paper URL to access the publisher's website."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Google Scholar doesn't support direct paper reading
        
        Returns:
            str: Message indicating the feature is not supported
        """
        return (
            "Google Scholar doesn't support direct paper reading. "
            "Please use the paper URL to access the full text on the publisher's website."
        )

if __name__ == "__main__":
    # Test Google Scholar searcher
    searcher = GoogleScholarSearcher()
    
    print("Testing search functionality...")
    query = "machine learning"
    max_results = 5
    
    try:
        papers = searcher.search(query, max_results=max_results)
        print(f"\nFound {len(papers)} papers for query '{query}':")
        for i, paper in enumerate(papers, 1):
            print(f"\n{i}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors)}")
            print(f"   Citations: {paper.citations}")
            print(f"   URL: {paper.url}")
    except Exception as e:
        print(f"Error during search: {e}")
