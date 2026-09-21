# paper_search_mcp/server.py
import argparse
import asyncio
import io
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
from collections.abc import Awaitable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import httpx
from mcp.server.fastmcp import FastMCP

from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.base_search import BASESearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.citeseerx import CiteSeerXSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.dblp import DBLPSearcher
from .academic_platforms.doaj import DOAJSearcher
from .academic_platforms.europepmc import EuropePMCSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.hal import HALSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.openaire import OpenAiresearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.pmc import PMCSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.sci_hub import SciHubFetcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.ssrn import SSRNSearcher
from .academic_platforms.unpaywall import UnpaywallResolver, UnpaywallSearcher
from .academic_platforms.zenodo import ZenodoSearcher
from .config import get_env, load_env_file

# Initialize MCP server
mcp = FastMCP("paper_search_server")
logger = logging.getLogger(__name__)
GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS = 20.0
SEARCH_PAPERS_SOURCE_TIMEOUT_SECONDS = 45.0
SEARCH_EXECUTOR_MAX_WORKERS = 32


class SearchExecutorSaturatedError(RuntimeError):
    """Raised instead of queuing unbounded work behind blocked providers."""


class _BoundedSearchExecutor:
    """Keep timed-out blocking searches isolated in a fixed-size worker pool."""

    def __init__(self, max_workers: int):
        self._max_workers = max_workers
        self._slots = BoundedSemaphore(max_workers)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="paper-search",
        )

    def submit(self, function, /, *args, **kwargs) -> Future:
        if not self._slots.acquire(blocking=False):
            raise SearchExecutorSaturatedError(
                "search worker capacity is exhausted by unfinished provider calls"
            )

        try:
            future = self._executor.submit(function, *args, **kwargs)
        except BaseException:
            self._slots.release()
            raise

        future.add_done_callback(lambda _future: self._slots.release())
        return future

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


_SEARCH_EXECUTOR = _BoundedSearchExecutor(SEARCH_EXECUTOR_MAX_WORKERS)

# Instances of searchers
arxiv_searcher = ArxivSearcher()
pubmed_searcher = PubMedSearcher()
biorxiv_searcher = BioRxivSearcher()
medrxiv_searcher = MedRxivSearcher()
google_scholar_searcher = GoogleScholarSearcher()
iacr_searcher = IACRSearcher()
semantic_searcher = SemanticSearcher()
crossref_searcher = CrossRefSearcher()
openalex_searcher = OpenAlexSearcher()
pmc_searcher = PMCSearcher()
core_searcher = CORESearcher()
europepmc_searcher = EuropePMCSearcher()
dblp_searcher = DBLPSearcher()
openaire_searcher = OpenAiresearcher()
citeseerx_searcher = CiteSeerXSearcher()
doaj_searcher = DOAJSearcher()
base_searcher = BASESearcher()
unpaywall_resolver = UnpaywallResolver()
unpaywall_searcher = UnpaywallSearcher(resolver=unpaywall_resolver)
zenodo_searcher = ZenodoSearcher()
hal_searcher = HALSearcher()
ssrn_searcher = SSRNSearcher()
# scihub_searcher = SciHubSearcher()


# Asynchronous helper to adapt synchronous searchers
# Runs blocking requests-based calls in a thread pool to avoid blocking the event loop.
async def async_search(searcher, query: str, max_results: int, **kwargs) -> List[Dict]:
    search_future = _SEARCH_EXECUTOR.submit(
        searcher.search,
        query,
        max_results=max_results,
        **kwargs,
    )
    papers = await asyncio.wrap_future(search_future)
    return [paper.to_dict() for paper in papers]


async def _run_search_with_timeout(
    source_name: str,
    search_task: Awaitable[List[Dict]],
    timeout_seconds: Optional[float] = None,
) -> List[Dict]:
    """Run one source without allowing it to stall a multi-source request."""
    if timeout_seconds is None:
        timeout_seconds = SEARCH_PAPERS_SOURCE_TIMEOUT_SECONDS

    try:
        return await asyncio.wait_for(search_task, timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"search for source '{source_name}' timed out after {timeout_seconds:g} seconds"
        ) from exc


ALL_SOURCES = [
    "arxiv",
    "pubmed",
    "biorxiv",
    "medrxiv",
    "google_scholar",
    "iacr",
    "semantic",
    "crossref",
    "openalex",
    "pmc",
    "core",
    "europepmc",
    "dblp",
    "openaire",
    "citeseerx",
    "doaj",
    "base",
    "zenodo",
    "hal",
    "ssrn",
    "unpaywall",
]


# ---------------------------------------------------------------------------
# Optional paid-platform connectors (disabled by default)
# Set PAPER_SEARCH_MCP_IEEE_API_KEY / PAPER_SEARCH_MCP_ACM_API_KEY to activate
# (legacy IEEE_API_KEY / ACM_API_KEY are also supported).
# ---------------------------------------------------------------------------
_ieee_api_key = get_env("IEEE_API_KEY", "")
_acm_api_key = get_env("ACM_API_KEY", "")

if _ieee_api_key:
    from .academic_platforms.ieee import IEEESearcher
    ieee_searcher = IEEESearcher()
    ALL_SOURCES.append("ieee")
    logger.info("IEEE Xplore enabled via configured environment key.")
else:
    ieee_searcher = None

if _acm_api_key:
    from .academic_platforms.acm import ACMSearcher
    acm_searcher = ACMSearcher()
    ALL_SOURCES.append("acm")
    logger.info("ACM Digital Library enabled via configured environment key.")
else:
    acm_searcher = None


def _parse_sources(sources: str) -> List[str]:
    if not sources or sources.strip().lower() == "all":
        return ALL_SOURCES

    normalized = [part.strip().lower() for part in sources.split(",") if part.strip()]
    return [source for source in normalized if source in ALL_SOURCES]


def _invalid_sources(sources: str) -> List[str]:
    """Return distinct requested source names that are unknown or unavailable."""
    if not sources or sources.strip().lower() == "all":
        return []

    normalized = [part.strip().lower() for part in sources.split(",") if part.strip()]
    return list(dict.fromkeys(source for source in normalized if source not in ALL_SOURCES))


def _paper_unique_key(paper: Dict[str, Any]) -> str:
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"

    title = (paper.get("title") or "").strip().lower()
    authors = (paper.get("authors") or "").strip().lower()
    if title:
        return f"title:{title}|authors:{authors}"

    paper_id = (paper.get("paper_id") or "").strip().lower()
    return f"id:{paper_id}"


def _dedupe_papers(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for paper in papers:
        key = _paper_unique_key(paper)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(paper)

    return deduped


def _safe_filename(filename_hint: str, default: str = "paper") -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename_hint).strip("._")
    if not safe:
        return default
    return safe[:120]


async def _download_from_url(
    pdf_url: str,
    save_path: str,
    filename_hint: str = "paper",
    expected_title: str = "",
    expected_doi: str = "",
) -> Optional[str]:
    """Download and, when identity hints are present, verify a fallback PDF."""
    if not pdf_url:
        return None

    output_name = f"{_safe_filename(filename_hint)}.pdf"
    output_path = os.path.join(save_path, output_name)
    temporary_path = ""

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(pdf_url)

        if response.status_code >= 400 or not response.content:
            return None

        content = bytes(response.content)
        content_type = (response.headers.get("content-type") or "").lower()
        if not _looks_like_pdf(content):
            logger.warning(
                "Resolved URL did not return PDF bytes: %s (content-type=%s)",
                pdf_url,
                content_type,
            )
            return None

        if expected_title or expected_doi:
            matches = await asyncio.to_thread(
                _pdf_matches_expected,
                content,
                expected_title,
                expected_doi,
            )
            if not matches:
                logger.warning(
                    "Downloaded PDF from %s could not be verified as title=%r DOI=%r",
                    pdf_url,
                    expected_title[:120],
                    expected_doi,
                )
                return None

        os.makedirs(save_path, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output_name}.",
            suffix=".part",
            dir=save_path,
            delete=False,
        ) as file_obj:
            temporary_path = file_obj.name
            file_obj.write(content)
        os.replace(temporary_path, output_path)
        temporary_path = ""
        return output_path
    except Exception as exc:
        logger.warning("Direct URL download failed for %s: %s", pdf_url, exc)
        return None
    finally:
        if temporary_path:
            try:
                os.remove(temporary_path)
            except OSError:
                pass


def _looks_like_pdf(content: bytes) -> bool:
    """Check the PDF header instead of trusting a URL suffix or content type."""
    return bool(content) and b"%PDF-" in content[:1024]


_TITLE_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "among",
        "based",
        "between",
        "from",
        "into",
        "study",
        "that",
        "their",
        "through",
        "using",
        "with",
    }
)


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = re.sub(r"(?<=\w)-\s+(?=\w)", "", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _title_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[^\W_]+", _normalize_text(value), flags=re.UNICODE)
        if len(token) >= 4 and token not in _TITLE_STOPWORDS
    }


def _normalize_doi(value: str) -> str:
    normalized = unquote((value or "").strip()).casefold()
    normalized = re.sub(
        r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)",
        "",
        normalized,
    )
    return re.sub(r"\s+", "", normalized).rstrip(".,;)")


def _title_similarity(a: str, b: str) -> float:
    """Return token F1 similarity for two titles, in the range [0, 1]."""
    if not a or not b:
        return 0.0
    tokens_a = _title_tokens(a)
    tokens_b = _title_tokens(b)
    if not tokens_a or not tokens_b:
        return 1.0 if _normalize_text(a) == _normalize_text(b) else 0.0
    return 2 * len(tokens_a & tokens_b) / (len(tokens_a) + len(tokens_b))


def _pdf_matches_expected(
    pdf_source: str | os.PathLike[str] | bytes,
    expected_title: str,
    expected_doi: str = "",
) -> bool:
    """Verify fallback PDF identity from its first three extractable pages.

    An unreadable or image-only document is unverifiable and therefore rejected
    by the fallback chain. Direct source downloads without identity hints are not
    affected by this conservative rule.
    """
    if not expected_title and not expected_doi:
        return True

    try:
        from pypdf import PdfReader

        reader_source = io.BytesIO(pdf_source) if isinstance(pdf_source, bytes) else pdf_source
        reader = PdfReader(reader_source)
    except Exception as exc:
        logger.debug("Could not parse fallback PDF for identity verification: %s", exc)
        return False

    try:
        text_parts = []
        for page in reader.pages[:3]:
            text_parts.append(page.extract_text() or "")
    except Exception as exc:
        logger.debug("Fallback PDF text extraction failed: %s", exc)
        return False

    text = "\n".join(text_parts)
    if not text.strip():
        return False

    normalized_text = _normalize_text(text)
    normalized_doi = _normalize_doi(expected_doi)
    if normalized_doi:
        text_without_whitespace = re.sub(r"\s+", "", normalized_text)
        if normalized_doi in text_without_whitespace:
            return True
        doi_alphanumeric = re.sub(r"[^a-z0-9]", "", normalized_doi)
        text_alphanumeric = re.sub(r"[^a-z0-9]", "", normalized_text)
        if doi_alphanumeric and doi_alphanumeric in text_alphanumeric:
            return True

    expected_tokens = _title_tokens(expected_title)
    if not expected_tokens:
        return False
    document_tokens = _title_tokens(text)
    matching_tokens = expected_tokens & document_tokens
    return len(matching_tokens) / len(expected_tokens) >= 0.6


async def _try_repository_fallback(
    doi: str,
    title: str,
    save_path: str,
    expected_title: str | None = None,
) -> tuple[Optional[str], str]:
    """Search OA repositories for a paper matching the DOI or title, then download.

    When `expected_title` is provided, candidate papers whose titles are too
    dissimilar are skipped — this prevents the fallback from returning a
    plausible-but-wrong PDF. The same `expected_title` is propagated to
    `_download_from_url` so the downloaded PDF's content is also verified.
    """
    repository_searchers = [
        ("openaire", openaire_searcher),
        ("core", core_searcher),
        ("europepmc", europepmc_searcher),
        ("pmc", pmc_searcher),
    ]

    validation_title = title if expected_title is None else expected_title
    normalized_expected_doi = _normalize_doi(doi)
    query_candidates = list(
        dict.fromkeys(
            candidate
            for candidate in ((doi or "").strip(), (title or "").strip())
            if candidate
        )
    )
    if not query_candidates:
        return None, "no DOI/title provided for repository fallback"

    repository_errors: List[str] = []
    rejected_candidates = 0
    attempted_urls: set[str] = set()

    for repo_name, searcher in repository_searchers:
        for query in query_candidates:
            try:
                papers = await asyncio.to_thread(searcher.search, query, max_results=3)
            except Exception as exc:
                repository_errors.append(f"{repo_name}:{exc}")
                continue

            if not papers:
                continue

            for paper in papers:
                pdf_url = str(getattr(paper, "pdf_url", "") or "").strip()
                if not pdf_url or pdf_url in attempted_urls:
                    continue

                candidate_doi = _normalize_doi(str(getattr(paper, "doi", "") or ""))
                doi_matches = bool(
                    normalized_expected_doi
                    and candidate_doi
                    and normalized_expected_doi == candidate_doi
                )
                if validation_title and not doi_matches:
                    candidate_title = str(getattr(paper, "title", "") or "")
                    if candidate_title:
                        sim = _title_similarity(candidate_title, validation_title)
                        if sim < 0.6:
                            rejected_candidates += 1
                            logger.warning(
                                "Repository %s fallback title mismatch (sim=%.2f): "
                                "'%s' vs expected '%s' — skipping",
                                repo_name,
                                sim,
                                candidate_title[:80],
                                validation_title[:80],
                            )
                            continue

                attempted_urls.add(pdf_url)
                raw_paper_id = getattr(paper, "paper_id", "")
                paper_id = str(raw_paper_id or query).strip()
                downloaded = await _download_from_url(
                    pdf_url,
                    save_path,
                    f"{repo_name}_{paper_id}",
                    expected_title=validation_title,
                    expected_doi=doi,
                )
                if downloaded:
                    return downloaded, ""
                rejected_candidates += 1

    if rejected_candidates:
        repository_errors.append(
            f"{rejected_candidates} candidate PDF(s) did not match the requested paper"
        )
    if not repository_errors:
        repository_errors.append("no repository PDF candidate found")
    return None, "; ".join(repository_errors)


@mcp.tool()
async def search_papers(
    query: str,
    max_results_per_source: int = 5,
    sources: str = "all",
    year: str = "",
) -> Dict[str, Any]:
    """Unified top-level search across all configured academic platforms.

    Args:
        query: Search query string.
        max_results_per_source: Max results to fetch from each selected source.
        sources: Comma-separated source names or 'all'.
            Available: arxiv,pubmed,biorxiv,medrxiv,google_scholar,iacr,semantic,crossref,openalex,pmc,core,europepmc,dblp,openaire,citeseerx,doaj,base,zenodo,hal,ssrn,unpaywall
        year: Optional year filter for Semantic Scholar only.
    Returns:
        Aggregated dictionary with per-source stats, errors, and deduplicated papers.
    """
    selected_sources = _parse_sources(sources)
    invalid_sources = _invalid_sources(sources)
    errors: Dict[str, str] = {
        source: "Unknown or unavailable source." for source in invalid_sources
    }

    if not selected_sources:
        errors["sources"] = "No valid sources selected."
        return {
            "query": query,
            "sources_requested": sources,
            "sources_used": [],
            "source_results": {},
            "errors": errors,
            "papers": [],
            "total": 0,
        }

    task_map = {}
    for source in selected_sources:
        if source == "arxiv":
            task_map[source] = search_arxiv(query, max_results_per_source)
        elif source == "pubmed":
            task_map[source] = search_pubmed(query, max_results_per_source)
        elif source == "biorxiv":
            task_map[source] = search_biorxiv(query, max_results_per_source)
        elif source == "medrxiv":
            task_map[source] = search_medrxiv(query, max_results_per_source)
        elif source == "google_scholar":
            task_map[source] = search_google_scholar(query, max_results_per_source)
        elif source == "iacr":
            task_map[source] = search_iacr(query, max_results_per_source, fetch_details=False)
        elif source == "semantic":
            task_map[source] = search_semantic(query, year=year, max_results=max_results_per_source)
        elif source == "crossref":
            task_map[source] = search_crossref(query, max_results=max_results_per_source)
        elif source == "openalex":
            task_map[source] = search_openalex(query, max_results_per_source)
        elif source == "pmc":
            task_map[source] = search_pmc(query, max_results_per_source)
        elif source == "core":
            task_map[source] = search_core(query, max_results_per_source)
        elif source == "europepmc":
            task_map[source] = search_europepmc(query, max_results_per_source)
        elif source == "dblp":
            task_map[source] = search_dblp(query, max_results_per_source)
        elif source == "openaire":
            task_map[source] = search_openaire(query, max_results_per_source)
        elif source == "citeseerx":
            task_map[source] = search_citeseerx(query, max_results_per_source)
        elif source == "doaj":
            task_map[source] = search_doaj(query, max_results_per_source)
        elif source == "base":
            task_map[source] = search_base(query, max_results_per_source)
        elif source == "zenodo":
            task_map[source] = search_zenodo(query, max_results_per_source)
        elif source == "hal":
            task_map[source] = search_hal(query, max_results_per_source)
        elif source == "ssrn":
            task_map[source] = search_ssrn(query, max_results_per_source)
        elif source == "unpaywall":
            task_map[source] = search_unpaywall(query, max_results_per_source)
        elif source == "ieee":
            if ieee_searcher is not None:
                task_map[source] = async_search(ieee_searcher, query, max_results_per_source)
        elif source == "acm":
            if acm_searcher is not None:
                task_map[source] = async_search(acm_searcher, query, max_results_per_source)

    source_names = list(task_map.keys())
    source_outputs = await asyncio.gather(
        *(
            _run_search_with_timeout(source_name, search_task)
            for source_name, search_task in task_map.items()
        ),
        return_exceptions=True,
    )

    source_results: Dict[str, int] = {}
    merged_papers: List[Dict[str, Any]] = []

    for source_name, output in zip(source_names, source_outputs):
        if isinstance(output, Exception):
            errors[source_name] = str(output)
            source_results[source_name] = 0
            continue

        source_results[source_name] = len(output)
        for paper in output:
            if not paper.get("source"):
                paper["source"] = source_name
            merged_papers.append(paper)

    deduped_papers = _dedupe_papers(merged_papers)

    return {
        "query": query,
        "sources_requested": sources,
        "sources_used": source_names,
        "source_results": source_results,
        "errors": errors,
        "papers": deduped_papers,
        "total": len(deduped_papers),
        "raw_total": len(merged_papers),
    }


# Tool definitions
@mcp.tool()
async def search_arxiv(query: str, max_results: int = 10, sort_by: str = 'relevance', sort_order: str = 'descending') -> List[Dict]:
    """Search academic papers from arXiv.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
        sort_by: Sort criterion — 'relevance', 'submittedDate', or 'lastUpdatedDate' (default: 'relevance').
        sort_order: Sort direction — 'descending' or 'ascending' (default: 'descending').
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(arxiv_searcher, query, max_results, sort_by=sort_by, sort_order=sort_order)
    return papers if papers else []


@mcp.tool()
async def search_pubmed(query: str, max_results: int = 10, sort: str = 'relevance') -> List[Dict]:
    """Search academic papers from PubMed.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
        sort: Sort order — 'relevance' or 'pub_date' (default: 'relevance').
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(pubmed_searcher, query, max_results, sort=sort)
    return papers if papers else []


@mcp.tool()
async def search_biorxiv(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from bioRxiv.

    Note: bioRxiv does not provide full-text keyword search. The query may be a DOI,
    a date range such as '2024-01-01/2024-01-31', a category such as
    'bioinformatics', or an empty string for recent papers.

    Args:
        query: DOI, date range, category name, or an empty string for recent papers.
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(biorxiv_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_medrxiv(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from medRxiv.

    Note: medRxiv API filters by category name within the last 30 days, not full-text
    keyword search. Use a category keyword such as 'infectious_diseases',
    'cardiovascular_medicine', 'oncology', etc.

    Args:
        query: Category name to filter by (e.g., 'infectious_diseases', 'oncology').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(medrxiv_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_google_scholar(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from Google Scholar.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    try:
        papers = await _run_search_with_timeout(
            "google_scholar",
            async_search(
                google_scholar_searcher,
                query,
                max_results,
                timeout_seconds=GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS - 1.0,
            ),
            timeout_seconds=GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Google Scholar search timed out after %.1fs for query=%r; returning no results.",
            GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS,
            query,
        )
        return []
    return papers if papers else []


@mcp.tool()
async def search_iacr(
    query: str, max_results: int = 10, fetch_details: bool = True
) -> List[Dict]:
    """Search academic papers from IACR ePrint Archive.

    Args:
        query: Search query string (e.g., 'cryptography', 'secret sharing').
        max_results: Maximum number of papers to return (default: 10).
        fetch_details: Whether to fetch detailed information for each paper (default: True).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await asyncio.to_thread(iacr_searcher.search, query, max_results, fetch_details)
    return [paper.to_dict() for paper in papers] if papers else []


@mcp.tool()
async def download_arxiv(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF of an arXiv paper.

    Args:
        paper_id: arXiv paper ID (e.g., '2106.12345').
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """
    return await asyncio.to_thread(arxiv_searcher.download_pdf, paper_id, save_path)


@mcp.tool()
async def download_pubmed(paper_id: str, save_path: str = "./downloads") -> str:
    """Attempt to download PDF of a PubMed paper.

    Args:
        paper_id: PubMed ID (PMID).
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Message indicating that direct PDF download is not supported.
    """
    try:
        return pubmed_searcher.download_pdf(paper_id, save_path)
    except NotImplementedError as e:
        return str(e)


@mcp.tool()
async def download_biorxiv(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF of a bioRxiv paper.

    Args:
        paper_id: bioRxiv DOI.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """
    return biorxiv_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def download_medrxiv(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF of a medRxiv paper.

    Args:
        paper_id: medRxiv DOI.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """
    return medrxiv_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def download_iacr(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF of an IACR ePrint paper.

    Args:
        paper_id: IACR paper ID (e.g., '2009/101').
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """
    return iacr_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_arxiv_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from an arXiv paper PDF.

    Args:
        paper_id: arXiv paper ID (e.g., '2106.12345').
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return arxiv_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def read_pubmed_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a PubMed paper.

    Args:
        paper_id: PubMed ID (PMID).
        save_path: Directory where the PDF would be saved (unused).
    Returns:
        str: Message indicating that direct paper reading is not supported.
    """
    return pubmed_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def read_biorxiv_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a bioRxiv paper PDF.

    Args:
        paper_id: bioRxiv DOI.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return biorxiv_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def read_medrxiv_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a medRxiv paper PDF.

    Args:
        paper_id: medRxiv DOI.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return medrxiv_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def read_iacr_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from an IACR ePrint paper PDF.

    Args:
        paper_id: IACR paper ID (e.g., '2009/101').
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return iacr_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def search_semantic(query: str, year: str = "", max_results: int = 10) -> List[Dict]:
    """Search academic papers from Semantic Scholar.

    Args:
        query: Search query string (e.g., 'machine learning').
        year: Optional year filter (e.g., '2019', '2016-2020', '2010-', '-2015').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    kwargs = {}
    if year:
        kwargs['year'] = year
    papers = await async_search(semantic_searcher, query, max_results, **kwargs)
    return papers if papers else []


@mcp.tool()
async def download_semantic(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF of a Semantic Scholar paper.    

    Args:
        paper_id: Semantic Scholar paper ID, Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """ 
    return semantic_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_semantic_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a Semantic Scholar paper. 

    Args:
        paper_id: Semantic Scholar paper ID, Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return semantic_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def search_crossref(
    query: str,
    max_results: int = 10,
    filter: str = "",
    sort: str = "",
    order: str = "",
) -> List[Dict]:
    """Search academic papers from CrossRef database.
    
    CrossRef is a scholarly infrastructure organization that provides 
    persistent identifiers (DOIs) for scholarly content and metadata.
    It's one of the largest citation databases covering millions of 
    academic papers, journals, books, and other scholarly content.

    Args:
        query: Search query string (e.g., 'machine learning', 'climate change').
        max_results: Maximum number of papers to return (default: 10, max: 1000).
        filter: CrossRef filter string (e.g., 'has-full-text:true,from-pub-date:2020').
        sort: Sort field ('relevance', 'published', 'updated', 'deposited', etc.).
        order: Sort order ('asc' or 'desc').
    Returns:
        List of paper metadata in dictionary format.
    """
    extra = {
        key: value
        for key, value in {"filter": filter, "sort": sort, "order": order}.items()
        if value
    }
    papers = await async_search(crossref_searcher, query, max_results, **extra)
    return papers if papers else []


@mcp.tool()
async def get_crossref_paper_by_doi(doi: str) -> Dict:
    """Get a specific paper from CrossRef by its DOI.

    Args:
        doi: Digital Object Identifier (e.g., '10.1038/nature12373').
    Returns:
        Paper metadata in dictionary format, or empty dict if not found.
        
    Example:
        get_crossref_paper_by_doi("10.1038/nature12373")
    """
    paper = await asyncio.to_thread(crossref_searcher.get_paper_by_doi, doi)
    return paper.to_dict() if paper else {}


@mcp.tool()
async def download_crossref(paper_id: str, save_path: str = "./downloads") -> str:
    """Attempt to download PDF of a CrossRef paper.

    Args:
        paper_id: CrossRef DOI (e.g., '10.1038/nature12373').
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Message indicating that direct PDF download is not supported.
        
    Note:
        CrossRef is a citation database and doesn't provide direct PDF downloads.
        Use the DOI to access the paper through the publisher's website.
    """
    try:
        return crossref_searcher.download_pdf(paper_id, save_path)
    except NotImplementedError as e:
        return str(e)


@mcp.tool()
async def download_scihub(
    identifier: str,
    save_path: str = "./downloads",
    base_url: str = "https://sci-hub.se",
) -> str:
    """Download paper PDF via Sci-Hub (optional fallback connector).

    Args:
        identifier: DOI, title, PMID, or paper URL.
        save_path: Directory to save the PDF.
        base_url: Sci-Hub mirror URL.
    Returns:
        Downloaded PDF path on success; error message on failure.
    """
    fetcher = SciHubFetcher(base_url=base_url, output_dir=save_path)
    result = await asyncio.to_thread(fetcher.download_pdf, identifier)
    if result:
        return result
    return "Sci-Hub download failed. Try DOI first, then title, or change mirror URL."


@mcp.tool()
async def download_with_fallback(
    source: str,
    paper_id: str,
    doi: str = "",
    title: str = "",
    save_path: str = "./downloads",
    use_scihub: bool = False,
    scihub_base_url: str = "https://sci-hub.se",
) -> str:
    """Try source-native download, OA repositories, Unpaywall, then optional Sci-Hub.

    Args:
        source: Source name (arxiv, biorxiv, medrxiv, iacr, semantic, crossref, pubmed, pmc, core, europepmc, citeseerx, doaj, base, zenodo, hal, ssrn).
        paper_id: Source-native paper identifier.
        doi: Optional DOI used for repository/unpaywall/Sci-Hub fallback.
        title: Optional title used for repository/Sci-Hub fallback when DOI is unavailable.
        save_path: Directory to save downloaded files.
        use_scihub: Whether to fallback to Sci-Hub after OA attempts fail. Disabled by default.
        scihub_base_url: Sci-Hub mirror URL for fallback.
    Returns:
        Download path on success or explanatory error message.
    """
    source_name = source.strip().lower()

    primary_downloaders = {
        "arxiv": arxiv_searcher.download_pdf,
        "biorxiv": biorxiv_searcher.download_pdf,
        "medrxiv": medrxiv_searcher.download_pdf,
        "iacr": iacr_searcher.download_pdf,
        "semantic": semantic_searcher.download_pdf,
        "pubmed": pubmed_searcher.download_pdf,
        "crossref": crossref_searcher.download_pdf,
        "pmc": pmc_searcher.download_pdf,
        "core": core_searcher.download_pdf,
        "europepmc": europepmc_searcher.download_pdf,
        "citeseerx": citeseerx_searcher.download_pdf,
        "doaj": doaj_searcher.download_pdf,
        "base": base_searcher.download_pdf,
        "zenodo": zenodo_searcher.download_pdf,
        "hal": hal_searcher.download_pdf,
        "ssrn": ssrn_searcher.download_pdf,
    }

    attempt_errors: List[str] = []
    primary_error = ""
    if source_name in primary_downloaders:
        try:
            primary_result = await asyncio.to_thread(primary_downloaders[source_name], paper_id, save_path)
            if isinstance(primary_result, str) and os.path.exists(primary_result):
                return primary_result
            if isinstance(primary_result, str) and primary_result:
                primary_error = primary_result
        except Exception as exc:
            primary_error = str(exc)
            logger.warning("Primary download failed for %s/%s: %s", source_name, paper_id, exc)
    else:
        primary_error = f"Unsupported source '{source_name}' for primary download."

    if primary_error:
        attempt_errors.append(f"primary: {primary_error}")

    repository_result, repository_error = await _try_repository_fallback(
        doi, title, save_path, expected_title=title,
    )
    if repository_result:
        return repository_result
    if repository_error:
        attempt_errors.append(f"repositories: {repository_error}")

    normalized_doi = (doi or "").strip()
    if normalized_doi:
        unpaywall_url = await asyncio.to_thread(unpaywall_resolver.resolve_best_pdf_url, normalized_doi)
        if unpaywall_url:
            unpaywall_result = await _download_from_url(
                unpaywall_url, save_path, f"unpaywall_{normalized_doi}",
                expected_title=title, expected_doi=normalized_doi,
            )
            if unpaywall_result:
                return unpaywall_result
            attempt_errors.append("unpaywall: resolved OA URL but download failed (or content did not match expected title)")
        else:
            attempt_errors.append("unpaywall: no OA URL found (or PAPER_SEARCH_MCP_UNPAYWALL_EMAIL/UNPAYWALL_EMAIL missing)")
    else:
        attempt_errors.append("unpaywall: DOI not provided")

    if not use_scihub:
        return "Download failed after OA fallback chain. Details: " + " | ".join(attempt_errors)

    fallback_identifier = (doi or "").strip() or (title or "").strip() or paper_id
    fetcher = SciHubFetcher(base_url=scihub_base_url, output_dir=save_path)
    fallback_result = await asyncio.to_thread(fetcher.download_pdf, fallback_identifier)
    if fallback_result and os.path.isfile(fallback_result):
        verified = await asyncio.to_thread(
            _pdf_matches_expected,
            fallback_result,
            title,
            doi,
        )
        if verified:
            return fallback_result

        attempt_errors.append(
            "scihub: downloaded PDF content did not match the requested paper"
        )
        try:
            os.remove(fallback_result)
        except OSError as exc:
            logger.warning(
                "Could not remove unverified Sci-Hub download %s: %s",
                fallback_result,
                exc,
            )

    return "Download failed after OA fallback chain and Sci-Hub fallback. Details: " + " | ".join(attempt_errors)


@mcp.tool()
async def read_crossref_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Attempt to read and extract text content from a CrossRef paper.

    Args:
        paper_id: CrossRef DOI (e.g., '10.1038/nature12373').
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: Message indicating that direct paper reading is not supported.
        
    Note:
        CrossRef is a citation database and doesn't provide direct paper content.
        Use the DOI to access the paper through the publisher's website.
    """
    return crossref_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def search_openalex(
    query: str,
    max_results: int = 10,
    filter: Optional[str] = None,
) -> List[Dict]:
    """Search academic papers from OpenAlex.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
        filter: OpenAlex filter string.
            Examples:
            - 'is_oa:true,from_publication_date:2024-01-01'
            - 'has_pdf_url:true,publication_year:2024'
            - 'primary_location.source.id:S137773608' for Nature
            See https://docs.openalex.org/api-entities/works/filter-works
            for the full list of supported work filters.
    Returns:
        List of paper metadata in dictionary format.
    """
    extra = {k: v for k, v in {"filter": filter}.items() if v is not None}
    papers = await async_search(openalex_searcher, query, max_results, **extra)
    return papers if papers else []


@mcp.tool()
async def search_pmc(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from PubMed Central (PMC).

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(pmc_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_core(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from CORE.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(core_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_europepmc(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from Europe PMC.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(europepmc_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_dblp(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from dblp computer science bibliography.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(dblp_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_openaire(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from OpenAIRE European Open Access infrastructure.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(openaire_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_citeseerx(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from CiteSeerX digital library.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(citeseerx_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_doaj(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from DOAJ (Directory of Open Access Journals).

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(doaj_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_base(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from BASE (Bielefeld Academic Search Engine).

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(base_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_zenodo(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from Zenodo open repository.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(zenodo_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_hal(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from HAL open archive.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(hal_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_ssrn(query: str, max_results: int = 10) -> List[Dict]:
    """Search metadata records from SSRN.

    Note: SSRN connector is metadata-only and does not support direct PDF download.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(ssrn_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_unpaywall(query: str, max_results: int = 10) -> List[Dict]:
    """Lookup a DOI via Unpaywall and return OA metadata.

    Unpaywall is DOI-centric and does not support generic keyword search.
    This tool extracts the first DOI from `query` and returns at most one record.

    Args:
        query: DOI string or text containing a DOI.
        max_results: Kept for API consistency; Unpaywall returns max 1 record.
    Returns:
        List with one paper metadata dict when DOI is resolvable, else empty list.
    """
    papers = await async_search(unpaywall_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def read_dblp_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Attempt to read and extract text content from a dblp paper.

    Note: dblp doesn't provide direct paper content access.
    This function returns an informative message.

    Args:
        paper_id: dblp paper identifier.
        save_path: Directory where the PDF would be saved (unused).
    Returns:
        str: Message indicating that direct paper reading is not supported.
    """
    return dblp_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_dblp(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from dblp.

    Note: dblp doesn't provide direct PDF access.
    This function returns an informative message.

    Args:
        paper_id: dblp paper identifier.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Message indicating that direct PDF download is not supported.
    """
    return dblp_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_openaire_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Attempt to read and extract text content from an OpenAIRE paper.

    Args:
        paper_id: OpenAIRE paper identifier.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: Extracted text or error message.
    """
    return openaire_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_openaire(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from OpenAIRE.

    Args:
        paper_id: OpenAIRE paper identifier.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Path to downloaded PDF or error message.
    """
    return openaire_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_citeseerx_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a CiteSeerX paper.

    Args:
        paper_id: CiteSeerX paper identifier.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: Extracted text or fallback abstract/error message.
    """
    return citeseerx_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_citeseerx(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from CiteSeerX.

    Args:
        paper_id: CiteSeerX paper identifier.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Path to downloaded PDF or error message.
    """
    return citeseerx_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_doaj_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a DOAJ paper.

    Args:
        paper_id: DOAJ paper identifier.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: Extracted text content.
    """
    return doaj_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_doaj(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from DOAJ.

    Args:
        paper_id: DOAJ paper identifier.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Path to downloaded PDF.
    """
    return doaj_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_base_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a BASE paper.

    Args:
        paper_id: BASE paper identifier.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: Extracted text content.
    """
    return base_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_base(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from BASE.

    Args:
        paper_id: BASE paper identifier.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Path to downloaded PDF.
    """
    return base_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_zenodo_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a Zenodo paper.

    Args:
        paper_id: Zenodo paper identifier.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: Extracted text content.
    """
    return zenodo_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_zenodo(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from Zenodo.

    Args:
        paper_id: Zenodo paper identifier.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Path to downloaded PDF.
    """
    return zenodo_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_hal_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read and extract text content from a HAL paper.

    Args:
        paper_id: HAL paper identifier.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: Extracted text content.
    """
    return hal_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_hal(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from HAL.

    Args:
        paper_id: HAL paper identifier.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Path to downloaded PDF.
    """
    return hal_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_ssrn_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Read paper content from SSRN.

    Note: SSRN connector is metadata-only and read is not supported.

    Args:
        paper_id: SSRN paper identifier.
        save_path: Directory where the PDF is/will be saved (unused).
    Returns:
        str: Error message from metadata-only SSRN connector.
    """
    return ssrn_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_ssrn(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from SSRN.

    Note: SSRN connector is metadata-only and download is not supported.

    Args:
        paper_id: SSRN paper identifier.
        save_path: Directory to save the PDF (unused).
    Returns:
        str: Error message from metadata-only SSRN connector.
    """
    return ssrn_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_openalex_paper(paper_id: str, save_path: str = "./downloads") -> str:
    """Attempt to read and extract text content from an OpenAlex paper.

    Args:
        paper_id: OpenAlex paper ID.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        str: Message indicating that direct paper reading is not supported natively.
    """
    return openalex_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def download_openalex(paper_id: str, save_path: str = "./downloads") -> str:
    """Download PDF for a paper from OpenAlex.

    Args:
        paper_id: OpenAlex paper ID.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        str: Error message, typically OpenAlex relies on extracted pdf_url instead of direct downloads.
    """
    return await asyncio.to_thread(openalex_searcher.download_pdf, paper_id, save_path)


# ---------------------------------------------------------------------------
# Optional IEEE Xplore tools — registered only when API key is set
# ---------------------------------------------------------------------------
if ieee_searcher is not None:
    @mcp.tool()
    async def search_ieee(query: str, max_results: int = 10) -> List[Dict]:
        """Search IEEE Xplore for papers.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY).

        Args:
            query: Search query string.
            max_results: Maximum number of results (default: 10).
        Returns:
            List of paper dicts from IEEE Xplore.
        """
        return await async_search(ieee_searcher, query, max_results)

    @mcp.tool()
    async def download_ieee(paper_id: str, save_path: str = "./downloads") -> str:
        """Download a PDF from IEEE Xplore.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY) and institutional access.

        Args:
            paper_id: IEEE Xplore paper identifier.
            save_path: Directory to save the PDF (default: './downloads').
        Returns:
            str: Path to saved PDF or error message.
        """
        return await asyncio.to_thread(ieee_searcher.download_pdf, paper_id, save_path)

    @mcp.tool()
    async def read_ieee_paper(paper_id: str, save_path: str = "./downloads") -> str:
        """Download and read an IEEE Xplore paper.  Requires PAPER_SEARCH_MCP_IEEE_API_KEY (or IEEE_API_KEY).

        Args:
            paper_id: IEEE Xplore paper identifier.
            save_path: Directory where the PDF is/will be saved (default: './downloads').
        Returns:
            str: Extracted text content.
        """
        return ieee_searcher.read_paper(paper_id, save_path)


# ---------------------------------------------------------------------------
# Optional ACM Digital Library tools — registered only when API key is set
# ---------------------------------------------------------------------------
if acm_searcher is not None:
    @mcp.tool()
    async def search_acm(query: str, max_results: int = 10) -> List[Dict]:
        """Search ACM Digital Library for papers.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY).

        Args:
            query: Search query string.
            max_results: Maximum number of results (default: 10).
        Returns:
            List of paper dicts from ACM DL.
        """
        return await async_search(acm_searcher, query, max_results)

    @mcp.tool()
    async def download_acm(paper_id: str, save_path: str = "./downloads") -> str:
        """Download a PDF from ACM Digital Library.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY) and institutional access.

        Args:
            paper_id: ACM DL paper identifier.
            save_path: Directory to save the PDF (default: './downloads').
        Returns:
            str: Path to saved PDF or error message.
        """
        return await asyncio.to_thread(acm_searcher.download_pdf, paper_id, save_path)

    @mcp.tool()
    async def read_acm_paper(paper_id: str, save_path: str = "./downloads") -> str:
        """Download and read an ACM Digital Library paper.  Requires PAPER_SEARCH_MCP_ACM_API_KEY (or ACM_API_KEY).

        Args:
            paper_id: ACM DL paper identifier.
            save_path: Directory where the PDF is/will be saved (default: './downloads').
        Returns:
            str: Extracted text content.
        """
        return acm_searcher.read_paper(paper_id, save_path)


def _wait_for_windows_process_exit(process_id: int) -> bool:
    """Wait for a Windows process handle to become signalled."""
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    wait_object_0 = 0x00000000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        logger.warning(
            "Could not watch MCP client process %s (Windows error %s)",
            process_id,
            ctypes.get_last_error(),
        )
        return False

    try:
        result = kernel32.WaitForSingleObject(handle, infinite)
    finally:
        kernel32.CloseHandle(handle)

    if result != wait_object_0:
        logger.warning(
            "Waiting for MCP client process %s failed (result %#x)",
            process_id,
            result,
        )
        return False
    return True


def _exit_when_orphaned(poll_seconds: float = 5.0) -> None:
    """Exit if the MCP client that spawned this stdio server goes away.

    A stdio server is owned by exactly one client. When that client dies without
    closing the pipe cleanly, ``mcp.run`` keeps blocking on stdin and the process
    survives indefinitely, re-adopted by init. They accumulate: nine of these had
    piled up on one developer machine, the oldest running for over a day.

    Watch for reparenting and leave.
    """
    original_ppid = os.getppid()
    if os.name == "nt":
        # Windows keeps reporting the original parent PID after that process has
        # exited, so polling getppid() cannot detect orphaning. A process handle
        # becomes signalled at termination and works without an extra dependency.
        if not _wait_for_windows_process_exit(original_ppid):
            return
        logger.info("MCP client process %s exited; shutting down", original_ppid)
        os._exit(0)
        return

    while True:
        time.sleep(poll_seconds)
        ppid = os.getppid()
        if ppid == 1 or ppid != original_ppid:
            logger.info(
                "MCP client gone (ppid %s -> %s); shutting down", original_ppid, ppid
            )
            os._exit(0)
            return


def _server_env(name: str, default: str) -> str:
    """Return a server setting, preferring the repository-wide env prefix."""
    load_env_file()
    for key in (f"PAPER_SEARCH_MCP_{name}", f"PAPER_SEARCH_{name}"):
        value = os.environ.get(key)
        if value is not None and value.strip():
            return value.strip()
    return default


def _valid_port(raw: str) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "port must be an integer between 1 and 65535"
        ) from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be an integer between 1 and 65535")
    return port


def _valid_http_path(raw: str) -> str:
    path = raw.strip()
    if not path.startswith("/"):
        raise argparse.ArgumentTypeError("path must start with '/'")
    return path


def _build_server_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper Search MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default=_server_env("TRANSPORT", "stdio"),
        help="MCP transport (default: stdio; env: PAPER_SEARCH_MCP_TRANSPORT)",
    )
    parser.add_argument(
        "--host",
        default=_server_env("HOST", "127.0.0.1"),
        help="Network bind host (env: PAPER_SEARCH_MCP_HOST)",
    )
    parser.add_argument(
        "--port",
        type=_valid_port,
        default=_server_env("PORT", "8000"),
        help="Network bind port (env: PAPER_SEARCH_MCP_PORT)",
    )
    parser.add_argument(
        "--path",
        type=_valid_http_path,
        default=_server_env("PATH", "/mcp"),
        help="Streamable HTTP endpoint path (env: PAPER_SEARCH_MCP_PATH)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run over stdio or a shared network transport.

    Command-line arguments override environment settings. ``stdio`` stays the
    default, so existing MCP client configurations remain compatible. The
    shorter ``PAPER_SEARCH_*`` names introduced by PR #114 remain accepted as
    aliases for the preferred ``PAPER_SEARCH_MCP_*`` names.
    """
    args = _build_server_parser().parse_args(argv)

    if args.transport == "stdio":
        # Only meaningful for stdio: an http server has no owning client to outlive.
        threading.Thread(
            target=_exit_when_orphaned, name="orphan-watchdog", daemon=True
        ).start()
        mcp.run(transport="stdio")
        return

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    if args.transport == "streamable-http":
        mcp.settings.streamable_http_path = args.path
    logger.info(
        "serving %s on %s:%s", args.transport, mcp.settings.host, mcp.settings.port
    )
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
