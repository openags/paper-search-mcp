# Explicit institutional connectors

## Web of Science Starter

- Use `paper-search search 'TI=(machine learning)' -s wos`, MCP `search_wos`, or
  MCP `search_papers` with `sources="wos"`. This is the caller's explicit opt-in.
- Supplying `PAPER_SEARCH_MCP_WOS_API_KEY` (legacy `WOS_API_KEY`) never adds WoS to
  default/`all`/`fast`/`fastest` searches. The prefixed value takes precedence,
  including a blank value. Keys are read at call time and sent only in a header
  to the official API. The connector creates no accounts or credential files.
- Native Starter query syntax is passed through. Example: `TI=(machine learning)`.
  `search_wos` also accepts the supported `db` values (default `WOS`); the
  equivalent ordinary CLI flag is `--wos-db`.
- Metadata only: UID, title, authors, DOI, publication year, source, record URL,
  and citation counts when the API plan supplies them. Starter does not provide
  abstract/full-text reading or direct PDF download. No misleading MCP download
  or read tools are registered. CLI read/download fail with an unsupported error.
- `max_results` must be 0..100. Each call makes at most two requests, at most 50
  records per page. Results stop at the requested count, upstream exhaustion, or
  that page budget. Repeated IDs are deduplicated. An unstable upstream snapshot
  can return fewer unique results than requested. No automatic retries.
- Missing citation counts map to `citations=0` for the existing Paper schema;
  `extra.citation_count_available=false` distinguishes unavailable counts from
  observed zero. The citation count for the selected database is used, not a sum
  across databases.

## Failure contract

Python callers receive typed `InstitutionalAPIError` subclasses. Aggregate CLI
and MCP `errors` preserve a stable `[code]` prefix: `credentials_required`,
`permission_denied` (401/403), `rate_limited` (429), `timeout`, `connection_error`,
`not_found`, or `invalid_response`. Genuine zero matches are an empty paper list
without a source error. A malformed/error envelope is never a zero-hit success.
The rate-limit exception exposes `retry_after`; it does not sleep or retry.

Every request has a 5-second connection timeout and a 10-second read timeout.
Redirects are rejected to avoid sending credential headers to another host.
Errors omit upstream bodies, URLs, and request exception details, which can echo
credentials. Existing aggregate source timeouts still apply.

## Validation and provenance

This implementation is fixture-tested, **not live entitlement validated**. No
Clarivate key or institutional authorization was provided for this work. A
maintainer with authorization must verify search, actual plan limitations, zero
matches and pagination on the official service before treating live support as
verified. Never put keys in an issue, pull request, chat transcript, or fixture.

Adapted from [PR #48](https://github.com/openags/paper-search-mcp/pull/48), source
head `c6f98a56383b76f90ee3e53e6a4750d0731b8ee7`, with issue
[#27](https://github.com/openags/paper-search-mcp/issues/27) as the feature request.
The source Git history credits `universea <13444641+universea@users.noreply.github.com>`.
This revision preserves that contribution while fixing default quota use, API
schema mapping, pagination, and swallowed provider errors.

Official references checked on 2026-10-01:
- [Clarivate Starter API](https://developer.clarivate.com/apis/wos-starter)
- [Starter OpenAPI schema](https://developer.clarivate.com/apis/wos-starter/swagger)
