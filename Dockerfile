# syntax=docker/dockerfile:1.7
# Multi-stage build for paper-search-mcp.
# Default CMD runs the HTTP server (streamable-http) protected by an API key.
# Override with `--transport stdio` for local MCP clients (Claude Desktop / Cursor).

# ───────── Stage 1: build the wheel ─────────
FROM python:3.12-slim AS builder

WORKDIR /app

# build tooling only (stays in this stage)
RUN pip install --no-cache-dir build

COPY pyproject.toml README.md LICENSE ./
COPY paper_search_mcp/ ./paper_search_mcp/

RUN python -m build --wheel

# ───────── Stage 2: runtime ─────────
FROM python:3.12-slim AS runner

WORKDIR /app

# Install the wheel built in stage 1 (pulls only runtime deps; all are wheels).
COPY --from=builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl \
 && rm -f /tmp/*.whl \
 && useradd --create-home --uid 1000 appuser

# Runtime defaults (override with `docker run -e ...` or `--env-file`).
# API_KEY is REQUIRED for http transport; the server refuses to start if missing.
ENV MCP_TRANSPORT=http \
    HOST=0.0.0.0 \
    PORT=8000 \
    LOG_LEVEL=INFO \
    ALLOWED_ORIGINS=* \
    API_KEY="" \
    PAPER_SEARCH_MCP_UNPAYWALL_EMAIL="" \
    PAPER_SEARCH_MCP_CORE_API_KEY="" \
    PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY="" \
    PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN="" \
    PAPER_SEARCH_MCP_DOAJ_API_KEY="" \
    PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL="" \
    PAPER_SEARCH_MCP_OPENAIRE_API_KEY="" \
    PAPER_SEARCH_MCP_CITESEERX_API_KEY="" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Writable dir for downloaded PDFs (tools default to ./downloads).
RUN mkdir -p /app/downloads && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Healthcheck without curl/wget (slim has neither): pure-stdlib python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
         sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=3).status==200 else 1)"

# Default: HTTP server. For stdio (local clients):
#   docker run -i --rm --init <img> --transport stdio
CMD ["paper-search-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
