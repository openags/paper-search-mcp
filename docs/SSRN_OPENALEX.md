# SSRN discovery through OpenAlex

`ssrn` search now uses the official OpenAlex metadata API with the filter
`locations.source.id:S4210172589`. Matching a secondary location is important:
an OpenAlex work can have a journal as its primary location and an SSRN preprint
as another location. The existing `ssrn` CLI and MCP entry points use the same
connector. No fallback to SSRN HTML search occurs after an API failure.

Existing OpenAlex API key and email configuration is honored. The connector
does not create an account, save credentials or enroll in a plan. Provider access
and rate limits can change; HTTP authentication/rate-limit failures are explicit.

The requested count must be an integer from 0 through 1000. Cursor pagination
fetches up to 100 records per page and scans at most 20 pages. A null cursor or
an empty page ends a successful search. Repeated/invalid cursors, invalid JSON,
malformed result envelopes, HTTP errors and the scan limit raise errors instead
of looking like zero results or silently returning a partial result set.

SSRN identifiers are accepted only from official SSRN locators or the exact
`10.2139/ssrn.<id>` DOI form. Host suffix tricks, credentials/ports in URLs,
multiple IDs and unrelated `abstract_id` parameters are rejected. Records without
a verifiable unique SSRN identity are skipped; duplicate identities are removed.
Returned IDs use `ssrn:<abstract_id>`, with a canonical SSRN paper page. OpenAlex
OA and landing URLs are not assumed to be direct PDFs, so search leaves
`pdf_url` empty.

Public SSRN downloads remain best-effort. They require canonical paper identity
and citation-title metadata from the requested SSRN page, then an official SSRN
PDF link from that page. Requests do not follow redirects or sign in. Downloaded
bytes must pass complete-PDF, parser and first-page title checks (50 MiB limit)
before an atomic write. Missing identity metadata, challenges, login requirements
and PDFs with non-extractable titles are conservatively rejected.

## Attribution

The SSRN-through-OpenAlex design, cursor behavior and normalization regressions
are adapted from [PR #60](https://github.com/openags/paper-search-mcp/pull/60),
original commit
[`5f05c661`](https://github.com/debug-zhuweijian/paper-search-mcp/commit/5f05c661a7a9abe81c099b78b18f873c77927311),
authored by `debug-zhuweijian <3160527312@qq.com>` (verified in the original Git
patch). The integration commit must retain this `Co-authored-by` trailer and
original contribution history. The PR's already-incorporated date/Windows
changes and tests that swallow assertions are deliberately not replayed.

The secondary-location filter, strict locator identity, failure diagnostics,
loop bounds and validated public downloads harden that proposal. Offline tests
are separate from live-network observations and do not claim full SSRN coverage.
