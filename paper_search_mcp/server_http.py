# paper_search_mcp/server_http.py
"""
HTTP/SSE Transport Server for Paper Search MCP

This module provides HTTP REST API and Server-Sent Events (SSE) transport
for the paper-search-mcp server, enabling integration with n8n, webhooks,
and IoT clients.

Environment Variables:
    PAPER_SEARCH_HOST: Host to bind to (default: "0.0.0.0")
    PAPER_SEARCH_PORT: Port to bind to (default: 8090)
    PAPER_SEARCH_DEBUG: Enable debug mode (default: False)
"""

import os
import json
import asyncio
import uuid
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, Mount, Mount
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, Response
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from pathlib import Path

import httpx

# Import searchers from main server
from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher
from .paper import Paper

# Configuration from environment variables
HOST = os.getenv("PAPER_SEARCH_HOST", "0.0.0.0")
PORT = int(os.getenv("PAPER_SEARCH_PORT", "8090"))
DEBUG = os.getenv("PAPER_SEARCH_DEBUG", "false").lower() in ("true", "1", "yes")

# Downloads directory for PDFs
DOWNLOADS_DIR = Path(os.getenv("PAPER_SEARCH_DOWNLOADS", "./downloads")).resolve()
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Server version
VERSION = "0.1.3"

# Start time for uptime calculation
START_TIME = time.time()

# Initialize searchers
searchers = {
    "arxiv": ArxivSearcher(),
    "pubmed": PubMedSearcher(),
    "biorxiv": BioRxivSearcher(),
    "medrxiv": MedRxivSearcher(),
    "google_scholar": GoogleScholarSearcher(),
    "iacr": IACRSearcher(),
    "semantic": SemanticSearcher(),
    "crossref": CrossRefSearcher(),
}

# Platform metadata
PLATFORMS = {
    "arxiv": {
        "name": "arxiv",
        "description": "arXiv.org preprints - Open access archive for scholarly articles",
        "supports_download": True,
        "supports_read": True,
    },
    "pubmed": {
        "name": "pubmed",
        "description": "PubMed - Biomedical literature database from NCBI",
        "supports_download": False,
        "supports_read": False,
    },
    "biorxiv": {
        "name": "biorxiv",
        "description": "bioRxiv - Preprint server for biology",
        "supports_download": True,
        "supports_read": True,
    },
    "medrxiv": {
        "name": "medrxiv",
        "description": "medRxiv - Preprint server for health sciences",
        "supports_download": True,
        "supports_read": True,
    },
    "google_scholar": {
        "name": "google_scholar",
        "description": "Google Scholar - Academic search engine",
        "supports_download": False,
        "supports_read": False,
    },
    "iacr": {
        "name": "iacr",
        "description": "IACR ePrint Archive - Cryptology preprints",
        "supports_download": True,
        "supports_read": True,
    },
    "semantic": {
        "name": "semantic",
        "description": "Semantic Scholar - AI-powered academic search engine",
        "supports_download": True,
        "supports_read": True,
    },
    "crossref": {
        "name": "crossref",
        "description": "CrossRef - DOI registration and metadata database",
        "supports_download": False,
        "supports_read": False,
    },
}

# Available MCP tools mapping
MCP_TOOLS = {
    "search_arxiv": {"searcher": "arxiv", "type": "search"},
    "search_pubmed": {"searcher": "pubmed", "type": "search"},
    "search_biorxiv": {"searcher": "biorxiv", "type": "search"},
    "search_medrxiv": {"searcher": "medrxiv", "type": "search"},
    "search_google_scholar": {"searcher": "google_scholar", "type": "search"},
    "search_iacr": {"searcher": "iacr", "type": "search"},
    "search_semantic": {"searcher": "semantic", "type": "search"},
    "search_crossref": {"searcher": "crossref", "type": "search"},
    "download_arxiv": {"searcher": "arxiv", "type": "download"},
    "download_biorxiv": {"searcher": "biorxiv", "type": "download"},
    "download_medrxiv": {"searcher": "medrxiv", "type": "download"},
    "download_iacr": {"searcher": "iacr", "type": "download"},
    "download_semantic": {"searcher": "semantic", "type": "download"},
    "read_arxiv_paper": {"searcher": "arxiv", "type": "read"},
    "read_biorxiv_paper": {"searcher": "biorxiv", "type": "read"},
    "read_medrxiv_paper": {"searcher": "medrxiv", "type": "read"},
    "read_iacr_paper": {"searcher": "iacr", "type": "read"},
    "read_semantic_paper": {"searcher": "semantic", "type": "read"},
    "get_crossref_paper_by_doi": {"searcher": "crossref", "type": "get_by_doi"},
}


# ============================================================================
# SSE Client Management
# ============================================================================

@dataclass
class SSEClient:
    """Represents a connected SSE client"""
    client_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)


class SSEManager:
    """Manages SSE client connections and message broadcasting"""
    
    def __init__(self):
        self.clients: Dict[str, SSEClient] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start background tasks"""
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop(self):
        """Stop background tasks"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
    
    def register_client(self) -> SSEClient:
        """Register a new SSE client"""
        client_id = str(uuid.uuid4())
        client = SSEClient(client_id=client_id)
        self.clients[client_id] = client
        return client
    
    def unregister_client(self, client_id: str):
        """Unregister an SSE client"""
        if client_id in self.clients:
            del self.clients[client_id]
    
    async def send_event(self, client_id: str, event_type: str, data: Any):
        """Send an event to a specific client"""
        if client_id in self.clients:
            event = {
                "type": event_type,
                "data": data,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
            await self.clients[client_id].queue.put(event)
    
    async def broadcast(self, event_type: str, data: Any):
        """Broadcast an event to all connected clients"""
        for client_id in list(self.clients.keys()):
            await self.send_event(client_id, event_type, data)
    
    async def _heartbeat_loop(self):
        """Send heartbeat to all clients every 30 seconds"""
        while True:
            await asyncio.sleep(30)
            for client_id in list(self.clients.keys()):
                await self.send_event(client_id, "heartbeat", {"status": "alive"})
                if client_id in self.clients:
                    self.clients[client_id].last_heartbeat = time.time()
    
    async def _cleanup_loop(self):
        """Clean up disconnected clients (timeout after 90 seconds)"""
        while True:
            await asyncio.sleep(30)
            current_time = time.time()
            timeout = 90  # 3 missed heartbeats
            for client_id in list(self.clients.keys()):
                if current_time - self.clients[client_id].last_heartbeat > timeout:
                    self.unregister_client(client_id)


# Global SSE manager
sse_manager = SSEManager()


# ============================================================================
# MCP Session Management
# ============================================================================

@dataclass
class MCPSession:
    """Represents an MCP session over HTTP"""
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    request_counter: int = 0


class MCPSessionManager:
    """Manages MCP sessions for HTTP transport"""
    
    def __init__(self):
        self.sessions: Dict[str, MCPSession] = {}
    
    def create_session(self) -> MCPSession:
        """Create a new MCP session"""
        session_id = str(uuid.uuid4())
        session = MCPSession(session_id=session_id)
        self.sessions[session_id] = session
        return session
    
    def get_session(self, session_id: str) -> Optional[MCPSession]:
        """Get an existing session"""
        session = self.sessions.get(session_id)
        if session:
            session.last_activity = time.time()
        return session
    
    def remove_session(self, session_id: str):
        """Remove a session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
    
    def cleanup_stale_sessions(self, timeout: int = 3600):
        """Remove sessions older than timeout seconds"""
        current_time = time.time()
        for session_id in list(self.sessions.keys()):
            if current_time - self.sessions[session_id].last_activity > timeout:
                self.remove_session(session_id)


# Global MCP session manager
mcp_session_manager = MCPSessionManager()


# ============================================================================
# Error Handling
# ============================================================================

def create_error_response(
    code: str,
    message: str,
    details: Optional[Dict] = None,
    status_code: int = 400
) -> JSONResponse:
    """Create a standardized error response"""
    error_body = {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    }
    return JSONResponse(error_body, status_code=status_code)


# ============================================================================
# Helper Functions
# ============================================================================

async def run_search(
    searcher,
    query: str,
    max_results: int = 10,
    **kwargs
) -> List[Dict]:
    """Run a search asynchronously"""
    try:
        if 'year' in kwargs and kwargs['year'] is not None:
            papers = searcher.search(query, year=kwargs['year'], max_results=max_results)
        elif 'fetch_details' in kwargs:
            papers = searcher.search(query, max_results, kwargs.get('fetch_details', True))
        else:
            papers = searcher.search(query, max_results=max_results)
        return [paper.to_dict() for paper in papers]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# HTTP Endpoints - Health & System
# ============================================================================

async def health_check(request: Request) -> JSONResponse:
    """
    GET /health - Health Check endpoint
    
    Returns server health status, version, uptime, and available platforms.
    """
    uptime = int(time.time() - START_TIME)
    return JSONResponse({
        "status": "healthy",
        "version": VERSION,
        "uptime": uptime,
        "uptime_human": f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s",
        "platforms": list(PLATFORMS.keys()),
        "sse_clients": len(sse_manager.clients),
        "mcp_sessions": len(mcp_session_manager.sessions),
    })


async def get_platforms(request: Request) -> JSONResponse:
    """
    GET /api/platforms - List available search platforms
    
    Returns detailed information about each supported academic platform.
    """
    return JSONResponse({
        "platforms": list(PLATFORMS.values())
    })


# ============================================================================
# HTTP Endpoints - Search
# ============================================================================

async def search_papers(request: Request) -> JSONResponse:
    """
    POST /api/search/{platform} - Search papers on a specific platform
    GET /api/search/{platform}?q={query}&max_results={n} - Alternative GET method
    
    Request body (POST):
    {
        "query": "machine learning",
        "max_results": 10,
        "filters": {
            "year": "2020",
            "fetch_details": true
        }
    }
    """
    platform = request.path_params.get("platform")
    
    if platform not in searchers:
        return create_error_response(
            "INVALID_PLATFORM",
            f"Platform '{platform}' is not supported",
            {"available_platforms": list(PLATFORMS.keys())},
            status_code=404
        )
    
    # Handle both GET and POST
    if request.method == "GET":
        query = request.query_params.get("q", "")
        max_results = int(request.query_params.get("max_results", 10))
        year = request.query_params.get("year")
        fetch_details = request.query_params.get("fetch_details", "true").lower() == "true"
    else:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return create_error_response(
                "INVALID_JSON",
                "Request body must be valid JSON",
                status_code=400
            )
        query = body.get("query", "")
        max_results = body.get("max_results", 10)
        filters = body.get("filters", {})
        year = filters.get("year")
        fetch_details = filters.get("fetch_details", True)
    
    if not query:
        return create_error_response(
            "MISSING_QUERY",
            "Search query is required",
            status_code=400
        )
    
    start_time = time.time()
    
    # Broadcast search started event
    await sse_manager.broadcast("search_started", {
        "platform": platform,
        "query": query,
        "max_results": max_results
    })
    
    try:
        searcher = searchers[platform]
        kwargs = {}
        if year:
            kwargs['year'] = year
        if platform == "iacr":
            kwargs['fetch_details'] = fetch_details
        
        results = await run_search(searcher, query, max_results, **kwargs)
        search_time_ms = int((time.time() - start_time) * 1000)
        
        # Broadcast search completed event
        await sse_manager.broadcast("search_completed", {
            "platform": platform,
            "query": query,
            "results_count": len(results),
            "search_time_ms": search_time_ms
        })
        
        return JSONResponse({
            "results": results,
            "total_found": len(results),
            "search_time_ms": search_time_ms,
            "platform": platform,
            "query": query
        })
        
    except Exception as e:
        await sse_manager.broadcast("error", {
            "platform": platform,
            "query": query,
            "error": str(e)
        })
        return create_error_response(
            "SEARCH_FAILED",
            f"Search failed: {str(e)}",
            {"platform": platform, "query": query},
            status_code=500
        )


# ============================================================================
# HTTP Endpoints - Download
# ============================================================================

async def download_paper(request: Request) -> JSONResponse:
    """
    POST /api/download/{platform} - Download a paper PDF
    
    Request body:
    {
        "paper_id": "2301.12345",
        "save_path": "/tmp/papers/"
    }
    """
    platform = request.path_params.get("platform")
    
    if platform not in searchers:
        return create_error_response(
            "INVALID_PLATFORM",
            f"Platform '{platform}' is not supported",
            {"available_platforms": list(PLATFORMS.keys())},
            status_code=404
        )
    
    if not PLATFORMS[platform]["supports_download"]:
        return create_error_response(
            "DOWNLOAD_NOT_SUPPORTED",
            f"Platform '{platform}' does not support direct PDF downloads",
            status_code=400
        )
    
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return create_error_response(
            "INVALID_JSON",
            "Request body must be valid JSON",
            status_code=400
        )
    
    paper_id = body.get("paper_id")
    save_path = body.get("save_path", "./downloads")
    
    if not paper_id:
        return create_error_response(
            "MISSING_PAPER_ID",
            "Paper ID is required",
            status_code=400
        )
    
    start_time = time.time()
    
    # Broadcast download started event
    await sse_manager.broadcast("download_started", {
        "platform": platform,
        "paper_id": paper_id
    })
    
    try:
        searcher = searchers[platform]
        # Use configured downloads directory for URL-accessible storage
        actual_save_path = str(DOWNLOADS_DIR) if save_path == "./downloads" else save_path
        file_path = searcher.download_pdf(paper_id, actual_save_path)
        download_time_ms = int((time.time() - start_time) * 1000)
        
        # Get file size if possible
        file_size = 0
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
        
        # Generate download URL if file is in the downloads directory
        download_url = None
        if actual_save_path == str(DOWNLOADS_DIR) and os.path.exists(file_path):
            filename = Path(file_path).name
            download_url = f"http://{HOST}:{PORT}/downloads/{filename}"
        
        # Broadcast download completed event
        await sse_manager.broadcast("download_completed", {
            "platform": platform,
            "paper_id": paper_id,
            "file_path": file_path,
            "download_url": download_url,
            "file_size_bytes": file_size,
            "download_time_ms": download_time_ms
        })
        
        response_data = {
            "success": True,
            "file_path": file_path,
            "file_size_bytes": file_size,
            "download_time_ms": download_time_ms,
            "platform": platform,
            "paper_id": paper_id
        }
        if download_url:
            response_data["download_url"] = download_url
        
        return JSONResponse(response_data)
        
    except NotImplementedError as e:
        return create_error_response(
            "DOWNLOAD_NOT_SUPPORTED",
            str(e),
            status_code=400
        )
    except Exception as e:
        await sse_manager.broadcast("error", {
            "platform": platform,
            "paper_id": paper_id,
            "error": str(e)
        })
        return create_error_response(
            "DOWNLOAD_FAILED",
            f"Download failed: {str(e)}",
            {"platform": platform, "paper_id": paper_id},
            status_code=500
        )


# ============================================================================
# HTTP Endpoints - Paper Details
# ============================================================================

async def get_paper_details(request: Request) -> JSONResponse:
    """
    GET /api/paper/{platform}/{paper_id} - Get paper details
    """
    platform = request.path_params.get("platform")
    paper_id = request.path_params.get("paper_id")
    
    if platform not in searchers:
        return create_error_response(
            "INVALID_PLATFORM",
            f"Platform '{platform}' is not supported",
            status_code=404
        )
    
    # For CrossRef, we can get paper by DOI directly
    if platform == "crossref":
        try:
            paper = searchers["crossref"].get_paper_by_doi(paper_id)
            if paper:
                return JSONResponse({"paper": paper.to_dict()})
            else:
                return create_error_response(
                    "PAPER_NOT_FOUND",
                    f"Paper with DOI '{paper_id}' not found",
                    status_code=404
                )
        except Exception as e:
            return create_error_response(
                "FETCH_FAILED",
                f"Failed to fetch paper: {str(e)}",
                status_code=500
            )
    
    # For other platforms, search by paper_id
    try:
        results = await run_search(searchers[platform], paper_id, max_results=1)
        if results:
            return JSONResponse({"paper": results[0]})
        else:
            return create_error_response(
                "PAPER_NOT_FOUND",
                f"Paper '{paper_id}' not found on {platform}",
                status_code=404
            )
    except Exception as e:
        return create_error_response(
            "FETCH_FAILED",
            f"Failed to fetch paper: {str(e)}",
            status_code=500
        )


async def read_paper_content(request: Request) -> JSONResponse:
    """
    POST /api/read/{platform} - Read and extract text from a paper PDF
    
    Request body:
    {
        "paper_id": "2301.12345",
        "save_path": "/tmp/papers/"
    }
    """
    platform = request.path_params.get("platform")
    
    if platform not in searchers:
        return create_error_response(
            "INVALID_PLATFORM",
            f"Platform '{platform}' is not supported",
            status_code=404
        )
    
    if not PLATFORMS[platform]["supports_read"]:
        return create_error_response(
            "READ_NOT_SUPPORTED",
            f"Platform '{platform}' does not support paper reading",
            status_code=400
        )
    
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return create_error_response(
            "INVALID_JSON",
            "Request body must be valid JSON",
            status_code=400
        )
    
    paper_id = body.get("paper_id")
    save_path = body.get("save_path", "./downloads")
    
    if not paper_id:
        return create_error_response(
            "MISSING_PAPER_ID",
            "Paper ID is required",
            status_code=400
        )
    
    try:
        searcher = searchers[platform]
        content = searcher.read_paper(paper_id, save_path)
        
        return JSONResponse({
            "success": True,
            "paper_id": paper_id,
            "platform": platform,
            "content": content,
            "content_length": len(content)
        })
        
    except Exception as e:
        return create_error_response(
            "READ_FAILED",
            f"Failed to read paper: {str(e)}",
            {"platform": platform, "paper_id": paper_id},
            status_code=500
        )


# ============================================================================
# SSE Endpoint for MCP Transport
# ============================================================================

# Store for SSE client sessions with their message queues
sse_sessions: Dict[str, asyncio.Queue] = {}


async def sse_endpoint(request: Request) -> StreamingResponse:
    """
    GET /sse - Server-Sent Events endpoint for MCP SSE Transport
    
    This implements the MCP SSE transport protocol:
    1. Client connects via GET /sse
    2. Server sends 'endpoint' event with the messages URL
    3. Client sends MCP requests via POST to the messages URL
    4. Server sends responses via SSE
    
    Event types:
    - endpoint: Contains the URL for posting messages
    - message: MCP JSON-RPC responses
    """
    session_id = str(uuid.uuid4())
    message_queue: asyncio.Queue = asyncio.Queue()
    sse_sessions[session_id] = message_queue
    
    # Build the messages endpoint URL
    # Use the host from the request to construct the URL
    host = request.headers.get("host", f"{HOST}:{PORT}")
    scheme = request.headers.get("x-forwarded-proto", "http")
    messages_url = f"{scheme}://{host}/sse/messages?sessionId={session_id}"
    
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # First, send the endpoint event as per MCP SSE spec
            yield f"event: endpoint\ndata: {messages_url}\n\n"
            
            # Then wait for and forward messages
            while True:
                try:
                    # Wait for messages with timeout for keepalive
                    message = await asyncio.wait_for(
                        message_queue.get(),
                        timeout=30.0
                    )
                    yield f"event: message\ndata: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    # Send comment as keepalive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            # Cleanup session
            if session_id in sse_sessions:
                del sse_sessions[session_id]
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


async def sse_messages_endpoint(request: Request) -> Response:
    """
    POST /sse/messages - Endpoint for receiving MCP messages via SSE transport
    
    This handles incoming MCP JSON-RPC requests from clients using SSE transport.
    The sessionId query parameter links the request to the SSE connection.
    """
    session_id = request.query_params.get("sessionId")
    
    if not session_id or session_id not in sse_sessions:
        return JSONResponse(
            {"error": "Invalid or expired session"},
            status_code=400
        )
    
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400
        )
    
    # Process the MCP request and get response
    response = await process_mcp_request(body)
    
    # Send response via SSE
    message_queue = sse_sessions[session_id]
    await message_queue.put(response)
    
    # Return accepted status
    return Response(status_code=202)


async def process_mcp_request(body: dict) -> dict:
    """Process an MCP JSON-RPC request and return the response"""
    
    # Validate JSON-RPC structure
    if body.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32600, "message": "Invalid Request: jsonrpc must be '2.0'"}
        }
    
    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")
    
    # Handle different MCP methods
    if method == "initialize":
        session = mcp_session_manager.create_session()
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "paper_search_server",
                    "version": VERSION
                }
            }
        }
    
    elif method == "notifications/initialized":
        # Client notification that initialization is complete
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {}
        }
    
    elif method == "tools/list":
        tools = []
        for tool_name, tool_info in MCP_TOOLS.items():
            tool_def = {
                "name": tool_name,
                "description": get_tool_description(tool_name, tool_info),
                "inputSchema": get_tool_input_schema(tool_info)
            }
            tools.append(tool_def)
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": tools}
        }
    
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if tool_name not in MCP_TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }
        
        try:
            result = await execute_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(e)}
            }
    
    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {}
        }
    
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }


def get_tool_description(tool_name: str, tool_info: dict) -> str:
    """Generate a description for a tool"""
    descriptions = {
        "search_arxiv": "Search academic papers from arXiv preprint repository",
        "search_pubmed": "Search biomedical literature from PubMed database",
        "search_biorxiv": "Search biology preprints from bioRxiv",
        "search_medrxiv": "Search health sciences preprints from medRxiv",
        "search_google_scholar": "Search academic papers via Google Scholar",
        "search_iacr": "Search cryptography papers from IACR ePrint Archive",
        "search_semantic": "Search papers using Semantic Scholar AI-powered search",
        "search_crossref": "Search papers in CrossRef DOI database",
        "download_arxiv": "Download PDF from arXiv",
        "download_biorxiv": "Download PDF from bioRxiv",
        "download_medrxiv": "Download PDF from medRxiv",
        "download_iacr": "Download PDF from IACR ePrint",
        "download_semantic": "Download PDF via Semantic Scholar",
        "read_arxiv_paper": "Extract text content from arXiv paper",
        "read_biorxiv_paper": "Extract text content from bioRxiv paper",
        "read_medrxiv_paper": "Extract text content from medRxiv paper",
        "read_iacr_paper": "Extract text content from IACR paper",
        "read_semantic_paper": "Extract text content from Semantic Scholar paper",
        "get_crossref_paper_by_doi": "Get paper metadata from CrossRef by DOI",
    }
    return descriptions.get(tool_name, f"{tool_info['type'].capitalize()} papers from {tool_info['searcher']}")


def get_tool_input_schema(tool_info: dict) -> dict:
    """Generate input schema for a tool - n8n compatible format"""
    if tool_info['type'] == 'search':
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query string (e.g., 'machine learning', 'covid-19 treatment')"
                },
                "max_results": {
                    "type": "string",
                    "description": "Maximum number of results to return (default: 10)",
                    "default": "10"
                }
            },
            "required": ["query"]
        }
    elif tool_info['type'] in ('download', 'read'):
        return {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "string",
                    "description": "Paper identifier (e.g., arXiv ID '2301.12345', DOI '10.1234/example')"
                },
                "save_path": {
                    "type": "string",
                    "description": "Directory to save the PDF (default: ./downloads)",
                    "default": "./downloads"
                }
            },
            "required": ["paper_id"]
        }
    elif tool_info['type'] == 'get_by_doi':
        return {
            "type": "object",
            "properties": {
                "doi": {
                    "type": "string",
                    "description": "Digital Object Identifier (e.g., '10.1038/nature12373')"
                }
            },
            "required": ["doi"]
        }
    return {
        "type": "object",
        "properties": {}
    }


async def execute_tool(tool_name: str, arguments: dict) -> dict:
    """Execute an MCP tool and return the result"""
    tool_info = MCP_TOOLS[tool_name]
    searcher = searchers[tool_info["searcher"]]
    
    if tool_info["type"] == "search":
        query = str(arguments.get("query", ""))
        # Handle max_results - convert string to int if needed
        max_results_raw = arguments.get("max_results", 10)
        try:
            max_results = int(max_results_raw) if max_results_raw else 10
        except (ValueError, TypeError):
            max_results = 10
        year = arguments.get("year")
        
        kwargs = {}
        if year:
            kwargs['year'] = str(year)
        if tool_info["searcher"] == "iacr":
            fetch_details = arguments.get("fetch_details", True)
            if isinstance(fetch_details, str):
                fetch_details = fetch_details.lower() in ('true', '1', 'yes')
            kwargs['fetch_details'] = fetch_details
        
        results = await run_search(searcher, query, max_results, **kwargs)
        return {"content": [{"type": "text", "text": json.dumps(results, indent=2)}]}
        
    elif tool_info["type"] == "download":
        paper_id = str(arguments.get("paper_id", ""))
        # Always use configured downloads directory for URL generation
        try:
            file_path = searcher.download_pdf(paper_id, str(DOWNLOADS_DIR))
            # Check if download was successful (file_path should be a valid path, not an error message)
            if not file_path or "Failed" in file_path or "Error" in file_path or not os.path.exists(file_path):
                return {"content": [{"type": "text", "text": json.dumps({"error": file_path or "Download failed"}, indent=2)}]}
            # Generate download URL
            filename = Path(file_path).name
            download_url = f"http://{HOST}:{PORT}/downloads/{filename}"
            return {"content": [{"type": "text", "text": json.dumps({"file_path": file_path, "download_url": download_url}, indent=2)}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, indent=2)}]}
        
    elif tool_info["type"] == "read":
        paper_id = str(arguments.get("paper_id", ""))
        save_path = str(arguments.get("save_path", "./downloads"))
        content = searcher.read_paper(paper_id, save_path)
        return {"content": [{"type": "text", "text": content}]}
        
    elif tool_info["type"] == "get_by_doi":
        doi = str(arguments.get("doi", ""))
        paper = searcher.get_paper_by_doi(doi)
        return {"content": [{"type": "text", "text": json.dumps(paper.to_dict() if paper else {}, indent=2)}]}
    
    return {"content": [{"type": "text", "text": "Unknown tool type"}]}


async def mcp_endpoint(request: Request) -> JSONResponse:
    """
    POST /mcp - MCP JSON-RPC over HTTP endpoint (direct HTTP, not SSE)
    
    Handles MCP protocol messages over direct HTTP transport.
    For SSE transport, use GET /sse + POST /sse/messages
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"}
        }, status_code=400)
    
    response = await process_mcp_request(body)
    return JSONResponse(response)


async def mcp_messages(request: Request) -> JSONResponse:
    """
    POST /messages - Alternative MCP messages endpoint
    
    Same as /mcp but at /messages path for compatibility.
    """
    return await mcp_endpoint(request)


# ============================================================================
# Application Setup
# ============================================================================

@asynccontextmanager
async def lifespan(app: Starlette):
    """Application lifespan handler"""
    # Startup
    await sse_manager.start()
    print(f"Paper Search MCP HTTP Server starting on {HOST}:{PORT}")
    yield
    # Shutdown
    await sse_manager.stop()
    print("Paper Search MCP HTTP Server shutting down")


# Define routes
routes = [
    # Health & System
    Route("/health", health_check, methods=["GET"]),
    Route("/api/platforms", get_platforms, methods=["GET"]),
    
    # Search
    Route("/api/search/{platform}", search_papers, methods=["GET", "POST"]),
    
    # Download
    Route("/api/download/{platform}", download_paper, methods=["POST"]),
    
    # Read paper content
    Route("/api/read/{platform}", read_paper_content, methods=["POST"]),
    
    # Paper details
    Route("/api/paper/{platform}/{paper_id:path}", get_paper_details, methods=["GET"]),
    
    # SSE MCP Transport (for n8n and other SSE clients)
    Route("/sse", sse_endpoint, methods=["GET"]),
    Route("/sse/messages", sse_messages_endpoint, methods=["POST"]),
    
    # Direct MCP HTTP Transport
    Route("/mcp", mcp_endpoint, methods=["POST"]),
    Route("/messages", mcp_messages, methods=["POST"]),
    
    # Static files for downloaded PDFs
    Mount("/downloads", StaticFiles(directory=str(DOWNLOADS_DIR)), name="downloads"),
]

# CORS middleware configuration
middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
]

# Create Starlette application
app = Starlette(
    debug=DEBUG,
    routes=routes,
    middleware=middleware,
    lifespan=lifespan,
)


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point for running the HTTP server"""
    import uvicorn
    uvicorn.run(
        "paper_search_mcp.server_http:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
    )


if __name__ == "__main__":
    main()
