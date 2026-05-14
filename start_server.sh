#!/bin/bash
# Paper Search MCP Server Startup Script
# 
# Usage:
#   ./start_server.sh [MODE]
#
# Modes:
#   http  - Start HTTP/SSE server (default)
#   stdio - Start stdio-based MCP server
#
# Environment Variables:
#   PAPER_SEARCH_HOST - Host to bind to (default: 0.0.0.0)
#   PAPER_SEARCH_PORT - Port to bind to (default: 8090)
#   PAPER_SEARCH_DEBUG - Enable debug mode (default: false)
#   SEMANTIC_SCHOLAR_API_KEY - Optional API key for Semantic Scholar

set -e

MODE="${1:-http}"
PORT="${PAPER_SEARCH_PORT:-8090}"
HOST="${PAPER_SEARCH_HOST:-0.0.0.0}"
DEBUG="${PAPER_SEARCH_DEBUG:-false}"

echo "Paper Search MCP Server"
echo "======================"
echo "Mode: $MODE"
echo "Host: $HOST"
echo "Port: $PORT"
echo "Debug: $DEBUG"
echo ""

case "$MODE" in
    http|HTTP)
        echo "Starting HTTP/SSE server..."
        if [ "$DEBUG" = "true" ]; then
            exec uvicorn "paper_search_mcp.server_http:app" --host "$HOST" --port "$PORT" --reload
        else
            exec uvicorn "paper_search_mcp.server_http:app" --host "$HOST" --port "$PORT"
        fi
        ;;
    stdio|STDIO)
        echo "Starting stdio-based MCP server..."
        exec python -m paper_search_mcp.server
        ;;
    *)
        echo "Error: Unknown mode '$MODE'"
        echo "Usage: $0 [http|stdio]"
        exit 1
        ;;
esac
