# Optional local search cache

Search caching is **off by default**. Starting the server, searching, or asking
for cache status does not create a cache while it is disabled. To opt in, set:

```dotenv
PAPER_SEARCH_MCP_SEARCH_CACHE_ENABLED=1
# Optional overrides (defaults shown):
PAPER_SEARCH_MCP_SEARCH_CACHE_PATH=~/.cache/paper-search-mcp/search.sqlite3
PAPER_SEARCH_MCP_SEARCH_CACHE_TTL_SECONDS=86400
PAPER_SEARCH_MCP_SEARCH_CACHE_MAX_ENTRIES=1000
PAPER_SEARCH_MCP_SEARCH_CACHE_MAX_BYTES=16777216
```

Restart the server after changing settings. Set `SEARCH_CACHE_ENABLED=0` (with
the same prefix) to disable reads and writes. Invalid configuration disables
caching with a warning instead of blocking the server. The existing legacy
unprefixed environment-variable fallback also works.

The cache covers eligible public-source searches routed through `server.async_search`, including
MCP source tools and unified search. Direct Python `searcher.search()` calls,
legacy CLI paths that call connectors directly, citation lookups, downloads, and
paper-reading tools are not cached. CLI generic calls of MCP search tools use the
same server integration. No additional network calls or background refreshes are
added. Simultaneous misses can still issue separate provider calls.

## Inspect and clear

- MCP `get_search_cache_status()` shows enabled state, configured path/limits,
  stored entry count, and whether the database can be opened. It never lists
  queries. Expired entries may count until the next lookup prunes them.
- MCP `clear_search_cache()` removes all result rows and reports how many were
  cleared. It works while disabled and does not create a missing database.
  A generation counter prevents already-running searches from repopulating the
  cache after a clear. New searches may populate it while enabled.
- Disabling alone does not delete existing cached data. To stop persistence and
  remove cached results, disable, restart, then call `clear_search_cache()`.
  Clear only affects the configured cache, not PDFs or other files.

## Data and resource boundaries

Opting in persists search queries, filters/options, and returned metadata in
plain local SQLite. Use a private directory, especially on shared machines;
do not enable this for queries you do not want stored. New files/directories
request owner-only permissions on POSIX; existing directory permissions are not
changed. Filesystem snapshots/backups may retain removed data, so clearing is
not a guarantee of secure erasure. Remote operators should consider every
caller's queries before enabling a shared server cache.

Keys use canonical JSON with the full source class, exact case-sensitive query,
limit, all search options, and a fingerprint of provider configuration. They do
not join ambiguous delimiters or use a query hash as their only identity.
Authenticated providers (API keys, tokens, session authentication, or non-consent
cookies) bypass caching. Institutional and newly added connectors are excluded by
default; no WoS/Scopus or per-user OAuth-authorized provider results are cached.
A custom connector may declare `search_cache_public = True` only when its
uncredentialed results are safe to share among server callers; credential checks
still apply. The server's own OAuth identity does not enter public search cache
keys, so only genuinely public source results may be cached on a shared server.
Global credential settings affect the configuration digest but are never stored
as raw credential values. Unsupported or oversized keys bypass caching. Clear the
cache after changing custom connector configuration not represented by its
endpoint/key/contact settings, or after upgrading a connector's output contract.

Only nonempty successful results are cached, preserving their existing serialized
shape. Empty lists and exceptions are not cached: some legacy connectors still
represent provider errors as empty lists. Cancellation before a search returns
does not cache a late result. Nonempty partial results from a legacy connector
cannot be distinguished from complete results without changing that connector;
they follow the same TTL. Cache errors fail open to provider search and log only
a generic warning, without query content. Clear failures are explicit.

TTL is measured from storage time, never extended by a cache hit. Lowering the
TTL shortens existing entries' usable lifetime. Clock rollback invalidates
entries written in the apparent future. Limits are 1–2,592,000 seconds TTL,
1–10,000 entries, and 1–512 MiB database size. Individual entries are capped at
1 MiB or one quarter of the configured database limit, whichever is smaller.
Oldest entries are evicted to keep key+payload bytes under half the database
limit, reserving room for SQLite overhead; SQLite's page-count limit caps the
physical database. A transient rollback journal can use additional space up to
roughly one database copy. Clearing reuses freed pages; it need not shrink the
file. If a limit is lowered below an existing file's size, searches bypass that
cache; clear remains available. To reclaim disk space, stop all server processes
and remove only the configured cache database after clearing it.

SQLite transactions synchronize threads and processes. Lock waits are capped at
100 ms; contention can skip caching rather than stall provider work. A corrupt,
incompatible, or unrelated existing database is not overwritten or repaired.

## Verification and provenance

Deterministic tests cover default-off behavior, enable/disable, canonical key
separation and collisions, TTL boundaries, reduced TTL and clock rollback, row
and physical byte bounds, corruption/lock failures, clear-vs-inflight races,
cancellation, multiple threads/processes, and unchanged result serialization.
No external cache dependency is required; SQLite is in Python's standard library.

Thanks to `@heliowap` for the infrastructure proposal in
[issue #94](https://github.com/openags/paper-search-mcp/issues/94). This is a new,
limited cache implementation; no code from the issue was copied. It does not
implement semantic indexes, LLM extraction, evidence tables, or medical heuristics.
