from typing import Dict, List, Optional
from datetime import datetime
import re
import time
import requests
import logging
from ..paper import Paper
from .base import PaperSource
from ..utils import extract_doi

logger = logging.getLogger(__name__)

# A bare OpenAlex work id, e.g. W2741809807
_WORK_ID_RE = re.compile(r"^W\d+$")
_OPENALEX_PREFIX = "https://openalex.org/"
# OpenAlex rejects an OR-filter with more than 100 values (verified: 85 -> 200,
# 101 -> 400 "Maximum number of values exceeded").
_MAX_OR_VALUES = 100
# Retry budget for 429/5xx: OpenAlex throttles bursts, and a graph walk bursts.
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_MAX_RETRY_DELAY = 30.0


def _strip_openalex_prefix(value: str) -> str:
    """'https://openalex.org/W123' -> 'W123'. Ids arrive as full URLs everywhere."""
    return (value or "").replace(_OPENALEX_PREFIX, "")


class OpenAlexSearcher(PaperSource):
    """OpenAlex paper search implementation"""

    BASE_URL = "https://api.openalex.org/works"

    def __init__(self):
        self.session = requests.Session()
        # OpenAlex encourages providing an email in User-Agent for the "polite pool"
        self.session.headers.update(
            {"User-Agent": "paper-search-mcp/1.0 (mailto:openags@example.com)"}
        )

    @staticmethod
    def _is_budget_exhausted(response) -> bool:
        """
        Distinguish an exhausted request budget from ordinary burst throttling.

        OpenAlex meters usage and answers an exhausted budget with 429 and a body like
        "Insufficient budget. This request costs $0.001 but you only have $0 remaining."
        That does not clear within a retry window, so backing off just burns time.
        """
        body = getattr(response, "text", "") or ""
        if not body:
            try:
                body = str(response.json())
            except Exception:
                return False
        return "budget" in body.lower()

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        """Honour Retry-After when the server sends it, else exponential backoff."""
        retry_after = (getattr(response, "headers", None) or {}).get("Retry-After")
        try:
            return min(float(retry_after), _MAX_RETRY_DELAY)
        except (TypeError, ValueError):
            return _RETRY_BASE_DELAY * (2 ** attempt)

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        """
        GET returning parsed JSON, or None on any transport/HTTP failure.

        429s and 5xx are retried with backoff: a burst of graph lookups can outrun the
        rate limit, and a silent empty result there looks exactly like "no such paper".
        """
        for attempt in range(_MAX_RETRIES):
            try:
                response = self.session.get(url, params=params, timeout=30)
            except Exception as e:
                logger.error(f"OpenAlex request failed for {url}: {e}")
                return None

            if response.status_code == 429 and self._is_budget_exhausted(response):
                logger.error(
                    f"OpenAlex request budget is exhausted, so {url} was not retried. "
                    "Wait for the budget to reset (midnight UTC)."
                )
                return None

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == _MAX_RETRIES - 1:
                    logger.error(
                        f"OpenAlex still returning {response.status_code} for {url} "
                        f"after {_MAX_RETRIES} attempts"
                    )
                    return None
                delay = self._retry_delay(response, attempt)
                logger.warning(
                    f"OpenAlex returned {response.status_code}; retrying {url} "
                    f"in {delay}s ({attempt + 1}/{_MAX_RETRIES})"
                )
                time.sleep(delay)
                continue

            if response.status_code != 200:
                logger.error(f"OpenAlex request to {url} returned {response.status_code}")
                return None

            try:
                return response.json()
            except ValueError as e:
                logger.error(f"OpenAlex returned non-JSON from {url}: {e}")
                return None

        return None

    def _reconstruct_abstract(self, inverted_index: Optional[dict]) -> str:
        """
        OpenAlex provides abstracts as an inverted index to save space.
        This function reconstructs the original abstract text.
        """
        if not inverted_index:
            return ""
        try:
            word_positions = []
            for word, positions in inverted_index.items():
                for pos in positions:
                    word_positions.append((pos, word))
            # Sort by position
            word_positions.sort(key=lambda x: x[0])
            return " ".join([word for _, word in word_positions])
        except Exception as e:
            logger.warning(f"Error reconstructing OpenAlex abstract: {e}")
            return ""

    def _parse_work(self, item: dict) -> Optional[Paper]:
        """Map one OpenAlex work object to a Paper. Returns None if it has no title."""
        # ID usually looks like 'https://openalex.org/W2741809807'
        paper_id = _strip_openalex_prefix(item.get("id", ""))
        title = item.get("title")
        if not title:
            return None  # Skip items without a title

        # Process Authors
        # Every `or <default>` below is load-bearing: OpenAlex returns JSON null for
        # absent fields, so .get(key, default) yields None rather than the default.
        authors = [
            (author.get("author") or {}).get("display_name", "")
            for author in (item.get("authorships") or [])
            if (author.get("author") or {}).get("display_name")
        ]

        # Abstract
        abstract = self._reconstruct_abstract(item.get("abstract_inverted_index"))

        # Process DOI
        doi = item.get("doi", "")
        if doi:
            # OpenAlex DOI is returned as a full url e.g. https://doi.org/10...
            doi = doi.replace("https://doi.org/", "")

        if not doi and abstract:
            doi = extract_doi(abstract)

        # Process URLs (Landing page vs direct PDF)
        url = ""
        pdf_url = ""

        primary_location = item.get("primary_location")
        if primary_location:
            url = primary_location.get("landing_page_url", "")
            pdf_url = primary_location.get("pdf_url", "")

        if not url:
            url = item.get("id", "")

        # best_oa_location points at whichever repository actually hosts an open copy,
        # which is often not the primary location.
        if not pdf_url:
            best_oa = item.get("best_oa_location") or {}
            pdf_url = best_oa.get("pdf_url") or ""

        # Everything above is a field OpenAlex asserts to be a PDF. oa_url below is only
        # the "best free link", which is frequently a landing page -- worth having, but
        # callers must not be told it is a file.
        pdf_is_direct = bool(pdf_url)

        # Check general open access availability for PDF fallback
        open_access = item.get("open_access") or {}
        if not pdf_url and open_access.get("is_oa"):
            pdf_url = open_access.get("oa_url", "")

        # Dates
        pub_date_str = item.get("publication_date")
        published_date = None
        if pub_date_str:
            try:
                published_date = datetime.strptime(pub_date_str, "%Y-%m-%d")
            except ValueError:
                pass

        # Categories / Concepts
        concepts = [
            concept.get("display_name")
            for concept in (item.get("concepts") or [])
            if concept.get("display_name")
        ]

        return Paper(
            paper_id=paper_id,
            title=title,
            authors=authors,
            abstract=abstract,
            url=url,
            pdf_url=pdf_url or "",
            published_date=published_date,
            source="openalex",
            categories=concepts[:5],  # Keep top 5 concepts to reduce size
            doi=doi,
            citations=item.get("cited_by_count", 0),
            references=[
                _strip_openalex_prefix(ref)
                for ref in (item.get("referenced_works") or [])
            ],
            # OpenAlex's own openness verdict, kept separate from pdf_url because a
            # paper can be open access while the only indexed link is a landing page.
            extra={
                "is_oa": bool(open_access.get("is_oa")),
                "pdf_is_direct": pdf_is_direct,
            },
        )

    def _parse_results(self, data: Optional[dict], max_results: int) -> List[Paper]:
        """Map a works-list response to Papers, dropping any that fail to parse."""
        papers = [self._parse_work(item) for item in ((data or {}).get("results") or [])]
        return [paper for paper in papers if paper][:max_results]

    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        """
        Search OpenAlex works. Uses the 'search' filter.

        Args:
            query: Search query string
            max_results: Maximum results to return (natively max 200 per page)

        Returns:
            List[Paper]: List of found papers with metadata.
        """
        data = self._get(self.BASE_URL, {"search": query, "per_page": min(max_results, 200)})
        return self._parse_results(data, max_results)

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        """Strip the URL/scheme wrappers OpenAlex ids and DOIs are usually quoted with."""
        value = (identifier or "").strip()
        for prefix in ("https://openalex.org/", "http://openalex.org/",
                       "https://doi.org/", "http://doi.org/", "doi:"):
            if value.lower().startswith(prefix):
                value = value[len(prefix):]
                break
        return value.strip()

    def _resolve_work_id(self, identifier: str) -> Optional[str]:
        """
        Resolve an OpenAlex id, a DOI, or a paper title to an OpenAlex work id.

        Logs and returns None when the identifier matches nothing, so callers only
        need an early return.
        """
        value = self._normalize_identifier(identifier)
        if not value:
            return None

        if _WORK_ID_RE.match(value):
            return value

        if value.startswith("10."):
            data = self._get(f"{self.BASE_URL}/doi:{value}")
            if data and data.get("id"):
                return _strip_openalex_prefix(data["id"])
            logger.warning(f"OpenAlex could not resolve DOI: {value!r}")
            return None

        # Anything else is a title. Match against the title field first -- a plain
        # `search` also matches abstracts, so it can land on a citing paper instead.
        for params in ({"filter": f"title.search:{value}"}, {"search": value}):
            data = self._get(self.BASE_URL, {**params, "per_page": 1})
            results = (data or {}).get("results") or []
            if results and results[0].get("id"):
                return _strip_openalex_prefix(results[0]["id"])

        logger.warning(f"OpenAlex could not resolve identifier: {identifier!r}")
        return None

    def get_citations(self, identifier: str, max_results: int = 10) -> List[Paper]:
        """
        Works that cite the given paper -- forward snowballing.

        Args:
            identifier: OpenAlex work id, DOI, or paper title.
            max_results: Maximum citing works to return.

        Returns:
            List[Paper]: Citing works, most-cited first. Empty if unresolvable.
        """
        return self._related_works(identifier, "cites", max_results)

    def get_references(self, identifier: str, max_results: int = 10) -> List[Paper]:
        """
        Works cited by the given paper -- backward snowballing.

        Coverage depends on the publisher having deposited its reference list; records
        without one (many arXiv preprints) return an empty list rather than an error.

        Args:
            identifier: OpenAlex work id, DOI, or paper title.
            max_results: Maximum referenced works to return.

        Returns:
            List[Paper]: Referenced works, most-cited first. Empty if none deposited.
        """
        return self._related_works(identifier, "cited_by", max_results)

    def _related_works(self, identifier: str, edge: str, max_results: int = 10) -> List[Paper]:
        """
        One hop along the citation graph.

        `cites:<id>` gives works citing the paper; `cited_by:<id>` gives works it cites.
        Both are ranked by OpenAlex across the whole edge set, so the caller gets a true
        most-cited-first slice rather than the top of an arbitrary batch.
        """
        work_id = self._resolve_work_id(identifier)
        if not work_id:
            return []

        data = self._get(self.BASE_URL, {
            "filter": f"{edge}:{work_id}",
            "per_page": min(max_results, 200),
            "sort": "cited_by_count:desc",
        })
        return self._parse_results(data, max_results)

    def resolve_oa_pdf(self, identifier: str) -> Optional[Paper]:
        """
        Locate an open-access copy of a paper from a DOI/title -- a key-less stand-in
        for an Unpaywall lookup, which otherwise needs a registered email address.

        Args:
            identifier: OpenAlex work id, DOI, or paper title.

        Returns:
            Paper with pdf_url set when an open copy exists, else the Paper with an
            empty pdf_url, or None if the paper could not be resolved at all.
        """
        work_id = self._resolve_work_id(identifier)
        if not work_id:
            return None

        work = self._get(f"{self.BASE_URL}/{work_id}")
        if not work:
            return None
        return self._parse_work(work)

    def citation_counts_by_doi(self, dois: List[str]) -> Dict[str, int]:
        """
        Look up citation counts for many DOIs in as few requests as possible.

        Lets results from sources that publish no citation count (arXiv, dblp, PubMed...)
        be enriched with one, without any API key.

        Args:
            dois: DOIs in any spelling; duplicates and non-DOIs are ignored.

        Returns:
            Dict mapping lowercased bare DOI -> citation count. DOIs OpenAlex does not
            know are simply absent.
        """
        wanted: List[str] = []
        for doi in dois:
            value = self._normalize_identifier(doi).lower()
            if value.startswith("10.") and value not in wanted:
                wanted.append(value)

        counts: Dict[str, int] = {}
        for start in range(0, len(wanted), _MAX_OR_VALUES):
            batch = wanted[start:start + _MAX_OR_VALUES]
            data = self._get(self.BASE_URL, {
                "filter": f"doi:{'|'.join(batch)}",
                "per_page": len(batch),
                "select": "doi,cited_by_count",
            })
            for item in ((data or {}).get("results") or []):
                item_doi = self._normalize_identifier(item.get("doi") or "").lower()
                if item_doi:
                    counts[item_doi] = item.get("cited_by_count") or 0

        return counts

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        OpenAlex does not host PDFs natively, it only links to open access versions.
        """
        raise NotImplementedError(
            "OpenAlex does not provide direct PDF downloads natively. "
            "Please use the extracted 'pdf_url' if available, or DOI for fallback."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Not implemented for OpenAlex.
        """
        return (
            "OpenAlex papers cannot be read directly through this aggregator. "
            "Please use the paper's DOI or pdf_url to access the full text."
        )


if __name__ == "__main__":
    searcher = OpenAlexSearcher()
    print("Testing OpenAlex search...")
    papers = searcher.search("CRISPR Cas9 Nature", max_results=3)

    for i, paper in enumerate(papers, 1):
        print(f"\n{i}. {paper.title}")
        print(f"   DOI: {paper.doi}")
        print(f"   URL: {paper.url}")
        print(f"   OA PDF: {paper.pdf_url}")
        print(f"   Citations: {paper.citations}")
        print(f"   Authors: {', '.join(paper.authors[:3])}")
        if paper.abstract:
            print(f"   Abstract: {paper.abstract[:100]}...")
