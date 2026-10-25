"""Compatibility layer for the MCP Python SDK 1.x and 2.x.

MCP SDK 2.0 renamed ``mcp.server.fastmcp.FastMCP`` to
``mcp.server.mcpserver.MCPServer`` and moved HTTP transport options (host, port,
endpoint path, transport security) out of ``server.settings`` into ``run()`` and
the ASGI app factories. Protocol types also switched to snake_case attributes.

Everything version-specific lives here so the rest of the package keeps a single
code path and both SDK generations stay supported:

* SDK 1.x (``mcp>=1.27.2,<2``) with ``fastmcp>=3.4.2,<4``
* SDK 2.x (``mcp>=2.2,<3``) with ``fastmcp>=4.1,<5``
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings

try:  # MCP SDK >= 2.0
    from mcp.server.mcpserver import MCPServer as FastMCP

    MCP_V2 = True
except ImportError:  # MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP

    MCP_V2 = False

__all__ = [
    "MCP_V2",
    "FastMCP",
    "HttpSettings",
    "build_http_app",
    "call_tool_payload",
    "configure_http",
    "http_settings",
    "run_transport",
    "tool_input_schema",
]

# SDK 2.x has no settings fields for HTTP transport options, so the values the
# CLI configured are kept on the server object until run()/app construction.
_HTTP_SETTINGS_ATTR = "_paper_search_http_settings"


@dataclass(frozen=True)
class HttpSettings:
    """HTTP transport options, with the same defaults as SDK 1.x settings."""

    host: str = "127.0.0.1"
    port: int = 8000
    streamable_http_path: str = "/mcp"
    json_response: bool = False
    transport_security: TransportSecuritySettings | None = None


def _loopback_transport_security() -> TransportSecuritySettings:
    """The DNS-rebinding policy SDK 1.x stores for a loopback-constructed server."""
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )


def http_settings(server: FastMCP) -> HttpSettings:
    """Return the HTTP transport options currently configured on ``server``.

    The server is constructed with the default loopback host, so SDK 1.x stores
    a loopback-only DNS-rebinding policy in ``server.settings`` at construction
    and keeps it when ``--host`` later changes (even to ``0.0.0.0``). SDK 2.x
    would only apply that policy for loopback bind hosts and disable Host/Origin
    validation otherwise, so the same policy is made the explicit default here
    to keep both SDKs equally protected.
    """
    if not MCP_V2:
        settings = server.settings
        return HttpSettings(
            host=settings.host,
            port=settings.port,
            streamable_http_path=settings.streamable_http_path,
            json_response=settings.json_response,
            transport_security=settings.transport_security,
        )
    configured = getattr(server, _HTTP_SETTINGS_ATTR, None)
    if configured is None:
        configured = HttpSettings(transport_security=_loopback_transport_security())
    return configured


def configure_http(server: FastMCP, **changes: Any) -> HttpSettings:
    """Update HTTP transport options (``host``, ``port``,
    ``streamable_http_path``, ``json_response``, ``transport_security``)."""
    updated = replace(http_settings(server), **changes)
    if MCP_V2:
        setattr(server, _HTTP_SETTINGS_ATTR, updated)
    else:
        for name, value in changes.items():
            setattr(server.settings, name, value)
    return updated


def run_transport(server: FastMCP, transport: str) -> None:
    """Run ``server`` with the configured HTTP options on either SDK."""
    if transport == "stdio" or not MCP_V2:
        # SDK 1.x reads host/port/path/security from server.settings.
        server.run(transport=transport)
        return
    options = http_settings(server)
    kwargs: dict[str, Any] = {"host": options.host, "port": options.port}
    if transport == "streamable-http":
        kwargs["streamable_http_path"] = options.streamable_http_path
        kwargs["json_response"] = options.json_response
    if options.transport_security is not None:
        kwargs["transport_security"] = options.transport_security
    server.run(transport=transport, **kwargs)


def build_http_app(server: FastMCP, transport: str):
    """Build the SDK's ASGI app for ``sse`` or ``streamable-http``."""
    if not MCP_V2:
        return server.sse_app() if transport == "sse" else server.streamable_http_app()
    options = http_settings(server)
    kwargs: dict[str, Any] = {"host": options.host}
    if options.transport_security is not None:
        kwargs["transport_security"] = options.transport_security
    if transport == "sse":
        return server.sse_app(**kwargs)
    return server.streamable_http_app(
        streamable_http_path=options.streamable_http_path,
        json_response=options.json_response,
        **kwargs,
    )


def tool_input_schema(tool: Any) -> dict[str, Any]:
    """Return a tool's JSON input schema (``inputSchema`` before SDK 2.0)."""
    schema = tool.input_schema if MCP_V2 else tool.inputSchema
    return schema or {}


def call_tool_payload(result: Any) -> tuple[list[Any], dict[str, Any] | None]:
    """Normalize ``server.call_tool()`` output to ``(content, structured)``.

    SDK 2.x returns a ``CallToolResult``. SDK 1.x returns a
    ``(content, structured)`` tuple for tools with an output schema, a dict, or
    a plain sequence of content blocks.
    """
    if hasattr(result, "content") and not isinstance(result, (tuple, list, dict)):
        structured = result.structured_content if MCP_V2 else result.structuredContent
        return list(result.content), structured
    if isinstance(result, tuple):
        content, structured = result
        return list(content), structured
    if isinstance(result, dict):
        return [], result
    return list(result), None
