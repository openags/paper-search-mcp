"""Optional OAuth protected-resource boundary for the HTTP transports.

The MCP SDK owns discovery, bearer authentication and scope enforcement;
FastMCP owns signature/JWKS verification. No token issuer or credential store
is implemented here. stdio does not import or configure this module.
"""
from __future__ import annotations

import ipaddress
import math
import os
import re
import time
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastmcp.server.auth.providers.jwt import JWTVerifier
from joserfc.errors import JoseError
from joserfc.jwt import JWTClaimsRegistry
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.routes import (
    build_resource_metadata_url,
    create_protected_resource_routes,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, TypeAdapter, UrlConstraints
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount

from .config import load_env_file

_PREFIX = "PAPER_SEARCH_MCP_"
_FIELDS = (
    "OAUTH_ISSUER", "OAUTH_JWKS_URI", "OAUTH_AUDIENCE", "OAUTH_RESOURCE_URL",
    "OAUTH_SCOPES", "OAUTH_ALGORITHM",
)
_ALGORITHMS = frozenset({
    "RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512",
})
_SCOPE = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+\Z")
# An issuer is an exact identifier: https://issuer.example and its slash-suffixed
# variant must not be conflated by URL validation or SDK metadata serialization.
_ISSUER_URL = TypeAdapter(Annotated[AnyHttpUrl, UrlConstraints(preserve_empty_path=True)])


class AuthConfigurationError(ValueError):
    """An unsafe or incomplete HTTP auth configuration; never fall back to open."""


def _public_url(
    value: str, name: str, *, loopback_http: bool = False,
    preserve_empty_path: bool = False,
) -> str:
    """Validate operator-supplied URLs without network lookups or normalization."""
    try:
        parsed = urlsplit(value)
        # Do not silently rewrite the audience/resource/issuer identity.
        validated = (
            _ISSUER_URL.validate_python(value) if preserve_empty_path else AnyHttpUrl(value)
        )
        if str(validated) != value:
            raise ValueError("URL is not canonical")
        loopback = parsed.hostname == "localhost"
        if parsed.hostname and not loopback:
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                pass
        secure = parsed.scheme == "https" or (
            loopback_http and parsed.scheme == "http" and loopback
        )
        if (
            not secure or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError("unsafe URL")
        _ = parsed.port
    except ValueError as exc:
        suffix = " (HTTP is allowed only for a loopback resource)" if loopback_http else ""
        raise AuthConfigurationError(
            f"{_PREFIX}{name} must be a canonical HTTPS URL without credentials, "
            f"query or fragment{suffix}"
        ) from exc
    return value


@dataclass(frozen=True)
class OAuthConfig:
    issuer: str
    jwks_uri: str
    audience: str
    resource_url: str
    scopes: tuple[str, ...]
    algorithm: str = "RS256"


def load_http_auth_config(auth_mode: str | None, endpoint_path: str) -> OAuthConfig | None:
    """Read HTTP-only settings after transport selection, and fail closed."""
    load_env_file()
    mode = auth_mode if auth_mode is not None else os.getenv(_PREFIX + "AUTH", "none").strip()
    configured = {name: os.getenv(_PREFIX + name, "").strip() for name in _FIELDS}
    if mode == "none":
        if any(configured.values()):
            raise AuthConfigurationError(
                "OAuth settings are present but HTTP authentication is disabled; "
                "set PAPER_SEARCH_MCP_AUTH=oauth or remove the OAuth settings"
            )
        return None
    if mode != "oauth":
        raise AuthConfigurationError("PAPER_SEARCH_MCP_AUTH must be 'none' or 'oauth'")
    missing = [name for name in _FIELDS[:-1] if not configured[name]]
    if missing:
        raise AuthConfigurationError(
            "Missing OAuth settings: " + ", ".join(_PREFIX + name for name in missing)
        )
    issuer = _public_url(
        configured["OAUTH_ISSUER"], "OAUTH_ISSUER", preserve_empty_path=True
    )
    jwks_uri = _public_url(configured["OAUTH_JWKS_URI"], "OAUTH_JWKS_URI")
    resource_url = _public_url(
        configured["OAUTH_RESOURCE_URL"], "OAUTH_RESOURCE_URL", loopback_http=True
    )
    if urlsplit(resource_url).path != endpoint_path:
        raise AuthConfigurationError(
            "OAUTH_RESOURCE_URL path must match the HTTP endpoint "
            "(--path for streamable-http, /sse for SSE)"
        )
    audience = configured["OAUTH_AUDIENCE"]
    # RFC 8707 resource binding: do not advertise one resource while accepting
    # tokens for a different API (or an OIDC client/ID-token audience).
    if audience != resource_url:
        raise AuthConfigurationError("OAUTH_AUDIENCE must exactly equal OAUTH_RESOURCE_URL")
    scopes = tuple(dict.fromkeys(configured["OAUTH_SCOPES"].split(" ")))
    if any(not _SCOPE.fullmatch(scope) for scope in scopes) or "offline_access" in scopes:
        raise AuthConfigurationError(
            "OAUTH_SCOPES must be nonempty space-separated OAuth scope tokens; "
            "offline_access is not a resource scope"
        )
    algorithm = configured["OAUTH_ALGORITHM"] or "RS256"
    if algorithm not in _ALGORITHMS:
        raise AuthConfigurationError(
            "OAUTH_ALGORITHM must be a supported asymmetric RS*, PS* or ES* algorithm"
        )
    return OAuthConfig(issuer, jwks_uri, audience, resource_url, scopes, algorithm)


class ResourceJWTVerifier(JWTVerifier):
    """Apply access-token claim requirements after library signature validation."""

    def __init__(self, config: OAuthConfig, *, http_client: httpx.AsyncClient | None = None):
        # Scopes are checked by the SDK gate, not the JWT verifier, so valid
        # tokens lacking a required scope receive 403 rather than 401.
        super().__init__(
            jwks_uri=config.jwks_uri, issuer=config.issuer,
            audience=config.audience, algorithm=config.algorithm,
            http_client=http_client,
        )
        self._resource_url = config.resource_url
        self._claims = JWTClaimsRegistry(
            iss={"essential": True, "value": config.issuer},
            aud={"essential": True, "value": config.audience},
            exp={"essential": True}, sub={"essential": True},
        )

    async def verify_token(self, token: str):
        try:
            verified = await super().verify_token(token)
            if verified is None:
                return None
            claims = verified.claims
            self._claims.validate(claims)
            # NumericDate must be finite and not boolean. Reject exp == now
            # too; the library's generic claim check uses a strict '<'.
            for key in ("exp", "nbf", "iat"):
                value = claims.get(key)
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    return None
            if claims["exp"] <= time.time():
                return None
            if not isinstance(claims["sub"], str) or not claims["sub"].strip():
                return None
            for key in ("client_id", "azp"):
                if key in claims and (not isinstance(claims[key], str) or not claims[key].strip()):
                    return None
            for key in ("scope", "scp"):
                if key not in claims:
                    continue
                value = claims[key]
                if isinstance(value, str):
                    values = value.split(" ") if value else []
                elif isinstance(value, list):
                    values = value
                else:
                    return None
                if any(not isinstance(item, str) or not _SCOPE.fullmatch(item) for item in values):
                    return None
        except (JoseError, ValueError, TypeError, KeyError, OverflowError):
            return None
        # SDK sessions are bound to (client, issuer, subject). FastMCP's generic
        # verifier does not populate the SDK subject field in 3.4.2.
        verified.subject = claims["sub"]
        verified.resource = self._resource_url
        return verified


class _ScopeChallenge:
    """Enrich the SDK's RFC 6750 errors with every required resource scope."""

    def __init__(self, app, scopes: tuple[str, ...]):
        self.app = app
        self.scope_parameter = ('scope="' + " ".join(scopes) + '"').encode("ascii")

    async def __call__(self, scope, receive, send):
        async def send_with_scope(message):
            if message["type"] == "http.response.start" and message["status"] in (401, 403):
                message = dict(message)
                headers = []
                for name, value in message.get("headers", []):
                    if name.lower() == b"www-authenticate" and value.startswith(b"Bearer "):
                        value += b", " + self.scope_parameter
                    headers.append((name, value))
                message["headers"] = headers
            await send(message)
        await self.app(scope, receive, send_with_scope)


def create_protected_http_app(server: FastMCP, transport: str, config: OAuthConfig) -> Starlette:
    """Wrap the existing SDK app; do not rebuild or copy its tool registry."""
    if transport not in ("sse", "streamable-http"):
        raise AuthConfigurationError("OAuth protected resources require an HTTP transport")
    resource = AnyHttpUrl(config.resource_url)
    parsed = urlsplit(config.resource_url)
    # Preserve local access, and permit only the configured public authority.
    # Never trust arbitrary Host or forwarded headers just because auth is on.
    existing = server.settings.transport_security
    allowed_hosts = list(existing.allowed_hosts) if existing else []
    allowed_origins = list(existing.allowed_origins) if existing else []
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys([*allowed_hosts, parsed.netloc])),
        allowed_origins=list(dict.fromkeys([*allowed_origins, f"{parsed.scheme}://{parsed.netloc}"])),
    )
    inner = server.sse_app() if transport == "sse" else server.streamable_http_app()
    verifier = ResourceJWTVerifier(config)
    gate = RequireAuthMiddleware(
        inner, list(config.scopes), build_resource_metadata_url(resource)
    )
    # Public metadata is outside authentication. Bearer credentials are only
    # extracted from Authorization, never a query string, form or cookie.
    protected = Starlette(
        routes=[Mount("/", app=_ScopeChallenge(gate, config.scopes))],
        middleware=[
            Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(verifier)),
            Middleware(AuthContextMiddleware),
        ],
    )
    return Starlette(
        routes=[
            *create_protected_resource_routes(
                resource_url=resource, authorization_servers=[_ISSUER_URL.validate_python(config.issuer)],
                scopes_supported=list(config.scopes), resource_name="Paper Search MCP",
            ),
            Mount("/", app=protected),
        ],
        lifespan=inner.router.lifespan_context,
    )


def run_protected_http(server: FastMCP, transport: str, config: OAuthConfig) -> None:
    """Use the same ASGI runner as the SDK, after successful configuration."""
    import uvicorn
    app = create_protected_http_app(server, transport, config)
    uvicorn.run(
        app, host=server.settings.host, port=server.settings.port,
        log_level=server.settings.log_level.lower(),
    )
