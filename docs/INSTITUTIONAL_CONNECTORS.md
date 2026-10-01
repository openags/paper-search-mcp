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

## Scopus and optional ScienceDirect text

Scopus is also explicit-only: `paper-search search 'TITLE(machine learning)' -s
scopus`, MCP `search_scopus`, or `search_papers` with `sources="scopus"`. Supplying
`PAPER_SEARCH_MCP_SCOPUS_API_KEY` (legacy `SCOPUS_API_KEY`) never adds it to a
preset. An already-issued institution token can optionally be supplied as
`PAPER_SEARCH_MCP_SCOPUS_INST_TOKEN` (legacy `SCOPUS_INST_TOKEN`); neither is
stored by the connector. Institutional access is determined by Elsevier, not
by whether a key exists. Users remain responsible for their API agreement,
permissions, quotas, and institutional network requirements.

Search uses `STANDARD` metadata by default. `COMPLETE` is explicitly selected
and may require additional entitlement. Metadata availability varies by view;
search does not promise abstracts or full text. No per-result detail requests
are made. Limits are 0..100 results and at most four pages of 25 records, without
retries or cursor harvesting. Invalid inputs fail before a request. The native
query syntax passes through. Supported options are identical in MCP and CLI:

| MCP `search_scopus` argument | Ordinary CLI search flag |
| --- | --- |
| `view` (`STANDARD`, `COMPLETE`) | `--scopus-view` |
| `sort` (`relevance`, `coverDate`, `citedby-count`, `creator`) | `--scopus-sort` |
| `field` (empty, `TITLE`, `ABS`, `KEY`, `AUTH`, `AFFILORG`) | `--scopus-field` |
| `date` (`YYYY`, `YYYY-YYYY`, `YYYY-`, `-YYYY`) | `--scopus-date` |

Open-ended dates are converted to closed ranges (1788 or next year as the
missing bound). `relevance` maps to the official `relevancy` sort parameter.
The existing CLI `--sort` remains local merged-result ordering and is separate
from `--scopus-sort`. The existing `--year` continues to apply to Semantic only.

`read_scopus_paper(paper_id)` or `paper-search read scopus ID` requests Scopus
abstract metadata only. Set MCP `full_text=true` or CLI `--full-text` to make at
most one additional ScienceDirect Article Retrieval request:

1. Validate the numeric Scopus ID before constructing a URL
2. Confirm the Abstract Retrieval response returns that exact Scopus ID
3. Retrieve directly by that record's DOI (or PII if no DOI); no title search
4. Confirm the article's own core metadata contains a matching DOI/PII and no
   conflicting target identifiers; reference-list DOI mentions do not count
5. Extract only a structured XML article body. Abstracts, raw `originalText`,
   metadata, HTML/error pages, and plain text are never labeled full text

The read response is JSON with `status`, `abstract`, `full_text`,
`full_text_requested`, and `reason`:

- `full_text`: identity verified and a structured article body was present
- `abstract_only`: an abstract is present, but no qualifying full text
- `unavailable`: neither requested article body nor an abstract is available

Article 401/403/404 responses preserve a `full_text_error` with a typed code and
HTTP status alongside any available abstract. Quota/timeouts/malformed responses
and identity failures raise typed errors, not success-shaped content. A matched
article may lack a structured body even when some formats are entitled; this is
reported as unavailable body, not a promise that no full text exists elsewhere.
No PDF tool is advertised and CLI download reports unsupported. Reads do not
write files. XML DTDs/entities are not loaded, and documents over 8 MiB are not
parsed. Caller-supplied API headers are never forwarded through redirects.

CLI read exits 0 for an available requested representation, 2 if full text was
requested but only an abstract was retrieved, and 1 for unavailable/error.
Callers should always inspect the JSON status and reason as well.

### Scopus validation and attribution

Search, pagination, dates, credential precedence, 401/403/429/timeouts, abstract
identity, DOI/PII article identity, malformed XML, wrong-title-like content,
and abstract-only/full-text distinctions have deterministic synthetic tests.
These are **not live entitlement validation**. No authorized Scopus key,
institutional token, or per-article ScienceDirect entitlement was supplied.
The public Elsevier XML example endpoint also returned a site-unavailable page
in this environment; the conservative XML route therefore remains fixture-only.
An authorized maintainer must validate an actual entitled XML article response,
STANDARD/COMPLETE search, abstract retrieval and quota behavior before calling
this live support verified. Earlier PR author live reports do not validate this
changed implementation.

Adapted from [PR #89](https://github.com/openags/paper-search-mcp/pull/89), source
head `81e46d7e45c0abaa4a40f515c581baf66ceaac24`. Its Git author is
`guokaichen <mildwall@users.noreply.github.com>` and its existing co-author trailer
is `Claude Fable 5 <noreply@anthropic.com>`; both are retained in the integration.
The source history is retained as a parent while the unsafe first title-hit
retrieval and default-key-triggered fan-out are deliberately not adopted.

Official references checked on 2026-10-01:
- [Scopus Search API](https://dev.elsevier.com/documentation/SCOPUSSearchAPI.wadl)
- [Abstract Retrieval API](https://dev.elsevier.com/documentation/AbstractRetrievalAPI.wadl)
- [Article Retrieval API](https://dev.elsevier.com/documentation/ArticleRetrievalAPI.wadl)
