"""DNS-rebinding protection must not depend on which MCP SDK is installed.

The server is constructed with the default loopback host, so MCP SDK 1.x keeps
a loopback-only Host/Origin policy even when ``--host`` is later set to
``0.0.0.0``. SDK 2.x would disable validation for a non-loopback bind host
unless a policy is passed explicitly; ``mcp_compat`` must pass the same one.
"""
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from paper_search_mcp.mcp_compat import (
    MCP_V2,
    FastMCP,
    build_http_app,
    configure_http,
    http_settings,
    run_transport,
)

INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "rebinding-test", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream"}
# The SDK answers 421 and then raises "Request validation failed"; only the
# HTTP status matters here, so the rejected client does not re-raise it.


def _server(host):
    server = FastMCP("rebinding-test")
    configure_http(server, host=host, json_response=True)
    return server


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "::"])
def test_default_policy_is_loopback_only_for_any_bind_host(host):
    security = http_settings(_server(host)).transport_security
    assert security is not None
    assert security.enable_dns_rebinding_protection
    assert set(security.allowed_hosts) == {"127.0.0.1:*", "localhost:*", "[::1]:*"}


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_streamable_http_rejects_foreign_host_and_accepts_loopback(host):
    app = build_http_app(_server(host), "streamable-http")
    # One client: the Streamable HTTP session manager can only start once per app.
    with TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as client:
        foreign = {**HEADERS, "Host": "attacker.example:8000"}
        assert client.post("/mcp", headers=foreign, json=INITIALIZE).status_code == 421
        response = client.post("/mcp", headers=HEADERS, json=INITIALIZE)
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "rebinding-test"


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_sse_app_receives_the_policy(host):
    # An accepted SSE stream never ends, so a live request would hang instead
    # of failing on a regression; check the policy handed to the SDK instead.
    # The live 421 behavior is covered by the Streamable HTTP test above.
    server = _server(host)
    if MCP_V2:
        with patch.object(server, "sse_app") as sse_app:
            build_http_app(server, "sse")
        security = sse_app.call_args.kwargs["transport_security"]
    else:
        build_http_app(server, "sse")
        security = server.settings.transport_security
    assert security.enable_dns_rebinding_protection
    assert "localhost:*" in security.allowed_hosts


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
def test_run_transport_applies_the_policy_on_a_wildcard_bind(transport):
    server = _server("0.0.0.0")
    with patch.object(server, "run") as run:
        run_transport(server, transport)
    run.assert_called_once()
    kwargs = run.call_args.kwargs
    assert kwargs["transport"] == transport
    if MCP_V2:
        # SDK 2.x takes transport options as run() keyword arguments.
        security = kwargs["transport_security"]
    else:
        # SDK 1.x reads them from server.settings.
        security = server.settings.transport_security
    assert security.enable_dns_rebinding_protection
    assert "localhost:*" in security.allowed_hosts
