---
name: paper-search
description: Search, download, and read academic papers from 20+ sources (arXiv, PubMed, Semantic Scholar, CrossRef, etc). Use when the user asks to find papers, search for research, look up academic literature, download a paper PDF, or extract text from a paper.
---

# Paper Search

Search, download, and read academic papers via the `paper-search` CLI from the `paper-search-mcp` package.

## CLI Usage

All commands run via:
```bash
uvx --from git+https://github.com/openags/paper-search-mcp paper-search <command> [args]
```

No repository clone or local path configuration is required. Optional API keys can be configured in `~/.config/paper-search-mcp/.env`.

### Search
```bash
uvx --from git+https://github.com/openags/paper-search-mcp paper-search search "<query>" -n <max_per_source> -s <sources> -y <year>
```
- `-n`: results per source (default: 5)
- `-s`: comma-separated sources or "all" (default: all)
- `-y`: year filter for Semantic Scholar (e.g. "2020", "2018-2022")

For speed, prefer targeted sources (`-s arxiv,semantic,crossref`) over "all" unless broad coverage is needed.

### Download PDF
```bash
uvx --from git+https://github.com/openags/paper-search-mcp paper-search download <source> <paper_id> [-o ./downloads]
```

### Read (extract text)
```bash
uvx --from git+https://github.com/openags/paper-search-mcp paper-search read <source> <paper_id> [-o ./downloads]
```

### List sources
```bash
uvx --from git+https://github.com/openags/paper-search-mcp paper-search sources
```

## Output

`search` and `download` return JSON. `read` returns plain text. Config warnings go to stderr and can be ignored.

## Sources

arxiv, pubmed, biorxiv, medrxiv, google_scholar, iacr, semantic, crossref, openalex, pmc, core, europepmc, dblp, openaire, citeseerx, doaj, base, zenodo, hal, ssrn, unpaywall

Optional env vars use the `PAPER_SEARCH_MCP_` prefix, for example `PAPER_SEARCH_MCP_IEEE_API_KEY` and `PAPER_SEARCH_MCP_ACM_API_KEY`.

## Workflow

1. Search with targeted sources to find papers
2. Present results as a table: title, authors, year, source, DOI/URL
3. If the user wants full text, use `read <source> <paper_id>`
4. If the user wants the PDF, use `download <source> <paper_id>` and report the saved path
