# Optional OAuth protected resource

Issue [#25](https://github.com/openags/paper-search-mcp/issues/25) is addressed by an
opt-in HTTP resource-server boundary. The external authorization server handles
login, consent, client registration, PKCE, issuing and refreshing access tokens.
Paper Search only advertises that server and validates its JWT access tokens.
There is no authorization server, account database, token store, static bearer
secret, Laravel integration, or automatic account/grant creation here.

## Defaults and scope

- `stdio` is still the default and does not read or validate HTTP auth settings
- HTTP still binds to `127.0.0.1` by default
- With no OAuth settings, local SSE and Streamable HTTP remain unauthenticated
- `--auth oauth` or `PAPER_SEARCH_MCP_AUTH=oauth` protects **both** HTTP transports,
  including SSE message POSTs and Streamable HTTP GET/POST/DELETE requests
- Configuration errors stop HTTP startup; they never fall back to open mode
- Directly importing `server.mcp` and calling its SDK `sse_app()` or
  `streamable_http_app()` does not apply these application settings. Use the
  documented CLI, or call `load_http_auth_config` and
  `create_protected_http_app` explicitly when embedding the server

This is one access policy for the entire shared server. Possession of **all**
configured scopes authorizes every exposed tool, including downloads and file
writes. Scope names are operator-defined, not a per-tool permission system.
Run the service as an unprivileged user with a restricted filesystem and network;
do not offer this mode as an untrusted multi-tenant sandbox. Authentication does
not constrain existing tool path arguments, upstream destinations, disk use,
provider quotas or tool execution costs.

## Configuration contract

All names below use the `PAPER_SEARCH_MCP_` prefix. They can be set in the existing
user configuration `.env` file or process environment. There are no legacy aliases
for the new auth settings. CLI `--auth` selects the mode; the remaining auth
configuration comes from the environment.

| Variable suffix | Requirement |
| --- | --- |
| `AUTH` | `none` (default) or `oauth` |
| `OAUTH_ISSUER` | Exact JWT issuer and authorization-server discovery identifier; HTTPS |
| `OAUTH_JWKS_URI` | Operator-approved HTTPS public signing-key endpoint |
| `OAUTH_RESOURCE_URL` | Exact public MCP endpoint URL, including `/mcp`, your custom `--path`, or `/sse` |
| `OAUTH_AUDIENCE` | Must exactly equal `OAUTH_RESOURCE_URL`; tokens for other APIs or OIDC client IDs are rejected |
| `OAUTH_SCOPES` | Nonempty, single-space-separated scope names that every request requires |
| `OAUTH_ALGORITHM` | Optional; defaults to `RS256`; supported values: `RS256`, `RS384`, `RS512`, `PS256`, `PS384`, `PS512`, `ES256`, `ES384`, `ES512` |

URLs must be canonical, without user information, query strings or fragments.
Origin-only URLs include the trailing slash, for example
`https://login.example.org/`. This avoids silently changing the issuer string
between JWT validation and SDK-generated metadata. If an issuer actually uses a
non-canonical identifier, this version deliberately rejects that configuration;
do not change the issuer claim yourself to work around it. Use a compatible
issuer configuration or a separately reviewed adapter.

HTTP is accepted only for a loopback **resource URL** during local development.
Issuer and JWKS URLs always require HTTPS. Neither `none` nor shared-secret HS*
signature algorithms are accepted. `offline_access` is not a resource scope.
OAuth variables left behind while auth is disabled cause a startup error: remove
them before deliberately returning to open HTTP mode. stdio ignores them.

Example configuration, using illustrative domains only:

```bash
export PAPER_SEARCH_MCP_AUTH=oauth
export PAPER_SEARCH_MCP_OAUTH_ISSUER=https://login.example.org/
export PAPER_SEARCH_MCP_OAUTH_JWKS_URI=https://login.example.org/.well-known/jwks.json
export PAPER_SEARCH_MCP_OAUTH_RESOURCE_URL=https://papers.example.org/mcp
export PAPER_SEARCH_MCP_OAUTH_AUDIENCE=https://papers.example.org/mcp
export PAPER_SEARCH_MCP_OAUTH_SCOPES='papers:use'
paper-search-mcp --transport streamable-http --host 127.0.0.1 --port 8000 --path /mcp
```

For SSE use `--transport sse`, and set **both** resource URL and audience to the
public `/sse` URL. A token issued for `/mcp` intentionally cannot be replayed
against `/sse` unless the issuer explicitly includes both audiences.

## Discovery and token behavior

The SDK publishes public RFC 9728 metadata at the origin followed by
`/.well-known/oauth-protected-resource` and the endpoint path. For the example,
this is `https://papers.example.org/.well-known/oauth-protected-resource/mcp`.
It advertises the exact resource, issuer, supported scopes, and header-only
bearer authentication. Metadata can be read without a token; it does not expose
keys or secrets. Missing/invalid tokens receive HTTP 401 and a Bearer
`WWW-Authenticate` challenge containing `resource_metadata` and all required
scopes. A valid token without every required scope receives HTTP 403 with
`insufficient_scope` and the same discovery/scope guidance.

Only `Authorization: Bearer <access-token>` is accepted. Query parameters,
cookies, arbitrary authorization schemes and ID tokens for a different audience
do not authenticate a request. Tokens are not forwarded to academic providers.
The verifier checks the signature with the configured asymmetric algorithm and
JWKS, exact issuer, audience, required finite expiration, and a nonempty subject.
Optional `nbf` and `iat` timestamps are validated. It accepts `scope` or `scp`
(space-delimited string or array), following FastMCP's precedence of `scope` when
both exist. Required scopes are enforced by the SDK HTTP gate.

Subject is explicitly copied into the SDK access-token identity. Both SDK
transports bind session ownership to `(client_id, issuer, subject)`, so a different
user cannot use the same client's stolen session ID. `client_id` or `azp` can be
present; otherwise FastMCP uses the subject as the client identifier.

Signature verification, JWKS parsing/caching and key rotation use FastMCP's
`JWTVerifier`; standard claims use `joserfc.JWTClaimsRegistry`; discovery,
bearer auth, scope enforcement and transport sessions use the MCP SDK. No custom
cryptographic implementation or custom OAuth flow is introduced. In the tested
FastMCP version, JWKS keys are cached for up to one hour and refreshed for an
unknown key ID; failed refreshes reject the request. Already cached keys can
continue working until cache expiry. Key revocation is therefore not immediate,
and no token-introspection/revocation endpoint is implemented.

## Deployment checklist

1. Configure an existing OAuth authorization server to issue short-lived JWT
   **access tokens** for the exact public resource URL, with the chosen scope(s)
   and algorithm. Verify its issuer metadata, JWKS, claims and key-rotation
   behavior. No private signing key or client secret belongs in this server
2. Ensure the MCP client can discover the external issuer and obtain a client ID
   via the mechanism that the client and issuer both support (pre-registration,
   Dynamic Client Registration, or Client ID Metadata Documents). This feature
   does not make an arbitrary OIDC provider automatically compatible with every
   MCP client
3. Terminate TLS at a trusted reverse proxy. Keep the backend private, forward
   `Authorization`, preserve the public path and `Host`, and route the metadata
   path as well as the MCP endpoint. SSE also needs `/messages/` routed to the
   backend. Cross-origin browser access is not enabled by this feature
4. Keep the public resource path identical to the backend endpoint. Prefix
   rewriting is not supported. Do not expose a second SDK app or an open backend
   listener that bypasses the protected CLI/factory
5. DNS-rebinding checks remain enabled, permitting the configured public
   authority/origin and the SDK's existing local allowlist. Arbitrary forwarded
   headers do not expand that allowlist
6. Apply inbound rate limits, concurrency/body-size limits, monitoring, filesystem
   isolation, download quotas and appropriate network egress restrictions. JWT
   key-cache misses can trigger a JWKS request; throttle abusive unauthenticated
   traffic at the edge. JWT verification is not a DoS defense
7. Test a real client login/consent/refresh cycle and confirm the two error cases
   below before publishing the service. Keep bearer tokens out of URLs, shell
   history, application logs and support reports

Non-secret checks after configuring your own deployment:

```bash
# Public metadata should be 200 and name your exact resource/issuer/scopes.
curl -i https://papers.example.org/.well-known/oauth-protected-resource/mcp
# No token should be 401, with resource_metadata and scope in the challenge.
curl -i https://papers.example.org/mcp
```

Use your client's secure token handling to test a valid access token and a token
missing scope; do not paste credentials into bug reports. Wrong issuer/audience,
expired tokens and invalid signatures should return 401; insufficient scope
should return 403.

## Verification and known limits

Deterministic tests use fresh in-memory RSA keys and a fixture JWKS endpoint.
They cover both transports, valid MCP initialization/tool calls over real local
HTTP sockets, rejection paths, discovery/challenges, JWKS cache/rotation/outage,
configuration failures, session ownership and preservation of stdio defaults.
They never create users, client grants or persistent signing credentials.

The fixtures are **not** an end-to-end identity-provider certification. No live
issuer/deployment/client credentials were supplied for this work. Real issuer
metadata, browser login/consent, client registration, PKCE, refresh, proxies/TLS,
provider-specific claim mapping and production load still need deployment-specific
validation. The implementation is a protected-resource component, not a claim
that an entire deployment conforms to every OAuth 2.1 or later MCP requirement.
The protocol exercised by integration tests is MCP `2025-11-25`.

### References and provenance

- [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [RFC 9728 protected resource metadata](https://www.rfc-editor.org/rfc/rfc9728.html)
- [FastMCP JWT verification](https://gofastmcp.com/servers/auth/token-verification)
- [FastMCP remote OAuth architecture](https://gofastmcp.com/servers/auth/remote-oauth)

Thanks to [issue #25](https://github.com/openags/paper-search-mcp/issues/25) for the
native protected-resource request. This is a new focused implementation using the
installed libraries' public interfaces; no code/tests were copied or adapted
from PR #56, PR #90, or a Laravel integration.
