"""Test-side helpers that run the same assertions on MCP SDK 1.x and 2.x."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from paper_search_mcp.mcp_compat import MCP_V2, http_settings

if MCP_V2:
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
else:
    from mcp.client.streamable_http import streamablehttp_client


@asynccontextmanager
async def streamable_http_connection(url: str, headers: dict[str, str] | None = None):
    """Open a Streamable HTTP client transport that sends ``headers``."""
    if MCP_V2:
        async with create_mcp_http_client(headers=headers) as http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                yield streams
    else:
        async with streamablehttp_client(url, headers=headers) as streams:
            yield streams


def is_error(result: Any) -> bool:
    """``CallToolResult.is_error`` (``isError`` before SDK 2.0)."""
    return bool(result.is_error if MCP_V2 else result.isError)


def server_name(initialize_result: Any) -> str:
    """Server name from an ``InitializeResult`` on either SDK."""
    info = initialize_result.server_info if MCP_V2 else initialize_result.serverInfo
    return info.name


def isolate_http_transport_state(monkeypatch, server: Any) -> None:
    """Let a test configure HTTP transport options and build a fresh
    Streamable HTTP session manager without leaking into other tests."""
    if MCP_V2:
        monkeypatch.setattr(server, "_paper_search_http_settings", http_settings(server), raising=False)
        monkeypatch.setattr(server._lowlevel_server, "_session_manager", None)
    else:
        monkeypatch.setattr(server, "settings", server.settings.model_copy(deep=True))
        monkeypatch.setattr(server, "_session_manager", None)


def annotation(tool: Any, wire_name: str) -> Any:
    """Read a tool annotation by its MCP wire name, e.g. ``readOnlyHint``.

    SDK 2.x exposes snake_case attributes and deprecates the camelCase ones.
    """
    if tool.annotations is None:
        return None
    return tool.annotations.model_dump(by_alias=True).get(wire_name)
