# paper_search_mcp/server.py
import os
import hmac
from typing import List, Dict, Optional

import httpx
import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher

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

# Instances of searchers
arxiv_searcher = ArxivSearcher()
pubmed_searcher = PubMedSearcher()
biorxiv_searcher = BioRxivSearcher()
medrxiv_searcher = MedRxivSearcher()
google_scholar_searcher = GoogleScholarSearcher()
iacr_searcher = IACRSearcher()
semantic_searcher = SemanticSearcher()
crossref_searcher = CrossRefSearcher()
# scihub_searcher = SciHubSearcher()


# Asynchronous helper to adapt synchronous searchers
async def async_search(searcher, query: str, max_results: int, **kwargs) -> List[Dict]:
    async with httpx.AsyncClient() as client:
        # Assuming searchers use requests internally; we'll call synchronously for now
        if 'year' in kwargs:
            papers = searcher.search(query, year=kwargs['year'], max_results=max_results)
        else:
            papers = searcher.search(query, max_results=max_results)
        return [paper.to_dict() for paper in papers]


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
async def download_arxiv(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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
async def download_pubmed(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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
async def download_biorxiv(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Download PDF of a bioRxiv paper.

    Args:
        paper_id: bioRxiv DOI.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """
    return biorxiv_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def download_medrxiv(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Download PDF of a medRxiv paper.

    Args:
        paper_id: medRxiv DOI.
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """
    return medrxiv_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def download_iacr(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
    """Download PDF of an IACR ePrint paper.

    Args:
        paper_id: IACR paper ID (e.g., '2009/101').
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """
    return iacr_searcher.download_pdf(paper_id, save_path)


@mcp.tool()
async def read_arxiv_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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
async def read_medrxiv_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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
async def read_iacr_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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
        save_path: Directory to save the PDF (default: './downloads').
    Returns:
        Path to the downloaded PDF file.
    """ 
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
async def download_crossref(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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
async def read_crossref_paper(paper_id: str, save_path: str = DEFAULT_DOWNLOAD_PATH) -> str:
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

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount(message_path, app=sse.handle_post_message),
        ],
    )

    return BearerAuthMiddleware(app, BEARER_TOKEN)


if __name__ == "__main__":
    import anyio

    async def main():
        mcp._setup_handlers()
        app = create_app()
        config = uvicorn.Config(app, host="0.0.0.0", port=8089)
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(main)
