# IEEE Xplore metadata

The existing opt-in IEEE connector now implements the official Metadata Search
API. It uses an already configured `PAPER_SEARCH_MCP_IEEE_API_KEY` (or legacy
`IEEE_API_KEY`); no credentials are created or saved and no plan is purchased.
Without a key its operations retain the explicit configuration error and the
normal CLI/MCP IEEE tools remain unavailable.

Search supports 0..1000 requested results, pages of at most 200, a 20-page scan
bound and stable-article-number deduplication. Metadata includes authors,
abstract, DOI, dates, citation count and index terms. Dates accept full date,
month, year, abbreviated/full month name plus year, with publication-year fallback.
The paper URL is constructed from the validated numeric article identity. Only
an official IEEE PDF locator with a matching `arnumber` is returned as `pdf_url`;
this metadata locator does not establish PDF availability or entitlement.

A metadata key does not grant full text. Native PDF download and reading remain
explicitly unsupported, even with a key. Use the returned paper page to review
available access options. No institutional login, payment or account enrollment
is performed by this connector.

HTTP 401/403 fail immediately. Network failures, 429 and selected 5xx responses
allow at most three attempts, with a maximum four-second retry delay; a longer
`Retry-After` reports rate limiting rather than sleeping past that bound.
Redirects are not followed. HTTP failures, invalid JSON/schema, malformed
article identities and non-advancing pagination raise errors, including failures
after an earlier successful page. Genuine zero results remain distinct.
Exception messages never include request URLs or response bodies, because the
official API requires its key in the query string.

Official references:
- [API query basics](https://developer.ieee.org/docs/read/Searching_the_IEEE_Xplore_Metadata_API)
- [Metadata response fields](https://developer.ieee.org/docs/read/Metadata_API_responses)

## Attribution and verification limits

Metadata parsing, pagination and date handling are adapted from
[PR #60](https://github.com/openags/paper-search-mcp/pull/60), commit
[`5f05c661`](https://github.com/debug-zhuweijian/paper-search-mcp/commit/5f05c661a7a9abe81c099b78b18f873c77927311),
by `debug-zhuweijian <3160527312@qq.com>` (verified against the original patch).
Preserve the contributor's history and `Co-authored-by` integration trailer.
The older assertion that API failures should return an empty list was replaced
with a regression asserting an explicit failure. Windows/date changes already
on the base branch are not replayed.

Deterministic tests use fake keys and mocked responses. They cover success,
pagination, errors/denials, redacted diagnostics, limits, malformed data and
unsupported full text. No live key-authenticated IEEE search has been validated.
