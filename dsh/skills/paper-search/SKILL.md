---
name: paper-search
description: Search, download, and read academic papers through the paper-search MCP tools (mcp__paper-search__*), covering arXiv, PubMed, bioRxiv, Semantic Scholar, Crossref, OpenAlex and 15+ other sources. Use when the user asks to find papers, search research literature, download a paper PDF, or extract text from a paper.
whenToUse: Use when the user asks to find, download, or read academic papers or literature. Prefer these MCP tools over a raw web search for paper discovery.
---

# Paper Search

Academic paper discovery and retrieval in this harness runs through the `mcp__paper-search__*` tools (the paper-search-mcp MCP server). Do not run the `paper-search` CLI or hand-write API requests — use these tools.

## Core tools

- `mcp__paper-search__search_papers` — multi-source concurrent search with deduplication and DOI backfill. Prefer it for broad queries; one call covers many sources at once.
- `mcp__paper-search__download_with_fallback` — OA-first PDF download with a source-native → repository → Unpaywall fallback chain.
- `mcp__paper-search__search_unpaywall` — DOI-centric open-access metadata lookup.

## Source-specific tools

Per source the pattern is `search_<source>` / `download_<source>` / `read_<source>_paper`, with availability varying by source. Sources: arxiv, pubmed, biorxiv, medrxiv, iacr, semantic, crossref, openalex, pmc, core, europepmc, dblp, openaire, citeseerx, doaj, base, zenodo, hal, ssrn, google_scholar. With `PAPER_SEARCH_MCP_IEEE_API_KEY` / `PAPER_SEARCH_MCP_ACM_API_KEY` configured, ieee and acm tools are registered too.

For speed, prefer a targeted set (`search_arxiv`, `search_semantic`, `search_crossref`) over multi-source search when the field is clear.

## Workflow

1. Search: `search_papers` for broad coverage, `search_<source>` for targeted queries.
2. Present results as a table: title, authors, year, source, DOI/URL.
3. Full text: `read_<source>_paper`; PDF: `download_<source>` or `download_with_fallback`, then report the saved path.

## Notes

- Sources work without API keys; optional keys (Semantic Scholar, CORE, Unpaywall email, …) live in `~/.config/paper-search-mcp/.env` and are loaded by the server automatically.
- Google Scholar and SSRN can be bot-blocked upstream; fall back to other sources rather than retrying.
