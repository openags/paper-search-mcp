# paper_search_mcp/server.py
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from PyPDF2 import PdfReader

from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher

from .academic_platforms.sci_hub import SciHubFetcher
from .paper import Paper

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    logger.warning("Invalid boolean value for %s=%r; using default=%s", name, raw, default)
    return default


def _normalize_transport(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    mapping = {
        "stdio": "stdio",
        "sse": "sse",
        "streamable-http": "streamable-http",
        "streamable_http": "streamable-http",
        "http": "streamable-http",
    }
    if normalized in mapping:
        return mapping[normalized]
    logger.warning("Unknown PAPER_SEARCH_MCP_TRANSPORT '%s', defaulting to stdio", value)
    return None


def _determine_transport() -> str:
    env_transport = _normalize_transport(os.environ.get("PAPER_SEARCH_MCP_TRANSPORT"))
    if env_transport:
        return env_transport
    if os.environ.get("PORT"):
        return "streamable-http"
    return "stdio"


SELECTED_TRANSPORT = _determine_transport()


def _determine_host(transport: str) -> str:
    if explicit_host := os.environ.get("PAPER_SEARCH_MCP_HOST"):
        return explicit_host
    if explicit_host := os.environ.get("HOST"):
        return explicit_host
    if transport == "streamable-http":
        return "0.0.0.0"
    return "127.0.0.1"


def _determine_port(transport: str) -> int:
    candidates = [os.environ.get("PAPER_SEARCH_MCP_PORT"), os.environ.get("PORT")]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return int(candidate)
        except ValueError:
            logger.warning("Invalid port value '%s', falling back to default", candidate)
    return 8081 if transport == "streamable-http" else 8000


# Initialize MCP server with environment-aware networking defaults
mcp = FastMCP(
    "paper_search_server",
    host=_determine_host(SELECTED_TRANSPORT),
    port=_determine_port(SELECTED_TRANSPORT),
)

DOWNLOAD_DIR = os.environ.get("PAPER_SEARCH_DOWNLOAD_DIR", "./downloads")
DISABLE_SCIHUB = _env_flag("PAPER_SEARCH_MCP_DISABLE_SCIHUB", False)
DEFAULT_MAX_RESULTS = 15
RESULTS_PER_SOURCE = 5
MAX_CACHE_SIZE = 128


SEARCHERS: Dict[str, Any] = {
    "arxiv": ArxivSearcher(),
    "pubmed": PubMedSearcher(),
    "biorxiv": BioRxivSearcher(),
    "medrxiv": MedRxivSearcher(),
    "google_scholar": GoogleScholarSearcher(),
    "iacr": IACRSearcher(),
    "semantic": SemanticSearcher(),
    "crossref": CrossRefSearcher(),
}

# Instances of searchers (retained for legacy tools)
arxiv_searcher = SEARCHERS["arxiv"]
pubmed_searcher = SEARCHERS["pubmed"]
biorxiv_searcher = SEARCHERS["biorxiv"]
medrxiv_searcher = SEARCHERS["medrxiv"]
google_scholar_searcher = SEARCHERS["google_scholar"]
iacr_searcher = SEARCHERS["iacr"]
semantic_searcher = SEARCHERS["semantic"]
crossref_searcher = SEARCHERS["crossref"]
scihub_searcher = None if DISABLE_SCIHUB else SciHubFetcher()


# Cached metadata for fetch requests
SEARCH_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


# Asynchronous helper to adapt synchronous searchers
async def async_search(searcher, query: str, max_results: int, **kwargs) -> List[Dict]:
    if "year" in kwargs:
        papers = await asyncio.to_thread(
            searcher.search, query, max_results=max_results, year=kwargs["year"]
        )
    else:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results)
    return [paper.to_dict() for paper in papers]


def _split_semicolon_values(value: str | None) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(";") if item.strip()]


def _build_document_id(source: str, paper: Dict[str, Any]) -> str:
    candidate_keys = [
        paper.get("paper_id"),
        paper.get("doi"),
        paper.get("url"),
        paper.get("pdf_url"),
        paper.get("title"),
    ]
    for key in candidate_keys:
        if key:
            return f"{source}:{key}"
    digest = hashlib.sha1(json.dumps(paper, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{source}:{digest[:12]}"


def _update_cache(document_id: str, metadata: Dict[str, Any]) -> None:
    if document_id in SEARCH_CACHE:
        SEARCH_CACHE.move_to_end(document_id)
    SEARCH_CACHE[document_id] = metadata
    while len(SEARCH_CACHE) > MAX_CACHE_SIZE:
        SEARCH_CACHE.popitem(last=False)


def _prepare_metadata(source: str, paper: Dict[str, Any]) -> Dict[str, Any]:
    authors = _split_semicolon_values(paper.get("authors"))
    categories = _split_semicolon_values(paper.get("categories"))
    keywords = _split_semicolon_values(paper.get("keywords"))
    references = _split_semicolon_values(paper.get("references"))
    metadata = {
        "source": source,
        "paper_id": paper.get("paper_id", ""),
        "title": paper.get("title", "").strip() or "Untitled document",
        "abstract": paper.get("abstract", ""),
        "doi": paper.get("doi", ""),
        "url": paper.get("url", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "published_date": paper.get("published_date", ""),
        "updated_date": paper.get("updated_date", ""),
        "authors": authors,
        "categories": categories,
        "keywords": keywords,
        "citations": paper.get("citations"),
        "references": references,
        "extra": paper.get("extra", ""),
    }
    return metadata


def _format_search_result(source: str, paper: Dict[str, Any]) -> Dict[str, Any]:
    document_id = _build_document_id(source, paper)
    metadata = _prepare_metadata(source, paper)
    _update_cache(document_id, metadata)
    url = metadata.get("url") or metadata.get("pdf_url") or ""
    return {
        "id": document_id,
        "title": metadata["title"],
        "url": url,
    }


async def _search_source(source: str, searcher: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    try:
        papers = await async_search(searcher, query, limit)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Search failed for source %s: %s", source, exc)
        return []

    results: List[Dict[str, Any]] = []
    for paper in papers:
        results.append(_format_search_result(source, paper))
    return results


async def _get_document_text(source: str, paper_id: str) -> str:
    searcher = SEARCHERS.get(source)
    if not searcher:
        return ""

    if not hasattr(searcher, "read_paper"):
        return ""

    try:
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        return await asyncio.to_thread(searcher.read_paper, paper_id, DOWNLOAD_DIR)
    except NotImplementedError:
        return ""
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Failed to read paper %s from %s: %s", paper_id, source, exc)
        return ""


def _empty_search_response() -> CallToolResult:
    payload = {"results": []}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


def _build_fetch_response(document_id: str, metadata: Dict[str, Any], text: str) -> CallToolResult:
    url = metadata.get("url") or metadata.get("pdf_url") or ""
    payload = {
        "id": document_id,
        "title": metadata.get("title", document_id),
        "text": text,
        "url": url,
        "metadata": metadata,
    }
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


@mcp.tool("search")
async def search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> CallToolResult:
    """Deep Research compatible search tool aggregating across sources."""

    if not query or not query.strip():
        return _empty_search_response()

    limit = max(1, min(RESULTS_PER_SOURCE, max_results))
    tasks = [
        _search_source(source, searcher, query, limit)
        for source, searcher in SEARCHERS.items()
    ]

    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    aggregated: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for group in gathered:
        if isinstance(group, Exception):  # pragma: no cover - defensive logging
            logger.warning("Unhandled search exception: %s", group)
            continue
        for item in group:
            if item["id"] in seen:
                continue
            aggregated.append(item)
            seen.add(item["id"])
            if len(aggregated) >= max_results:
                break
        if len(aggregated) >= max_results:
            break

    payload = {"results": aggregated}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


@mcp.tool("fetch")
async def fetch(id: str | None = None, document_id: str | None = None) -> CallToolResult:
    """Fetch full document content for a search result."""

    identifier = document_id or id

    if not identifier:
        raise ValueError("Document identifier is required")

    if document_id and id and document_id != id:
        raise ValueError("Conflicting identifiers provided")

    metadata = SEARCH_CACHE.get(identifier)
    if metadata is None:
        source, _, paper_id = identifier.partition(":")
        metadata = {
            "source": source,
            "paper_id": paper_id,
            "title": paper_id or identifier,
            "abstract": "",
            "doi": "",
            "url": "",
            "pdf_url": "",
            "published_date": "",
            "updated_date": "",
            "authors": [],
            "categories": [],
            "keywords": [],
            "citations": None,
            "references": [],
            "extra": "",
        }
    source = metadata.get("source", "")
    paper_id = metadata.get("paper_id", "")
    full_text = ""
    if source and paper_id:
        full_text = await _get_document_text(source, paper_id)

    if not full_text:
        full_text = metadata.get("abstract", "") or "Content unavailable."

    return _build_fetch_response(identifier, metadata, full_text)


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

    Args:
        query: Search query string (e.g., 'machine learning').
        max_results: Maximum number of papers to return (default: 10).
    Returns:
        List of paper metadata in dictionary format.
    """
    papers = await async_search(biorxiv_searcher, query, max_results)
    return papers if papers else []


@mcp.tool()
async def search_medrxiv(query: str, max_results: int = 10) -> List[Dict]:
    """Search academic papers from medRxiv.

    Args:
        query: Search query string (e.g., 'machine learning').
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
    async with httpx.AsyncClient() as client:
        papers = iacr_searcher.search(query, max_results, fetch_details)
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
    async with httpx.AsyncClient() as client:
        return arxiv_searcher.download_pdf(paper_id, save_path)


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


if not DISABLE_SCIHUB:
    @mcp.tool()
    async def download_scihub(identifier: str, save_path: str = "./downloads") -> str:
        """Download a PDF via Sci-Hub using DOI, PMID, URL, or direct PDF URL.

        Args:
            identifier: DOI, PMID, article URL, or direct PDF URL.
            save_path: Directory to save the PDF (default: './downloads').
        Returns:
            Path to the downloaded PDF file, or an error message on failure.
        """
        try:
            fetcher = SciHubFetcher(output_dir=save_path)
            result = fetcher.download_pdf(identifier)
            return result if result else "Failed to download PDF from Sci-Hub."
        except Exception as e:
            return f"Failed to download PDF from Sci-Hub: {e}"


    @mcp.tool()
    async def search_scihub(identifier: str, max_results: int = 1) -> List[Dict]:
        """Resolve a Sci-Hub identifier into a downloadable PDF result.

        Args:
            identifier: DOI, PMID, article URL, or direct PDF URL.
            max_results: Maximum number of results to return (default: 1).
        Returns:
            List containing a single resolved result if found, otherwise empty list.
        """
        if not identifier or not identifier.strip() or max_results < 1:
            return []

        try:
            fetcher = SciHubFetcher(output_dir=DOWNLOAD_DIR)
            pdf_url = fetcher._get_direct_url(identifier.strip())
            if not pdf_url:
                return []
            return [{
                "paper_id": identifier.strip(),
                "title": f"Sci-Hub result for {identifier.strip()}",
                "authors": "",
                "abstract": "",
                "doi": "",
                "published_date": "",
                "pdf_url": pdf_url,
                "url": pdf_url,
                "source": "scihub",
                "updated_date": "",
                "categories": "",
                "keywords": "",
                "citations": 0,
                "references": "",
                "extra": "",
            }]
        except Exception:
            return []


    @mcp.tool()
    async def read_scihub_paper(identifier: str, save_path: str = "./downloads") -> str:
        """Download and extract text from a paper via Sci-Hub.

        Args:
            identifier: DOI, PMID, article URL, or direct PDF URL.
            save_path: Directory where the PDF is/will be saved (default: './downloads').
        Returns:
            Extracted paper text, or an error message if unavailable.
        """
        try:
            fetcher = SciHubFetcher(output_dir=save_path)
            pdf_path = fetcher.download_pdf(identifier)
            if not pdf_path:
                return "Failed to download PDF from Sci-Hub."

            reader = PdfReader(pdf_path)
            text_parts: List[str] = []
            for page in reader.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
            text = "\n".join(text_parts).strip()
            return text or "No extractable text found in PDF."
        except Exception as e:
            return f"Failed to read paper from Sci-Hub: {e}"


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
async def search_crossref(query: str, max_results: int = 10, **kwargs) -> List[Dict]:
    """Search academic papers from CrossRef database.
    
    CrossRef is a scholarly infrastructure organization that provides 
    persistent identifiers (DOIs) for scholarly content and metadata.
    It's one of the largest citation databases covering millions of 
    academic papers, journals, books, and other scholarly content.

    Args:
        query: Search query string (e.g., 'machine learning', 'climate change').
        max_results: Maximum number of papers to return (default: 10, max: 1000).
        **kwargs: Additional search parameters:
            - filter: CrossRef filter string (e.g., 'has-full-text:true,from-pub-date:2020')
            - sort: Sort field ('relevance', 'published', 'updated', 'deposited', etc.)
            - order: Sort order ('asc' or 'desc')
    Returns:
        List of paper metadata in dictionary format.
        
    Examples:
        # Basic search
        search_crossref("deep learning", 20)
        
        # Search with filters
        search_crossref("climate change", 10, filter="from-pub-date:2020,has-full-text:true")
        
        # Search sorted by publication date
        search_crossref("neural networks", 15, sort="published", order="desc")
    """
    papers = await async_search(crossref_searcher, query, max_results, **kwargs)
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
    async with httpx.AsyncClient() as client:
        paper = crossref_searcher.get_paper_by_doi(doi)
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


def main() -> None:
    logger.info(
        "Starting paper_search_mcp server using %s transport on %s:%s",
        SELECTED_TRANSPORT,
        mcp.settings.host,
        mcp.settings.port,
    )
    mcp.run(transport=SELECTED_TRANSPORT)


if __name__ == "__main__":
    main()
