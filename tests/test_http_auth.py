"""Deterministic OAuth protected-resource fixtures, not live IdP certification."""
import asyncio
import contextlib
import socket
import threading
import time
from unittest.mock import patch

import httpx
import pytest
import uvicorn
from fastmcp.server.auth.providers.jwt import RSAKeyPair
from joserfc import jwt
from joserfc.jwk import RSAKey
from joserfc.jws import JWSRegistry
from joserfc.registry import HeaderParameter
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from paper_search_mcp import http_auth, server
from paper_search_mcp.http_auth import (
    AuthConfigurationError, OAuthConfig, ResourceJWTVerifier,
    create_protected_http_app, load_http_auth_config,
)

ISSUER = "https://issuer.example/"
RESOURCE = "https://papers.example/mcp"
SCOPES = ("papers:read", "papers:download")


@pytest.fixture(autouse=True)
def clean_auth_env(monkeypatch):
    for name in ("AUTH", *http_auth._FIELDS):
        monkeypatch.delenv("PAPER_SEARCH_MCP_" + name, raising=False)
    monkeypatch.setattr(http_auth, "load_env_file", lambda: None)


@pytest.fixture(scope="module")
def keys():
    # Private keys exist in memory for this test process only.
    return RSAKeyPair.generate(), RSAKeyPair.generate()


def configuration(path="/mcp", **overrides):
    values = dict(issuer=ISSUER, jwks_uri=ISSUER + "jwks", audience="https://papers.example" + path,
                  resource_url="https://papers.example" + path, scopes=SCOPES)
    values.update(overrides)
    return OAuthConfig(**values)


def configure_env(monkeypatch, **overrides):
    settings = dict(AUTH="oauth", OAUTH_ISSUER=ISSUER, OAUTH_JWKS_URI=ISSUER + "jwks",
                    OAUTH_AUDIENCE=RESOURCE, OAUTH_RESOURCE_URL=RESOURCE,
                    OAUTH_SCOPES=" ".join(SCOPES))
    settings.update(overrides)
    for key, value in settings.items():
        if value is None:
            monkeypatch.delenv("PAPER_SEARCH_MCP_" + key, raising=False)
        else:
            monkeypatch.setenv("PAPER_SEARCH_MCP_" + key, value)


def token(keys, *, cfg=None, changes=None, remove=(), key_index=0, headers=None):
    cfg = cfg or configuration()
    claims = dict(iss=cfg.issuer, aud=cfg.audience, exp=int(time.time()) + 600,
                  iat=int(time.time()), sub="alice", client_id="test-client", scope=" ".join(cfg.scopes))
    claims.update(changes or {})
    for claim in remove:
        claims.pop(claim, None)
    key = RSAKey.import_key(keys[key_index].private_key.get_secret_value())
    header = {"alg": "RS256", "kid": "key-1", **(headers or {})}
    if header.get("kid") is None:
        header.pop("kid", None)
    registry = JWSRegistry(header_registry={"custom": HeaderParameter("test critical header", lambda value: None)},
                           algorithms=[header["alg"]]) if "custom" in header else None
    return jwt.encode(header, claims, key, algorithms=[header["alg"]], registry=registry)


def jwks(keys, index=0, kid="key-1"):
    key = RSAKey.import_key(keys[index].public_key).as_dict(private=False)
    return {"keys": [{**key, "kid": kid, "use": "sig", "alg": "RS256"}]}


@contextlib.contextmanager
def app_fixture(keys, monkeypatch, transport="streamable-http", cfg=None, sdk=None):
    cfg = cfg or configuration("/sse" if transport == "sse" else "/mcp")
    requests = []
    def respond(request):
        requests.append(request)
        assert str(request.url) == cfg.jwks_uri
        assert "authorization" not in request.headers
        return httpx.Response(200, json=jwks(keys))
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    verifier = ResourceJWTVerifier(cfg, http_client=client)
    with monkeypatch.context() as local:
        local.setattr(http_auth, "ResourceJWTVerifier", lambda config: verifier)
        sdk = sdk or FastMCP("auth-test", json_response=True)
        app = create_protected_http_app(sdk, transport, cfg)
        yield app, cfg, verifier, requests
    asyncio.run(client.aclose())


def test_config_default_is_open_and_complete_oauth_is_opt_in(monkeypatch):
    assert load_http_auth_config(None, "/mcp") is None
    configure_env(monkeypatch)
    assert load_http_auth_config(None, "/mcp") == configuration()
    assert load_http_auth_config("oauth", "/mcp") == configuration()


@pytest.mark.parametrize("changes", [
    {"AUTH": "oath"}, {"AUTH": "none"}, {"OAUTH_ISSUER": None},
    {"OAUTH_JWKS_URI": ""}, {"OAUTH_AUDIENCE": None}, {"OAUTH_RESOURCE_URL": None},
    {"OAUTH_SCOPES": ""}, {"OAUTH_AUDIENCE": "https://another-api.example/"},
    {"OAUTH_ISSUER": "http://issuer.example/"}, {"OAUTH_ISSUER": "https://issuer.example"},
    {"OAUTH_ISSUER": "https://user:password@issuer.example/"},
    {"OAUTH_JWKS_URI": "https://issuer.example/jwks?key=secret"},
    {"OAUTH_JWKS_URI": "https://issuer.example/jwks#fragment"},
    {"OAUTH_RESOURCE_URL": "http://papers.example/mcp"},
    {"OAUTH_RESOURCE_URL": "https://papers.example/other"},
    {"OAUTH_SCOPES": "papers:read\npapers:download"}, {"OAUTH_SCOPES": 'papers:"read'},
    {"OAUTH_SCOPES": "offline_access"}, {"OAUTH_SCOPES": "papers:read  papers:download"},
    {"OAUTH_ALGORITHM": "none"}, {"OAUTH_ALGORITHM": "HS256"}, {"OAUTH_ALGORITHM": "EdDSA"},
])
def test_invalid_config_fails_closed(monkeypatch, changes):
    configure_env(monkeypatch, **changes)
    with pytest.raises(AuthConfigurationError):
        load_http_auth_config(None, "/mcp")


def test_loopback_http_resource_is_allowed_but_not_an_http_issuer(monkeypatch):
    resource = "http://127.0.0.1:8000/mcp"
    configure_env(monkeypatch, OAUTH_AUDIENCE=resource, OAUTH_RESOURCE_URL=resource)
    assert load_http_auth_config(None, "/mcp").resource_url == resource
    configure_env(monkeypatch, OAUTH_ISSUER="http://127.0.0.1:9999/")
    with pytest.raises(AuthConfigurationError):
        load_http_auth_config(None, "/mcp")


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
def test_metadata_is_public_and_challenge_identifies_resource(keys, monkeypatch, transport):
    with app_fixture(keys, monkeypatch, transport) as (app, cfg, _, requests):
        with TestClient(app, base_url="https://papers.example") as client:
            path = "/sse" if transport == "sse" else "/mcp"
            response = client.get(path)
            assert response.status_code == 401
            challenge = response.headers["www-authenticate"]
            assert 'resource_metadata="https://papers.example/.well-known/oauth-protected-resource' + path + '"' in challenge
            assert 'scope="papers:read papers:download"' in challenge
            metadata = client.get("/.well-known/oauth-protected-resource" + path,
                                  headers={"Authorization": "Bearer malformed"})
            assert metadata.status_code == 200
            assert metadata.json() == dict(resource=cfg.resource_url, authorization_servers=[ISSUER],
                                            scopes_supported=list(SCOPES), bearer_methods_supported=["header"],
                                            resource_name="Paper Search MCP")
            assert not requests  # metadata and missing credentials never fetch JWKS
            for endpoint in ("/authorize", "/token", "/register"):
                assert client.get(endpoint).status_code == 401  # no auth server implemented


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
@pytest.mark.parametrize("case", ["missing", "invalid", "expired", "wrong_issuer", "wrong_audience", "bad_signature",
                                      "missing_exp", "missing_sub", "missing_iss", "missing_aud", "future_nbf", "future_iat",
                                      "bad_exp", "infinite_exp", "boolean_nbf", "empty_sub", "wrong_scope", "no_scope",
                                      "query_token", "cookie_token", "different_scheme"])
def test_http_rejects_bad_credentials(keys, monkeypatch, transport, case):
    with app_fixture(keys, monkeypatch, transport) as (app, cfg, _, _):
        changes = {
            "expired": {"exp": int(time.time()) - 10}, "wrong_issuer": {"iss": "https://other.example/"},
            "wrong_audience": {"aud": "https://other.example/mcp"}, "future_nbf": {"nbf": int(time.time()) + 60},
            "future_iat": {"iat": int(time.time()) + 60}, "bad_exp": {"exp": "tomorrow"},
            "infinite_exp": {"exp": float("inf")}, "boolean_nbf": {"nbf": True}, "empty_sub": {"sub": ""},
            "wrong_scope": {"scope": "papers:read"},
        }.get(case, {})
        removed = {"missing_exp": ("exp",), "missing_sub": ("sub",), "missing_iss": ("iss",),
                   "missing_aud": ("aud",), "no_scope": ("scope",)}.get(case, ())
        credential = token(keys, cfg=cfg, changes=changes, remove=removed, key_index=int(case == "bad_signature"))
        headers = {"Authorization": "Bearer " + credential}
        params = {}
        if case in ("missing", "query_token", "cookie_token"):
            headers = {}
        if case == "invalid":
            headers = {"Authorization": "Bearer not-a-jwt"}
        if case == "query_token":
            params = {"access_token": credential}
        if case == "cookie_token":
            headers = {"Cookie": "access_token=" + credential}
        if case == "different_scheme":
            headers = {"Authorization": "Basic " + credential}
        with TestClient(app, base_url="https://papers.example") as client:
            # SSE inbound messages must be guarded too, not only the GET stream.
            endpoints = [("GET", "/sse"), ("POST", "/messages/")] if transport == "sse" else [
                ("GET", "/mcp"), ("POST", "/mcp"), ("DELETE", "/mcp")]
            for method, path in endpoints:
                response = client.request(method, path, headers=headers, params=params)
                status = 403 if case in ("wrong_scope", "no_scope") else 401
                assert response.status_code == status, response.text
                challenge = response.headers["www-authenticate"]
                assert "resource_metadata=" in challenge
                assert 'scope="papers:read papers:download"' in challenge
                if status == 403:
                    assert 'error="insufficient_scope"' in challenge


def test_jwks_rotation_cache_and_outage_fail_closed(keys):
    async def run():
        calls = []
        state = {"keys": jwks(keys)}
        def respond(request):
            calls.append(request)
            if state.get("failure"):
                return httpx.Response(503)
            return httpx.Response(200, json=state["keys"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            verifier = ResourceJWTVerifier(configuration(), http_client=client)
            valid = token(keys)
            verified = await verifier.verify_token(valid)
            assert verified.subject == "alice"
            assert verified.resource == RESOURCE
            assert await verifier.verify_token(valid)
            assert len(calls) == 1
            state["keys"] = jwks(keys, 1, "rotated")
            rotated = token(keys, key_index=1, headers={"kid": "rotated"})
            assert await verifier.verify_token(rotated)
            assert len(calls) == 2
            assert await verifier.verify_token(valid) is None
            state["failure"] = True
            verifier._jwks_cache_time = 0
            assert await verifier.verify_token(rotated) is None
    asyncio.run(run())


def test_scope_array_and_audience_array_are_supported(keys):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=jwks(keys)))) as client:
            verifier = ResourceJWTVerifier(configuration(), http_client=client)
            verified = await verifier.verify_token(token(keys, changes={"aud": [RESOURCE, "other"], "scp": list(SCOPES)}, remove=("scope",)))
            assert verified.scopes == list(SCOPES)
    asyncio.run(run())


def test_protected_mode_rejects_stdio_app(keys):
    with pytest.raises(AuthConfigurationError):
        create_protected_http_app(FastMCP("test"), "stdio", configuration())


def test_http_config_error_never_starts_server(monkeypatch):
    configure_env(monkeypatch, OAUTH_JWKS_URI=None)
    with patch.object(server.mcp, "run") as run, patch("uvicorn.run") as uvicorn_run:
        with pytest.raises(SystemExit):
            server.main(["--transport", "streamable-http"])
        run.assert_not_called()
        uvicorn_run.assert_not_called()


def test_stdio_ignores_incomplete_oauth_settings(monkeypatch):
    configure_env(monkeypatch, AUTH="typo", OAUTH_JWKS_URI=None)
    with patch.object(server.mcp, "run") as run, patch.object(server.threading, "Thread"), \
         patch.object(http_auth, "load_http_auth_config", side_effect=AssertionError("stdio must not read HTTP auth")):
        server.main(["--transport", "stdio"])
        run.assert_called_once_with(transport="stdio")


def test_main_selects_protected_runner_after_validation(monkeypatch):
    configure_env(monkeypatch)
    with patch.object(server.mcp, "run") as run, patch.object(http_auth, "run_protected_http") as protected:
        server.main(["--transport", "streamable-http"])
        run.assert_not_called()
        protected.assert_called_once_with(server.mcp, "streamable-http", configuration())


@contextlib.contextmanager
def listening(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    instance = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(target=instance.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not instance.started:
            assert thread.is_alive() and time.monotonic() < deadline, "ASGI server did not start"
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        instance.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), "ASGI server did not stop"


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
def test_real_http_mcp_initialize_list_and_call(keys, monkeypatch, transport):
    sdk = FastMCP("auth-test")
    @sdk.tool()
    def echo(value: str) -> str:
        return value
    with app_fixture(keys, monkeypatch, transport, sdk=sdk) as (app, cfg, _, _):
        with listening(app) as base:
            async def exercise():
                headers = {"Authorization": "Bearer " + token(keys, cfg=cfg)}
                client = sse_client(base + "/sse", headers=headers) if transport == "sse" else streamablehttp_client(base + "/mcp", headers=headers)
                async with client as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        assert (await session.initialize()).serverInfo.name == "auth-test"
                        assert [tool.name for tool in (await session.list_tools()).tools] == ["echo"]
                        result = await session.call_tool("echo", {"value": "protected success"})
                        assert not result.isError
                        assert result.content[0].text == "protected success"
            asyncio.run(asyncio.wait_for(exercise(), 20))


def test_streamable_http_session_is_bound_to_subject(keys, monkeypatch):
    with app_fixture(keys, monkeypatch) as (app, cfg, _, _):
        with TestClient(app, base_url="https://papers.example") as client:
            headers = {"Authorization": "Bearer " + token(keys, cfg=cfg), "Accept": "application/json, text/event-stream"}
            response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
            assert response.status_code == 200, response.text
            session_id = response.headers["mcp-session-id"]
            headers.update({"Mcp-Session-Id": session_id, "Mcp-Protocol-Version": "2025-11-25"})
            client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            assert client.post("/mcp", headers=headers, json=request).status_code == 200
            headers["Authorization"] = "Bearer " + token(keys, cfg=cfg, changes={"sub": "bob"})
            assert client.post("/mcp", headers=headers, json=request).status_code == 404


def test_public_host_does_not_disable_dns_rebinding_checks(keys, monkeypatch):
    with app_fixture(keys, monkeypatch) as (app, cfg, _, _):
        with TestClient(app, base_url="https://evil.example") as client:
            headers = {"Authorization": "Bearer " + token(keys, cfg=cfg), "Accept": "application/json, text/event-stream"}
            assert client.post("/mcp", headers=headers, json={}).status_code == 421


@pytest.mark.parametrize("claim,value", [
    ("iss", None), ("iss", [ISSUER]), ("iss", {"issuer": ISSUER}),
    ("aud", None), ("aud", [RESOURCE, 123]), ("aud", {"resource": RESOURCE}),
    ("exp", None), ("exp", []), ("exp", {}), ("exp", float("nan")), ("exp", True),
    ("sub", None), ("sub", 123), ("sub", ["alice"]), ("sub", {"user": "alice"}),
    ("scope", None), ("scope", [*SCOPES, 123]), ("scope", {"scope": list(SCOPES)}),
    ("scope", [*SCOPES, ["extra"]]), ("scp", None), ("scp", [123]),
    ("nbf", None), ("nbf", "tomorrow"), ("iat", []), ("client_id", ["client"]),
])
@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
def test_malformed_signed_claims_are_401(keys, monkeypatch, claim, value, transport):
    with app_fixture(keys, monkeypatch, transport) as (app, cfg, _, _):
        credential = token(keys, cfg=cfg, changes={claim: value})
        with TestClient(app, base_url="https://papers.example") as client:
            endpoint = "/sse" if transport == "sse" else "/mcp"
            response = client.get(endpoint, headers={"Authorization": "Bearer " + credential})
            assert response.status_code == 401, response.text


@pytest.mark.parametrize("headers", [{"alg": "RS512"}, {"kid": "missing"}, {"crit": ["custom"], "custom": True}])
def test_algorithm_kid_and_critical_headers_are_enforced(keys, monkeypatch, headers):
    with app_fixture(keys, monkeypatch) as (app, cfg, _, _):
        credential = token(keys, cfg=cfg, headers=headers)
        with TestClient(app, base_url="https://papers.example") as client:
            assert client.get("/mcp", headers={"Authorization": "Bearer " + credential}).status_code == 401


def test_token_key_urls_never_override_configured_jwks(keys):
    async def run():
        seen = []
        def respond(request):
            seen.append(str(request.url))
            return httpx.Response(200, json=jwks(keys))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            verifier = ResourceJWTVerifier(configuration(), http_client=client)
            credential = token(keys, headers={"jku": "https://attacker.example/jwks", "x5u": "https://attacker.example/cert"})
            assert await verifier.verify_token(credential)
            assert seen == [ISSUER + "jwks"]
    asyncio.run(run())


def test_stdio_real_session_with_invalid_http_auth_settings(tmp_path):
    import sys
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "paper_search_mcp.server"], cwd=tmp_path,
            env={"PAPER_SEARCH_MCP_ENV_FILE": str(tmp_path / "absent.env"),
                 "PAPER_SEARCH_MCP_AUTH": "misspelled-oauth",
                 "PAPER_SEARCH_MCP_OAUTH_ISSUER": "not-a-url"},
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                assert (await session.initialize()).serverInfo.name == "paper_search_server"
                assert "search_papers" in {tool.name for tool in (await session.list_tools()).tools}
    asyncio.run(asyncio.wait_for(exercise(), 30))


def test_sse_session_is_bound_to_subject(keys, monkeypatch):
    with app_fixture(keys, monkeypatch, "sse") as (app, cfg, _, _):
        with listening(app) as base:
            async def exercise():
                alice = {"Authorization": "Bearer " + token(keys, cfg=cfg)}
                bob = {"Authorization": "Bearer " + token(keys, cfg=cfg, changes={"sub": "bob"})}
                async with httpx.AsyncClient(timeout=5) as client:
                    async with client.stream("GET", base + "/sse", headers=alice) as response:
                        assert response.status_code == 200
                        lines = response.aiter_lines()
                        async for line in lines:
                            if line.startswith("data: "):
                                endpoint = base + line.removeprefix("data: ")
                                break
                        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                              "clientInfo": {"name": "test", "version": "1"}}}
                        assert (await client.post(endpoint, headers=bob, json=request)).status_code == 404
                        assert (await client.post(endpoint, headers=alice, json=request)).status_code == 202
            asyncio.run(asyncio.wait_for(exercise(), 10))


@pytest.mark.parametrize("jwks_body", [None, [], {}, {"keys": []}, {"keys": [None]}, {"keys": [{"kty": "invalid", "kid": "key-1"}]}])
def test_malformed_jwks_is_rejected(keys, jwks_body):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=jwks_body))) as client:
            verifier = ResourceJWTVerifier(configuration(), http_client=client)
            assert await verifier.verify_token(token(keys)) is None
    asyncio.run(run())


def test_jwks_without_token_kid_requires_one_key_and_past_nbf_is_allowed(keys):
    async def run():
        current = {"body": jwks(keys)}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=current["body"]))) as client:
            verifier = ResourceJWTVerifier(configuration(), http_client=client)
            credential = token(keys, headers={"kid": None}, changes={"nbf": int(time.time()) - 10})
            assert await verifier.verify_token(credential)
            current["body"] = {"keys": [*jwks(keys)["keys"], *jwks(keys, 1, "key-2")["keys"]]}
            verifier._jwks_cache_time = 0
            assert await verifier.verify_token(credential) is None
    asyncio.run(run())
