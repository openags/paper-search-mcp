# One-hop OpenAlex references and citations

OpenAlex search results now fill the existing `Paper.references` field from
`referenced_works`. `Paper.to_dict()` still serializes it as a `; `-joined string.
This is parsed from the existing response, with no extra search-time requests.

Two MCP tools retrieve one relationship hop, ranked by OpenAlex citation count:

- `get_citing_papers(identifier, max_results=10, filter="", max_pages=5,
  max_requests=8, timeout_seconds=30)`
- `get_referenced_papers(identifier, max_results=10, filter="", max_pages=5,
  max_requests=8, timeout_seconds=30)`

`identifier` must be a DOI (`10.1038/nature14539`, `doi:10.1038/nature14539`, or a
DOI URL) or an OpenAlex work ID (`W2741809807` or its OpenAlex URL). Titles are
rejected rather than guessed. A singleton lookup checks seed existence and
resolves its canonical ID. Optional filters narrow the relationship query;
`PAPER_SEARCH_MCP_OPENALEX_API_KEY` and `PAPER_SEARCH_MCP_OPENALEX_EMAIL` work as
before. Credentials remain in request headers, not query parameters.

Each result contains `work_id`, `direction`, `papers` (existing serialized Paper
shape), `total` returned, `total_available` reported by OpenAlex, `truncated`,
`stop_reason`, `pages_fetched`, `requests_made`, and `skipped_results` (duplicate
or untitled works). `stop_reason` is `complete`, `max_results`, `page_budget`, or
`request_budget`. There are no implicit requests for additional graph hops.

The hard input ceilings are 500 results, five 100-record pages, eight HTTP
requests including the seed and redirects, and a 60-second MCP wall-clock
budget. Defaults are smaller (10 results, 30 seconds). No automatic retry sleeps
or unbounded request queue are added. HTTP 404, 429, 5xx, authentication failures,
invalid JSON, and timeouts raise explicit errors; a failure on a later page does
not return a misleading successful partial result. Request/page caps deliberately
return partial results with `truncated=true`.

Requests use the existing bounded worker pool. Python cannot forcibly interrupt
an in-flight blocking socket call when the MCP timeout expires; it retains its
pool slot until it exits. Each request has a socket timeout of at most ten
seconds and the remaining budget, with deadline checks before and after each
request. Direct synchronous connector users get these socket/deadline checks,
not the MCP wrapper's independent wall-clock cancellation. Redirects only follow
canonical OpenAlex work endpoints and consume the same request budget.

Coverage depends on records deposited/matched in OpenAlex. An empty reference
list does not prove the original paper has no references. Counts and page order
can drift during pagination. No graph analytics, title matching, automatic
citation enrichment, or new default network source is enabled.

## Verification

Offline tests cover serialization, optional keys/filters, strict DOI/ID handling,
percent-encoded DOI paths, seed validation, both relationship directions, global
server-side ranking, fixed page sizes, partial-result flags, request/redirect/time
budgets, null fields, explicit failures, and MCP deadline enforcement. Live API
responses are not required for the test suite.

API references checked for this implementation:
[IDs](https://help.openalex.org/api/get-single-entities/),
[paging](https://help.openalex.org/api/paging/), and
[citation coverage](https://help.openalex.org/data/works/citations/).

For adapted code and contributor credit see [provenance](CONTRIBUTION_PROVENANCE.md).
