# Copilot Instructions for paper-search-mcp
WICHTIG: VERWENDE IMMER DAS RICHTIGE PYTHON ENVIROMENT!!! NICHT DAS SYSTEM PYTHON!!!
## Project Overview
MCP (Model Context Protocol) server providing tools to search and download academic papers from multiple platforms. Built with `fastmcp` and designed for integration with LLMs like Claude Desktop.

## Architecture

### Core Components
- **[paper_search_mcp/server.py](../paper_search_mcp/server.py)** - FastMCP server defining all `@mcp.tool()` endpoints
- **[paper_search_mcp/paper.py](../paper_search_mcp/paper.py)** - `Paper` dataclass standardizing output across all platforms
- **[paper_search_mcp/academic_platforms/](../paper_search_mcp/academic_platforms/)** - Platform-specific searchers (one file per platform)

### Data Flow
1. MCP client calls a tool (e.g., `search_arxiv`)
2. Tool uses platform-specific searcher class (e.g., `ArxivSearcher`)
3. Searcher returns `List[Paper]` objects
4. Tool converts via `paper.to_dict()` for JSON serialization

## Adding New Academic Platforms

Follow this pattern when adding a new platform:

1. **Create searcher** in `paper_search_mcp/academic_platforms/{platform}.py`:
```python
from typing import List
from ..paper import Paper

class PaperSource:
    """Abstract base class - copy this into each file"""
    def search(self, query: str, **kwargs) -> List[Paper]: raise NotImplementedError
    def download_pdf(self, paper_id: str, save_path: str) -> str: raise NotImplementedError
    def read_paper(self, paper_id: str, save_path: str) -> str: raise NotImplementedError

class NewPlatformSearcher(PaperSource):
    def search(self, query: str, max_results: int = 10) -> List[Paper]:
        # Return List[Paper] - all fields must be populated (use empty strings/lists for missing)
        pass
```

2. **Register in server.py**:
   - Import the searcher
   - Instantiate at module level
   - Add `@mcp.tool()` decorated async functions for search/download/read

3. **Create test** in `tests/test_{platform}.py` following existing patterns

## Key Conventions

### Paper Dataclass Requirements
All searchers must return `Paper` objects with these required fields:
- `paper_id`, `title`, `authors`, `abstract`, `doi`, `published_date`, `pdf_url`, `url`, `source`

Use empty string/list for unavailable fields, not `None` for required fields.

### Tool Naming Pattern
- `search_{platform}(query, max_results)` - Search papers
- `download_{platform}(paper_id, save_path)` - Download PDF
- `read_{platform}_paper(paper_id, save_path)` - Extract text from PDF

### HTTP Clients
- Searchers use `requests` internally (synchronous)
- Server wraps calls in `async_search()` helper with `httpx.AsyncClient`

## Development Commands

```bash
# Install in editable mode
uv pip install -e .

# Run tests
pytest tests/

# Run specific platform test
pytest tests/test_arxiv.py -v

# Run MCP server directly
python -m paper_search_mcp.server

# Lint
flake8 paper_search_mcp/
```

## Environment Variables
- `SEMANTIC_SCHOLAR_API_KEY` - Optional API key for enhanced Semantic Scholar rate limits

## Files to Ignore
- `hub.py` and `sci_hub.py` - Commented out/empty (Sci-Hub integration disabled)

---

## Planned: HTTP/SSE Transport Integration

Reference: [.github/workflows/todo.md](../workflows/todo.md)

### Overview
Extending the stdio-based MCP server with HTTP REST API and Server-Sent Events (SSE) for integration with n8n, webhooks, and IoT clients.

### New Files to Create
- `paper_search_mcp/server_http.py` - HTTP/SSE server (based on FastMCP `mcp.http_app()`)
- `start_server.sh` - Startup script for both modes (http/stdio)

### New Dependencies (to add to pyproject.toml)
```toml
"uvicorn>=0.24.0",
"starlette>=0.32.0",
"fastapi>=0.104.0",
```

### New Environment Variables
- `PAPER_SEARCH_HOST` (default: "0.0.0.0")
- `PAPER_SEARCH_PORT` (default: 8090)
- `PAPER_SEARCH_DEBUG` (default: False)

### HTTP Endpoints Pattern
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check with platform status |
| `/api/platforms` | GET | List available search platforms |
| `/api/search/{platform}` | POST/GET | Paper search |
| `/api/download/{platform}` | POST | PDF download |
| `/api/paper/{paper_id}` | GET | Paper details |
| `/sse` | GET | Server-Sent Events stream |
| `/mcp` | POST | MCP JSON-RPC over HTTP |

### SSE Event Types
- `connected`, `heartbeat`, `search_started`, `search_result`, `search_completed`
- `download_started`, `download_progress`, `download_completed`, `error`

### Implementation Phases
1. **Phase 1-2 (HIGH)**: Infrastructure + REST endpoints (~10-13h)
2. **Phase 3-4 (MEDIUM)**: SSE + MCP HTTP transport (~9-11h)
3. **Phase 5-7 (MEDIUM/LOW)**: Config, testing, docs (~13-18h)

### Key Requirement
Both transport modes (stdio + HTTP) must work in parallel - no breaking changes to existing APIs.
