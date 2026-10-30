# Public OpenReview papers

`openreview` is available in the normal CLI source registry and MCP unified
search. The direct tools are `search_openreview`, `download_openreview`, and
`read_openreview_paper`.

```sh
paper-search search "graph neural networks" -s openreview -n 5
paper-search download openreview NOTE_ID
paper-search read openreview 'https://openreview.net/forum?id=NOTE_ID'
```

The connector uses the official API v2 anonymously. It sends `source=forum`
when searching and separately requires a public (`readers: ["everyone", ...]`)
top-level paper note. Private notes, reviews, replies, deleted notes, account
creation and sign-in are outside scope. An explicit no-op authentication handler
prevents automatic `.netrc` credentials while retaining configured proxies and
TLS verification. API v1-only records are not included.

`max_results` must be an integer from 0 through 1000. Search uses offset pages
of at most 100 and scans at most 1000 upstream hits. Duplicate papers are removed.
A genuine successful empty response returns `[]`; HTTP, JSON/schema,
non-advancing pagination and scan-limit failures raise errors. Unified search
reports those errors in its per-source `errors` field. It does not return earlier
pages as a complete success after a later failure.

Only OpenReview-hosted PDFs are advertised for native downloading. A paper may
link to an external archive; its forum metadata is returned but `pdf_url` is
empty, and native downloading reports unsupported public full text. Downloads
first re-fetch the exact note ID, require it to be a public paper, and call the
fixed official `/pdf?id=...` endpoint. Redirects and authentication-required
responses are not followed. The bytes must be a complete, parseable PDF, and
the first-page text must contain the normalized paper title. Image-only PDFs,
PDFs with substantially different rendered titles, and files above 50 MiB are
conservatively rejected. Writes are atomic and validation failures do not
replace existing files. Reading downloads and validates before extracting text.

## Validation and provenance

This is an original implementation responding to
[issue #83](https://github.com/openags/paper-search-mcp/issues/83), reported by
`@adityaiyer7` (Thanks-to; no source code was copied from that issue).

Official references:
- [API v2](https://docs.openreview.net/reference/api-v2)
- [OpenReview Python API reference](https://openreview-py.readthedocs.io/en/latest/api.html)
- [Official client search implementation](https://github.com/openreview/openreview-py/blob/master/openreview/api/client.py)

Offline tests cover public-only filtering, malformed/error responses,
pagination, ID/URL spoofing, identity mismatches, corrupt/HTML/wrong-title/large
PDFs, atomic writes, and CLI/MCP routing. Live observations are listed in the
pull request separately; offline fixtures do not establish service availability.
