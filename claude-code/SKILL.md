---
name: paper-search
description: Search, download, and read academic papers from 20+ sources (arXiv, PubMed, Semantic Scholar, CrossRef, etc). Use when the user asks to find papers, search for research, look up academic literature, download a paper PDF, or extract text from a paper.
---

# Paper Search

Search, download, and read academic papers via the `paper-search` CLI.

## CLI Usage

All commands run via:
```bash
paper-search <command> [args]
```

If `paper-search` is not available, install it with `uv tool install paper-search-mcp`. Optional API keys can be configured in `~/.config/paper-search-mcp/.env`.

### Search
```bash
paper-search search "<query>" -n <max_per_source> -s <sources> -y <year>
```
- `-n`: results per source (default: 5)
- `-s`: comma-separated sources, "fast", "fastest", or "all" (default: all)
- `--exhaustive`: compatibility no-op (broad search is already the default); explicit `-s` always takes precedence
- `-y`: year filter for Semantic Scholar (e.g. "2020", "2018-2022")

For speed, use `-s fast` (OpenAlex, Crossref, arXiv, PubMed, Europe PMC; also Semantic Scholar when its API key is nonblank) or `-s fastest` (OpenAlex and Crossref). Explicit source choices are never expanded, even for DOI queries. Use `-s unpaywall` for DOI lookup; "all" already includes it. These presets do not enable optional paid sources.

### Download PDF
```bash
paper-search download <source> <paper_id> [-o ./downloads]
```

### Read (extract text)
```bash
paper-search read <source> <paper_id> [-o ./downloads]
```

### List sources
```bash
paper-search sources
```

### MCP tools from the CLI

`paper-search tool` calls the same registered tools as the MCP server, including
source-specific options and DOI lookup. Required arguments are positional;
optional arguments use kebab-case flags. Boolean flags use `--flag` and
`--no-flag`; omitted options retain their MCP defaults.

```bash
paper-search tool --help
paper-search tool --list  # JSON tool list, schemas, and annotations
paper-search tool search_crossref --help
paper-search tool search_crossref "transformer attention" --filter from-pub-date:2024-01-01 --sort published --order desc --max-results 2
paper-search tool get_crossref_paper_by_doi 10.1038/nature12373
paper-search tool search_iacr "cryptography" --no-fetch-details
```

Objects and lists return JSON; text and download paths return plain text. An
exception returns a JSON error with exit code 1; invalid CLI syntax exits with
code 2. A normal MCP result retains its original meaning, including error or
partial-result fields returned by the tool itself. `--list` shows only tools
enabled by the current configuration, so IEEE tools still require their API
key. The existing `search`, `download`, `read`, and `sources` commands are
unchanged. Tool calls keep the MCP tools' source selection and timeout behavior;
Sci-Hub fallback remains opt-in through `--use-scihub`.

## Output

`search` and `download` return JSON. `read` returns plain text. Config warnings go to stderr and can be ignored.

## Sources

arxiv, pubmed, biorxiv, medrxiv, google_scholar, iacr, semantic, crossref, openalex, pmc, core, europepmc, dblp, openaire, citeseerx, doaj, base, zenodo, hal, ssrn, unpaywall, acm

Optional (env vars): ieee (`IEEE_API_KEY`). ACM search is keyless and uses Crossref metadata; publisher PDF access may require the OA fallback.

## Workflow

1. Search with targeted sources to find papers
2. Present results as a table: title, authors, year, source, DOI/URL
3. If the user wants full text, use `read <source> <paper_id>`
4. If the user wants the PDF, use `download <source> <paper_id>` and report the saved path
