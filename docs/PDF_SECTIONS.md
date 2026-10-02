# Bounded local PDF sections

`extract_sections` is a small, no-new-dependency step toward issue
[#94](https://github.com/openags/paper-search-mcp/issues/94). It reuses the existing
`pypdf` dependency to read an already-downloaded PDF and split extracted text at
recognized standalone English headings. It is **heuristic section segmentation**,
not full structured extraction, semantic interpretation, table reconstruction,
PICO extraction or citation resolution.

## Use

Download a paper using an existing source, keeping it in the server's local
`./downloads` directory. Then pass its **relative filename**, for example:

```python
await extract_sections("2301.00001.pdf", max_pages=20, max_chars=40000)
```

The existing generic CLI discovers the tool automatically:

```bash
paper-search tool extract_sections 2301.00001.pdf --max-pages 20 --max-chars 40000
```

If an operator uses a different download directory, they can set
`PAPER_SEARCH_MCP_SECTION_PDF_ROOT` to that directory before starting the server.
Its default is `./downloads`, relative to the server's working directory. An
explicitly empty setting is rejected rather than expanding access to that
working directory.
The tool has no root argument: a remote caller cannot select a new read root.
This setting only controls section extraction; it does not change the existing
connectors' download locations or the access policies of other tools.

## Access and budgets

- Relative `.pdf` filenames and nested relative paths are accepted
- Absolute paths, drive/UNC paths, `..` or `.` components, empty components,
  symlinks, junctions, non-regular files and non-PDF content are rejected
- Reads are confined to the configured root, with no-follow directory handles
  where supported and canonical-path/file-identity checks on the portable path
- PDFs larger than 20 MiB and encrypted PDFs are rejected; no password is requested
- Defaults: 30 pages, 60,000 extracted characters, 50 sections and 30 seconds
- Hard argument ceilings: 100 pages, 200,000 characters, 100 sections and 60 seconds
- Two admitted parser jobs, separate from the provider search pool
- Each parser is a single-use subprocess with a 512 MiB POSIX virtual-address-space
  limit and CPU limit, plus the parent-enforced wall-clock budget
- Parser stream expansion is limited to 8 MiB per stream/stream array; excessive
  compressed content is rejected before text extraction completes
- No network requests, file writes, OCR, external image decoders, model calls or
  new dependencies

Page, character and section limits return `truncated=true` and explicit
`stop_reasons`. Failed parsing, resource-limit violations and timeouts raise
errors, not a misleading complete/empty result.

The parent passes bounded verified bytes and scalar budgets to the parser,
never a path or credentials. It drains the result while the child runs, including
results larger than a pipe buffer, and terminates/reaps the child on timeout or
MCP cancellation. Capacity remains occupied until cleanup completes. The two
admitted jobs and their bounded supervision/cancellation monitors are separate
from provider searches.
The PDF's regular-file read happens before parser isolation; unresponsive
filesystem IO can hold a supervision slot after the caller times out, but cannot
consume the provider pool or create unbounded parser processes.

Hard process resource limits require POSIX `RLIMIT_AS` and `RLIMIT_CPU` support;
on unsupported platforms, including Windows, this new tool fails closed with a
clear error before reading a PDF. Other tools are unaffected. The CPU guard is
rounded up to whole seconds with one second of margin; the parent deadline is
independent. The worker applies parser limits with pypdf's context-local API on
newer versions and its legacy constants in the isolated child on the locked
6.9.2 version, without changing server-wide parser behavior.

These are resource and input-path controls, not a full OS/filesystem/network
sandbox for arbitrary parser vulnerabilities. Keep the service unprivileged and
OS-isolated for untrusted/remote workloads. Existing download/read tools retain
their own access behavior; this addition does not make them multi-tenant-safe.

## Evidence and uncertainty

Sections stay in extracted reading order, including repeated headings. Each
section returns its original `heading`, a normalized `label`, exact extracted
body `text`, 1-based `page_start`/`page_end`, and `start_char`/`end_char` offsets.
Offsets describe body text in the concatenated extracted page text (one newline
inserted after each processed page while space remains), not PDF byte offsets or
visual coordinates. Heading lines are excluded from body text; leading/trailing
body whitespace is removed, with offsets adjusted to match the exact slice.
`heading_match` is `standalone_line` or `none`; it is not a confidence score.
If a character limit cuts through the final line, `partial_final_line=true` and
that incomplete line is never interpreted as a heading. Its text is preserved.

Recognized headings include Abstract, Introduction, Background, Methods/Materials
and Methods/Methodology, Results, Discussion, Results and Discussion, Conclusion,
Limitations, References/Bibliography, and Acknowledgments. Common numeric/Roman
section prefixes are accepted. Labels are only lexical matches: a contents page
or running header can resemble a section heading. Unknown/inline/non-English
headings are not inferred; unmatched text remains in its original span or an
`unclassified` preamble. No absent section is invented. Combined headings stay
combined and repeated headings are not silently merged.

The response always warns that labels are heuristic. Scanned/image-only pages
may contain no extractable text, and a blank processed range does not prove the
whole paper is blank. Multi-column layout, equations and tables inherit pypdf's
reading-order limits. `pages_processed`, `total_pages`, character counts and
truncation metadata make the processed scope explicit.

## Verification and attribution

Deterministic tests use locally generated PDFs and cover real pypdf extraction,
page spans, repeated/unknown/combined headings, exact text slices, all budgets,
blank/encrypted/corrupt inputs, path traversal, symlink and directory-swap attacks,
strict MCP wire validation, real generic CLI output above a pipe buffer, bounded
compressed expansion, memory-limit rejection, process-start failure cleanup,
non-cooperative parser termination and MCP cancellation. No academic-provider
access is used. The tool is included in authenticated HTTP/stdio registry checks.

Parser-limit references: [pypdf security](https://pypdf.readthedocs.io/en/latest/user/security.html)
and [configuration](https://pypdf.readthedocs.io/en/latest/modules/configuration.html).

Thanks to the roadmap in issue #94. This is an original implementation, with no
code copied from the proposal. The remainder of that roadmap stays open.
