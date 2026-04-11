# paper_search_mcp/server.py
import asyncio
import hmac
import logging
import os
from typing import Any, Dict, List, Optional
import httpx
import uvicorn
from dotenv import load_dotenv
from .config import get_env
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from mcp.server.transport_security import TransportSecuritySettings
from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.pmc import PMCSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.europepmc import EuropePMCSearcher
from .academic_platforms.sci_hub import SciHubFetcher
from .academic_platforms.dblp import DBLPSearcher
from .academic_platforms.openaire import OpenAiresearcher
from .academic_platforms.citeseerx import CiteSeerXSearcher
from .academic_platforms.doaj import DOAJSearcher
from .academic_platforms.base_search import BASESearcher
from .academic_platforms.unpaywall import UnpaywallResolver, UnpaywallSearcher
from .academic_platforms.zenodo import ZenodoSearcher
from .academic_platforms.hal import HALSearcher
from .academic_platforms.ssrn import SSRNSearcher
from .utils import extract_doi

# from .academic_platforms.hub import SciHubSearcher
from .paper import Paper

load_dotenv()

BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")
if not BEARER_TOKEN:
    raise RuntimeError("BEARER_TOKEN environment variable must be set")

DEFAULT_DOWNLOAD_PATH = os.environ.get("DOWNLOAD_PATH", "./downloads")
MCP_MESSAGES_PATH = os.environ.get("MCP_MESSAGES_PATH", "/messages/")

LARAVEL_INGEST_URL = os.environ.get("LARAVEL_INGEST_URL", "")
LARAVEL_MCP_TOKEN = os.environ.get("LARAVEL_MCP_TOKEN", "")


class BearerAuthMiddleware:
    """ASGI middleware that validates Bearer token on every HTTP request."""

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth_value = headers.get(b"authorization", b"").decode()
            if (
                not auth_value.startswith("Bearer ")
                or not hmac.compare_digest(auth_value[7:], self.token)
            ):
                response = Response("Unauthorized", status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Initialize MCP server
mcp = FastMCP("paper_search_server")
logger = logging.getLogger(__name__)

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
    if 'year' in kwargs:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, year=kwargs['year'])
    elif kwargs:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, **kwargs)
    else:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results)
    return [paper.to_dict() for paper in papers]


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


async def _download_from_url(pdf_url: str, save_path: str, filename_hint: str = "paper") -> Optional[str]:
    if not pdf_url:
        return None

    os.makedirs(save_path, exist_ok=True)
    output_name = f"{_safe_filename(filename_hint)}.pdf"
    output_path = os.path.join(save_path, output_name)

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(pdf_url)

        if response.status_code >= 400 or not response.content:
            return None

        content_type = (response.headers.get("content-type") or "").lower()
        is_pdf = "pdf" in content_type or response.content.startswith(b"%PDF") or pdf_url.lower().endswith(".pdf")
        if not is_pdf:
            logger.warning("Resolved URL is not a PDF candidate: %s (content-type=%s)", pdf_url, content_type)
            return None

        with open(output_path, "wb") as file_obj:
            file_obj.write(response.content)

        return output_path
    except Exception as exc:
        logger.warning("Direct URL download failed for %s: %s", pdf_url, exc)
        return None


async def _try_repository_fallback(doi: str, title: str, save_path: str) -> tuple[Optional[str], str]:
    repository_searchers = [
        ("openaire", openaire_searcher),
        ("core", core_searcher),
        ("europepmc", europepmc_searcher),
        ("pmc", pmc_searcher),
    ]

    query_candidates = [(doi or "").strip(), (title or "").strip()]
    query_candidates = [candidate for candidate in query_candidates if candidate]
    if not query_candidates:
        return None, "no DOI/title provided for repository fallback"

    repository_errors: List[str] = []

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
                pdf_url = (getattr(paper, "pdf_url", "") or "").strip()
                if not pdf_url:
                    continue

                paper_id = (getattr(paper, "paper_id", "") or query).strip()
                downloaded = await _download_from_url(pdf_url, save_path, f"{repo_name}_{paper_id}")
                if downloaded:
                    return downloaded, ""

    return None, "; ".join(repository_errors)


@mcp.tool()
async def search_papers(
    query: str,
    max_results_per_source: int = 5,
    sources: str = "all",
    year: Optional[str] = None,
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

    if not selected_sources:
        return {
            "query": query,
            "sources_requested": sources,
            "sources_used": [],
            "source_results": {},
            "errors": {"sources": "No valid sources selected."},
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
    source_outputs = await asyncio.gather(*task_map.values(), return_exceptions=True)

    source_results: Dict[str, int] = {}
    errors: Dict[str, str] = {}
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
async def search_arxiv(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from arXiv.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(arxiv_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_pubmed(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from PubMed.

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(pubmed_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_biorxiv(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from bioRxiv.

    Note: bioRxiv API filters by category name within the last 30 days, not full-text
    keyword search. Use a category keyword such as 'bioinformatics', 'neuroscience',
    'cell biology', etc.

    Args:
        query: Category name to filter by (e.g., 'bioinformatics', 'neuroscience').
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
    papers = await async_search(google_scholar_searcher, query, max_results)
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
async def download_arxiv(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Download PDF of an arXiv paper.

    Args:
        paper_id: arXiv paper ID (e.g., '2106.12345').
        save_path: Directory to save the PDF (uses shared volume by default).
    Returns:
        Path to the downloaded PDF file.
    """
    return await asyncio.to_thread(arxiv_searcher.download_pdf, paper_id, save_path)


@mcp.tool()
async def download_pubmed(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Attempt to download PDF of a PubMed paper.

    Args:
        paper_id: PubMed ID (PMID).
        save_path: Directory to save the PDF (uses shared volume by default).
    Returns:
        str: Message indicating that direct PDF download is not supported.
    """
    os.makedirs(save_path, exist_ok=True)
    try:
        return pubmed_searcher.download_pdf(paper_id, save_path)
    except NotImplementedError as e:
        return str(e)


@mcp.tool()
async def download_biorxiv(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Download PDF of a bioRxiv paper.

    Args:
        paper_id: bioRxiv DOI.
        save_path: Directory to save the PDF (uses shared volume by default).
    Returns:
        Path to the downloaded PDF file.
    """
    os.makedirs(save_path, exist_ok=True)
    return biorxiv_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def download_medrxiv(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Download PDF of a medRxiv paper.

    Args:
        paper_id: medRxiv DOI.
        save_path: Directory to save the PDF (uses shared volume by default).
    Returns:
        Path to the downloaded PDF file.
    """
    os.makedirs(save_path, exist_ok=True)
    return medrxiv_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def download_iacr(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Download PDF of an IACR ePrint paper.

    Args:
        paper_id: IACR paper ID (e.g., '2009/101').
        save_path: Directory to save the PDF (uses shared volume by default).
    Returns:
        Path to the downloaded PDF file.
    """
    os.makedirs(save_path, exist_ok=True)
    return iacr_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_arxiv_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Read and extract text content from an arXiv paper PDF.

    Args:
        paper_id: arXiv paper ID (e.g., '2106.12345').
        save_path: Directory where the PDF is/will be saved (uses shared volume by default).
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return arxiv_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def read_pubmed_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Read and extract text content from a PubMed paper.

    Args:
        paper_id: PubMed ID (PMID).
        save_path: Directory where the PDF would be saved (unused).
    Returns:
        str: Message indicating that direct paper reading is not supported.
    """
    return pubmed_searcher.read_paper(paper_id, save_path)


@mcp.tool()
async def read_biorxiv_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Read and extract text content from a bioRxiv paper PDF.

    Args:
        paper_id: bioRxiv DOI.
        save_path: Directory where the PDF is/will be saved (uses shared volume by default).
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return biorxiv_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def read_medrxiv_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Read and extract text content from a medRxiv paper PDF.

    Args:
        paper_id: medRxiv DOI.
        save_path: Directory where the PDF is/will be saved (uses shared volume by default).
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return medrxiv_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def read_iacr_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Read and extract text content from an IACR ePrint paper PDF.

    Args:
        paper_id: IACR paper ID (e.g., '2009/101').
        save_path: Directory where the PDF is/will be saved (uses shared volume by default).
    Returns:
        str: The extracted text content of the paper.
    """
    try:
        return iacr_searcher.read_paper(paper_id, save_path)
    except Exception as e:
        print(f"Error reading paper {paper_id}: {e}")
        return ""


@mcp.tool()
async def search_semantic(query: str, year: Optional[str] = None, max_results: int = 10) -> List[Dict]:
    """Search academic papers from Semantic Scholar.

    Args:
        query: Search query string (e.g., 'machine learning').
        year: Optional year filter (e.g., '2019', '2016-2020', '2010-', '-2015').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    kwargs = {}
    if year is not None:
        kwargs['year'] = year
    papers = await async_search(semantic_searcher, query, max_results, **kwargs)
    return papers if papers else []


@mcp.tool()
async def download_semantic(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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
        save_path: Directory to save the PDF (uses shared volume by default).
    Returns:
        Path to the downloaded PDF file.
    """
    os.makedirs(save_path, exist_ok=True)
    return semantic_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_semantic_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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
        save_path: Directory where the PDF is/will be saved (uses shared volume by default).
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
    filter: Optional[str] = None,
    sort: Optional[str] = None,
    order: Optional[str] = None,
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
    extra = {k: v for k, v in {'filter': filter, 'sort': sort, 'order': order}.items() if v is not None}
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
async def download_crossref(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Attempt to download PDF of a CrossRef paper.

    Args:
        paper_id: CrossRef DOI (e.g., '10.1038/nature12373').
        save_path: Directory to save the PDF (uses shared volume by default).
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
    use_scihub: bool = True,
    scihub_base_url: str = "https://sci-hub.se",
) -> str:
    """Try source-native download, OA repositories, Unpaywall, then optional Sci-Hub.

    Args:
        source: Source name (arxiv, biorxiv, medrxiv, iacr, semantic, crossref, pubmed, pmc, core, europepmc, citeseerx, doaj, base, zenodo, hal, ssrn).
        paper_id: Source-native paper identifier.
        doi: Optional DOI used for repository/unpaywall/Sci-Hub fallback.
        title: Optional title used for repository/Sci-Hub fallback when DOI is unavailable.
        save_path: Directory to save downloaded files.
        use_scihub: Whether to fallback to Sci-Hub after OA attempts fail.
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

    repository_result, repository_error = await _try_repository_fallback(doi, title, save_path)
    if repository_result:
        return repository_result
    if repository_error:
        attempt_errors.append(f"repositories: {repository_error}")

    normalized_doi = (doi or "").strip()
    if normalized_doi:
        unpaywall_url = await asyncio.to_thread(unpaywall_resolver.resolve_best_pdf_url, normalized_doi)
        if unpaywall_url:
            unpaywall_result = await _download_from_url(unpaywall_url, save_path, f"unpaywall_{normalized_doi}")
            if unpaywall_result:
                return unpaywall_result
            attempt_errors.append("unpaywall: resolved OA URL but download failed")
        else:
            attempt_errors.append("unpaywall: no OA URL found (or PAPER_SEARCH_MCP_UNPAYWALL_EMAIL/UNPAYWALL_EMAIL missing)")
    else:
        attempt_errors.append("unpaywall: DOI not provided")

    if not use_scihub:
        return "Download failed after OA fallback chain. Details: " + " | ".join(attempt_errors)

    fallback_identifier = (doi or "").strip() or (title or "").strip() or paper_id
    fetcher = SciHubFetcher(base_url=scihub_base_url, output_dir=save_path)
    fallback_result = await asyncio.to_thread(fetcher.download_pdf, fallback_identifier)
    if fallback_result:
        return fallback_result

    return "Download failed after OA fallback chain and Sci-Hub fallback. Details: " + " | ".join(attempt_errors)


@mcp.tool()
async def read_crossref_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Attempt to read and extract text content from a CrossRef paper.

    Args:
        paper_id: CrossRef DOI (e.g., '10.1038/nature12373').
        save_path: Directory where the PDF is/will be saved (uses shared volume by default).
    Returns:
        str: Message indicating that direct paper reading is not supported.
        
    Note:
        CrossRef is a citation database and doesn't provide direct paper content.
        Use the DOI to access the paper through the publisher's website.
    """
    return crossref_searcher.read_paper(paper_id, save_path)


_SEARCHER_MAP = {
    "arxiv": arxiv_searcher,
    "pubmed": pubmed_searcher,
    "biorxiv": biorxiv_searcher,
    "medrxiv": medrxiv_searcher,
    "iacr": iacr_searcher,
    "semantic": semantic_searcher,
    "crossref": crossref_searcher,
}


@mcp.tool()
async def ingest_paper(
    paper_id: str,
    source: str,
    projekt_id: str,
    save_path: str = DEFAULT_DOWNLOAD_PATH,
) -> Dict:
    """Download and ingest a paper into the RAG vector store, linked to a project.

    Args:
        paper_id: Platform-specific paper ID (e.g., arXiv '2106.12345', DOI, PMID).
        source: Platform name: 'arxiv', 'pubmed', 'biorxiv', 'medrxiv', 'iacr', 'semantic', 'crossref'.
        projekt_id: UUID of the review project to associate this paper with.
        save_path: Directory where the PDF is/will be saved (default: './downloads').
    Returns:
        Dict with 'status' key indicating 'queued' or an error message.
    """
    if not LARAVEL_INGEST_URL or not LARAVEL_MCP_TOKEN:
        return {"error": "LARAVEL_INGEST_URL or LARAVEL_MCP_TOKEN not configured"}

    searcher = _SEARCHER_MAP.get(source)
    if searcher is None:
        return {"error": f"Unknown source '{source}'. Valid: {list(_SEARCHER_MAP.keys())}"}

    try:
        text = searcher.read_paper(paper_id, save_path)
    except Exception as e:
        return {"error": f"Failed to read paper: {e}"}

    try:
        papers = searcher.search(paper_id, max_results=1)
        title = papers[0].title if papers else paper_id
    except Exception:
        title = paper_id

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            LARAVEL_INGEST_URL,
            json={
                "paper_id": paper_id,
                "source": source,
                "title": title,
                "text": text,
                "projekt_id": projekt_id,
            },
            headers={"Authorization": f"Bearer {LARAVEL_MCP_TOKEN}"},
        )

    if response.is_error:
        return {"error": f"Ingest endpoint returned {response.status_code}: {response.text}"}

    return response.json()


@mcp.tool()
async def search_rag_papers(
    query: str,
    projekt_id: Optional[str] = None,
    max_results: int = 5,
) -> List[Dict]:
    """Semantic search over ingested papers in the RAG vector store.

    Args:
        query: Natural language query (e.g., 'CRISPR off-target effects').
        projekt_id: Optional UUID to restrict search to a specific review project.
                    Omit for a global search across all ingested papers.
        max_results: Number of chunks to return (default: 5, max: 50).
    Returns:
        List of matching chunks with 'paper_id', 'title', 'source', 'chunk_index',
        'text_chunk', 'similarity', and 'metadata'.
    """
    if not LARAVEL_INGEST_URL or not LARAVEL_MCP_TOKEN:
        return [{"error": "LARAVEL_INGEST_URL or LARAVEL_MCP_TOKEN not configured"}]

    base_url = LARAVEL_INGEST_URL.rsplit("/ingest", 1)[0]
    params: Dict = {"q": query, "max_results": max_results}
    if projekt_id:
        params["projekt_id"] = projekt_id

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{base_url}/rag-search",
            params=params,
            headers={"Authorization": f"Bearer {LARAVEL_MCP_TOKEN}"},
        )

    if response.is_error:
        return [{"error": f"Search endpoint returned {response.status_code}: {response.text}"}]

    return response.json()


def create_app() -> Starlette:
    """Create a Starlette ASGI app with Bearer auth and SSE transport."""
    import json as _json

    # Keep the endpoint configurable so reverse proxies can avoid path collisions.
    message_path = MCP_MESSAGES_PATH
    if not message_path.startswith("/"):
        message_path = f"/{message_path}"
    if not message_path.endswith("/"):
        message_path = f"{message_path}/"

    sse = SseServerTransport(message_path)

    async def handle_sse(request: Request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp._mcp_server.run(
                streams[0],
                streams[1],
                mcp._mcp_server.create_initialization_options(),
            )

    async def handle_rest_search(request: Request):
        """Plain REST endpoint — callable from PHP without MCP protocol.

        POST /search   body: {"query": "...", "sources": [...], "max_results_per_source": 5}
        GET  /search   params: query=...&sources=pubmed,arxiv&max_results_per_source=5
        """
        try:
            if request.method == "POST":
                body = await request.body()
                params = _json.loads(body) if body else {}
            else:
                params = dict(request.query_params)

            query = str(params.get("query", "")).strip()
            if not query:
                return Response(
                    _json.dumps({"error": "query parameter required"}),
                    media_type="application/json",
                    status_code=400,
                )

            raw_sources = params.get("sources", "pubmed,arxiv,semantic")
            if isinstance(raw_sources, list):
                raw_sources = ",".join(raw_sources)

            max_results = int(params.get("max_results_per_source", 5))
            year = params.get("year") or None

            results = await search_papers(
                query=query,
                max_results_per_source=max_results,
                sources=raw_sources,
                year=year,
            )

            return Response(
                _json.dumps(results, default=str, ensure_ascii=False),
                media_type="application/json",
            )
        except Exception as exc:
            logging.exception("REST /search error")
            return Response(
                _json.dumps({"error": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/search", endpoint=handle_rest_search, methods=["GET", "POST"]),
            Mount(message_path, app=sse.handle_post_message),
        ],
    )

    return BearerAuthMiddleware(app, BEARER_TOKEN)


def main():
    import sys

    transport = os.getenv("MCP_TRANSPORT", "sse")
    if len(sys.argv) > 1 and sys.argv[1] == "stdio":
        transport = "stdio"

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        host = os.getenv("PAPERSEARCH_HOST", "0.0.0.0")
        port = int(os.getenv("PAPERSEARCH_PORT", "8089"))
        uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()

