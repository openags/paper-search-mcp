# Multi-stage build for smaller image
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY paper_search_mcp/ paper_search_mcp/

RUN pip install --no-cache-dir build \
    && python -m build --wheel \
    && pip install --no-cache-dir dist/*.whl

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/paper-search-mcp /usr/local/bin/paper-search-mcp

# Environment variables — set at runtime via docker-compose, not here
ENV PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=""
ENV PAPER_SEARCH_MCP_CORE_API_KEY=""
ENV PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY=""
ENV PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN=""
ENV PAPER_SEARCH_MCP_DOAJ_API_KEY=""
ENV PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL=""
ENV PAPER_SEARCH_MCP_IEEE_API_KEY=""
ENV PAPER_SEARCH_MCP_ACM_API_KEY=""

# Expose the MCP server port
EXPOSE 8089

# Command to run the MCP server with SSE transport and Bearer auth
CMD ["python", "-m", "paper_search_mcp.server"]
