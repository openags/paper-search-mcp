"""HTTP/stdio runner for the paper-search MCP server.

Exposes the existing FastMCP instance (with all @mcp.tool() registrations from
``server.py``) either over streamable-http (protected by a static API key) or
over stdio (for local clients like Claude Desktop).

Usage:
    paper-search-mcp                       # http on 127.0.0.1:8000 (needs API_KEY)
    paper-search-mcp --transport stdio     # stdio (JSON-RPC on stdout)
    paper-search-mcp --host 0.0.0.0 --port 8000

Env vars (all optional except API_KEY when transport=http):
    MCP_TRANSPORT    http|stdio (default http)
    HOST             bind host (default 127.0.0.1; use 0.0.0.0 in containers)
    PORT             bind port (default 8000)
    API_KEY          required for http (checked as Bearer token or x-api-key)
    ALLOWED_ORIGINS  CORS origins, comma-separated (default *)
    LOG_LEVEL        DEBUG|INFO|WARNING|ERROR (default INFO)
"""
from __future__ import annotations

import argparse
import contextlib
import hmac
import logging
import os
import sys

import uvicorn
from starlette.applications import Starlette
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    SimpleUser,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

# Reuse the FastMCP instance that already has every tool registered via @mcp.tool().
from .server import mcp

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    """Root logging to stderr. stdout stays reserved for JSON-RPC in stdio mode."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


class APIKeyBackend(AuthenticationBackend):
    """Validate ``Authorization: Bearer <key>`` or ``x-api-key: <key>``."""

    def __init__(self, expected_key: str) -> None:
        self._expected = expected_key

    async def authenticate(self, conn):
        token: str | None = None
        auth = conn.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        else:
            xk = conn.headers.get("x-api-key")
            if xk:
                token = xk.strip()
        if not token or not hmac.compare_digest(token, self._expected):
            raise AuthenticationError("invalid or missing api key")
        return AuthCredentials(), SimpleUser("api-client")


def _on_auth_error(conn: HTTPConnection, exc: AuthenticationError) -> Response:
    # NOTE: Starlette's AuthenticationMiddleware calls on_error *synchronously*
    # (it does `response = self.on_error(conn, exc)` then `await response(...)`),
    # so this callback must NOT be async. Returning a Response instance is enough.
    return JSONResponse(
        status_code=401,
        content={
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "unauthorized"},
            "id": None,
        },
        headers={"WWW-Authenticate": 'Bearer realm="mcp"'},
    )


def build_http_app(host: str, port: int, allowed_origins: list[str]) -> Starlette:
    """Build the root ASGI app: CORS (outermost) + public /health + authed /mcp."""
    api_key = os.environ.get("API_KEY")
    if not api_key:
        raise RuntimeError("API_KEY env var is required for http transport")

    # host/port are owned by FastMCP settings and consumed when building the app.
    mcp.settings.host = host
    mcp.settings.port = port

    # 1) FastMCP inner Starlette app (mounts /mcp). Calling this also lazily
    #    creates mcp.session_manager, which we MUST start in the parent lifespan
    #    (Mount does not propagate the sub-app lifespan in Starlette).
    mcp_subapp = mcp.streamable_http_app()

    # 2) Wrap with API-key auth so every /mcp request requires a valid key.
    authed_mcp = AuthenticationMiddleware(
        mcp_subapp, backend=APIKeyBackend(api_key), on_error=_on_auth_error
    )

    # 3) Parent lifespan: run the MCP session manager.
    @contextlib.asynccontextmanager
    async def lifespan(parent_app: Starlette):
        async with mcp.session_manager.run():
            yield

    async def health(_request: Request) -> Response:
        return JSONResponse({"ok": True})

    # 4) Root app. /health is declared BEFORE the Mount so it wins and stays public.
    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/", app=authed_mcp),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=allowed_origins,
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=[
                    "Authorization",
                    "x-api-key",
                    "Content-Type",
                    "Accept",
                    "Mcp-Session-Id",
                ],
                expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
            ),
        ],
        lifespan=lifespan,
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="paper-search MCP server")
    parser.add_argument(
        "--transport",
        choices=["http", "stdio"],
        default=os.environ.get("MCP_TRANSPORT", "http"),
    )
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    parser.add_argument("--origins", default=os.environ.get("ALLOWED_ORIGINS", "*"))
    args = parser.parse_args()

    configure_logging(args.log_level)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    if not os.environ.get("API_KEY"):
        print("FATAL: API_KEY env var required for http transport", file=sys.stderr)
        sys.exit(1)

    origins = [o.strip() for o in args.origins.split(",") if o.strip()]
    app = build_http_app(args.host, args.port, origins)
    logger.info("Starting paper-search MCP (http) on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
