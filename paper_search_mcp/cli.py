#!/usr/bin/env python3
"""CLI interface for paper-search — search, download, and read academic papers."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from .config import get_env
from .crossref_resolver import metadata_for_identifier, resolve_title
from .file_naming import get_default_output_dir, metadata_text, paper_output_path
from .academic_platforms.arxiv import ArxivSearcher
from .academic_platforms.pubmed import PubMedSearcher
from .academic_platforms.biorxiv import BioRxivSearcher
from .academic_platforms.medrxiv import MedRxivSearcher
from .academic_platforms.google_scholar import GoogleScholarSearcher
from .academic_platforms.iacr import IACRSearcher
from .academic_platforms.semantic import SemanticSearcher
from .academic_platforms.crossref import CrossRefSearcher
from .academic_platforms.openalex import OpenAlexSearcher
from .academic_platforms.pmc import PMCSearcher
from .academic_platforms.core import CORESearcher
from .academic_platforms.europepmc import EuropePMCSearcher
from .academic_platforms.dblp import DBLPSearcher
from .academic_platforms.openaire import OpenAiresearcher
from .academic_platforms.citeseerx import CiteSeerXSearcher
from .academic_platforms.doaj import DOAJSearcher
from .academic_platforms.base_search import BASESearcher
from .academic_platforms.unpaywall import UnpaywallResolver, UnpaywallSearcher
from .academic_platforms.zenodo import ZenodoSearcher
from .academic_platforms.hal import HALSearcher
from .academic_platforms.ssrn import SSRNSearcher
from .academic_platforms.sci_hub import SciHubSource

# ---------------------------------------------------------------------------
# Searcher registry
# ---------------------------------------------------------------------------

SEARCHERS: Dict[str, Any] = {}


def _init_searchers() -> None:
    """Lazily initialize searcher instances."""
    if SEARCHERS:
        return

    SEARCHERS["arxiv"] = ArxivSearcher()
    SEARCHERS["pubmed"] = PubMedSearcher()
    SEARCHERS["biorxiv"] = BioRxivSearcher()
    SEARCHERS["medrxiv"] = MedRxivSearcher()
    SEARCHERS["google_scholar"] = GoogleScholarSearcher()
    SEARCHERS["iacr"] = IACRSearcher()
    SEARCHERS["semantic"] = SemanticSearcher()
    SEARCHERS["crossref"] = CrossRefSearcher()
    SEARCHERS["openalex"] = OpenAlexSearcher()
    SEARCHERS["pmc"] = PMCSearcher()
    SEARCHERS["core"] = CORESearcher()
    SEARCHERS["europepmc"] = EuropePMCSearcher()
    SEARCHERS["dblp"] = DBLPSearcher()
    SEARCHERS["openaire"] = OpenAiresearcher()
    SEARCHERS["citeseerx"] = CiteSeerXSearcher()
    SEARCHERS["doaj"] = DOAJSearcher()
    SEARCHERS["base"] = BASESearcher()
    unpaywall_resolver = UnpaywallResolver()
    SEARCHERS["unpaywall"] = UnpaywallSearcher(resolver=unpaywall_resolver)
    SEARCHERS["zenodo"] = ZenodoSearcher()
    SEARCHERS["hal"] = HALSearcher()
    SEARCHERS["ssrn"] = SSRNSearcher()
    SEARCHERS["scihub"] = SciHubSource()

    # Optional paid connectors
    ieee_key = get_env("IEEE_API_KEY", "")
    if ieee_key:
        from .academic_platforms.ieee import IEEESearcher
        SEARCHERS["ieee"] = IEEESearcher()

    acm_key = get_env("ACM_API_KEY", "")
    if acm_key:
        from .academic_platforms.acm import ACMSearcher
        SEARCHERS["acm"] = ACMSearcher()


ALL_SOURCES = [
    "arxiv", "pubmed", "biorxiv", "medrxiv", "google_scholar", "iacr",
    "semantic", "crossref", "openalex", "pmc", "core", "europepmc",
    "dblp", "openaire", "citeseerx", "doaj", "base", "zenodo", "hal",
    "ssrn", "unpaywall",
]


def _parse_sources(sources: str) -> List[str]:
    if not sources or sources.strip().lower() == "all":
        return [s for s in ALL_SOURCES if s in SEARCHERS]
    normalized = [p.strip().lower() for p in sources.split(",") if p.strip()]
    return [s for s in normalized if s in SEARCHERS]


def _paper_unique_key(paper: Dict[str, Any]) -> str:
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    title = (paper.get("title") or "").strip().lower()
    authors = (paper.get("authors") or "").strip().lower()
    if title:
        return f"title:{title}|authors:{authors}"
    return f"id:{(paper.get('paper_id') or '').strip().lower()}"


def _dedupe(papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    out: list[Dict[str, Any]] = []
    for p in papers:
        k = _paper_unique_key(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

async def _async_search(searcher: Any, query: str, max_results: int, **kwargs) -> List[Dict]:
    if kwargs:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, **kwargs)
    else:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results)
    return [p.to_dict() for p in papers]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_search(args: argparse.Namespace) -> int:
    _init_searchers()
    selected = _parse_sources(args.sources)
    if not selected:
        print(json.dumps({"error": "No valid sources selected", "available": sorted(SEARCHERS.keys())}))
        return 1

    tasks = {}
    for src in selected:
        searcher = SEARCHERS[src]
        extra = {}
        if src == "semantic" and args.year:
            extra["year"] = args.year
        tasks[src] = _async_search(searcher, args.query, args.max_results, **extra)

    names = list(tasks.keys())
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    merged: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}
    source_counts: Dict[str, int] = {}

    for name, result in zip(names, results):
        if isinstance(result, Exception):
            errors[name] = str(result)
            source_counts[name] = 0
        else:
            source_counts[name] = len(result)
            for p in result:
                if not p.get("source"):
                    p["source"] = name
                merged.append(p)

    deduped = _dedupe(merged)

    output = {
        "query": args.query,
        "sources_used": names,
        "source_results": source_counts,
        "errors": errors,
        "total": len(deduped),
        "papers": deduped,
    }
    print(json.dumps(output, indent=2, default=str))
    return 0


async def cmd_download(args: argparse.Namespace) -> int:
    _init_searchers()
    source = args.source.strip().lower()

    if source not in SEARCHERS:
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(SEARCHERS.keys())}))
        return 1

    searcher = SEARCHERS[source]
    try:
        result = await asyncio.to_thread(searcher.download_pdf, args.paper_id, args.save_path)
        print(json.dumps({"status": "ok", "path": result}))
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1


async def cmd_read(args: argparse.Namespace) -> int:
    _init_searchers()
    source = args.source.strip().lower()

    if source not in SEARCHERS:
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(SEARCHERS.keys())}))
        return 1

    searcher = SEARCHERS[source]
    try:
        text = await asyncio.to_thread(searcher.read_paper, args.paper_id, args.save_path)
        print(text)
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1


async def cmd_sources(args: argparse.Namespace) -> int:
    _init_searchers()
    print(json.dumps({"sources": sorted(SEARCHERS.keys())}, indent=2))
    return 0


async def cmd_resolve(args: argparse.Namespace) -> int:
    """Resolve a title to its best CrossRef DOI candidate."""
    result = await asyncio.to_thread(resolve_title, args.title)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("error") else 0


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _bibtex_escape(text: str) -> str:
    """Escape special BibTeX characters in field values."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _bibtex_citekey(authors: str, year: str, title: str) -> str:
    """Build a clean BibTeX citekey: LastName + Year + FirstTitleWord."""
    # Extract first author's last name robustly
    first_entry = authors.split(";")[0].strip() if authors.strip() else ""
    if first_entry:
        if "," in first_entry:
            # "Smith, John" format
            last = first_entry.split(",")[0].strip()
        else:
            # "John Smith" or "Smith J" — take last space-separated token
            tokens = first_entry.split()
            # Prefer the longest token (usually the family name)
            last = max(tokens, key=len) if tokens else "Unknown"
    else:
        last = "Unknown"
    last = re.sub(r"[^a-zA-Z]", "", last) or "Unknown"

    first_word = re.sub(r"[^a-zA-Z]", "", title.split()[0]) if title.split() else "paper"
    return f"{last}{year}_{first_word}"


def _authors_to_bibtex(authors_str: str) -> str:
    """Convert semicolon-separated authors to BibTeX 'and'-separated format."""
    parts = [a.strip() for a in authors_str.split(";") if a.strip()]
    return " and ".join(parts)


def _paper_to_bibtex(p: Dict[str, Any]) -> str:
    """Convert a paper dict to a BibTeX entry."""
    authors = p.get("authors", "") or ""
    raw_date = p.get("published_date", "") or ""
    m = re.match(r"(\d{4})", str(raw_date))
    year = m.group(1) if m else ""
    title = p.get("title", "") or "untitled"
    doi = p.get("doi", "") or ""
    url = p.get("url", "") or ""
    abstract = _strip_html((p.get("abstract", "") or ""))[:300]
    source = p.get("source", "unknown")
    journal = p.get("journal", "") or ""

    citekey = _bibtex_citekey(authors, year, title)
    bibtex_authors = _authors_to_bibtex(authors)

    lines = [
        f"@article{{{citekey},",
        f"  author   = {{{_bibtex_escape(bibtex_authors)}}},",
        f"  title    = {{{_bibtex_escape(title)}}},",
        f"  year     = {{{year}}},",
    ]
    if journal:
        lines.append(f"  journal  = {{{_bibtex_escape(journal)}}},")
    if doi:
        lines.append(f"  doi      = {{{doi}}},")
    if url:
        lines.append(f"  url      = {{{url}}},")
    if abstract:
        lines.append(f"  abstract = {{{_bibtex_escape(abstract)}}},")
    lines.append(f"  note     = {{Retrieved via {source}}},")
    lines.append("}")
    return "\n".join(lines)


async def cmd_cite(args: argparse.Namespace) -> int:
    """Search and output BibTeX / RIS citations."""
    _init_searchers()
    selected = _parse_sources(args.sources)
    if not selected:
        print(json.dumps({"error": "No valid sources selected"}))
        return 1

    tasks = {src: _async_search(SEARCHERS[src], args.query, args.max_results)
             for src in selected}
    names = list(tasks.keys())
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    merged: List[Dict[str, Any]] = []
    for name, result in zip(names, results):
        if not isinstance(result, Exception):
            for p in result:
                if not p.get("source"):
                    p["source"] = name
                merged.append(p)
    deduped = _dedupe(merged)

    if args.format == "ris":
        entries = []
        for p in deduped:
            year = str(p.get("published_date", ""))[:4]
            au_lines = [
                f"AU  - {a.strip()}"
                for a in (p.get("authors", "") or "").split(";")
                if a.strip()
            ]
            entry = (
                ["TY  - JOUR", f"TI  - {p.get('title', '')}"]
                + au_lines
                + [
                    f"PY  - {year}",
                    f"DO  - {p.get('doi', '')}",
                    f"UR  - {p.get('url', '')}",
                    f"AB  - {_strip_html(p.get('abstract', '') or '')[:300]}",
                    "ER  -",
                    "",
                ]
            )
            entries.append("\n".join(entry))
        output_text = "\n".join(entries)
    else:
        lines = []
        for p in deduped:
            lines.append(_paper_to_bibtex(p))
            lines.append("")
        output_text = "\n".join(lines)

    if getattr(args, "output_file", None):
        out_path = Path(args.output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_text, encoding="utf-8")
        print(json.dumps({"status": "ok", "path": str(out_path), "count": len(deduped)}))
    else:
        print(output_text)

    return 0


# Fallback download: try sources in cascade order, stop on first success
FALLBACK_DOWNLOAD_ORDER = [
    "unpaywall", "core", "europepmc", "openalex", "semantic",
    "scihub",
]


def _looks_like_pdf_path(result: Any) -> bool:
    if not isinstance(result, str) or not result.lower().endswith(".pdf"):
        return False
    return Path(result).exists()


def _save_abstract_only_metadata(doi: str, save_path: str) -> str:
    metadata = metadata_for_identifier(doi)
    if not metadata:
        metadata = {"doi": doi, "url": f"https://doi.org/{doi}"}

    output_path = paper_output_path(
        save_path,
        title=metadata.get("title", ""),
        authors=metadata.get("authors", []),
        published_date=metadata.get("published_date", ""),
        identifier=doi,
        extension=".txt",
    )
    output_path.write_text(
        metadata_text(
            title=metadata.get("title", ""),
            authors=metadata.get("authors", []),
            abstract=metadata.get("abstract", ""),
            doi=metadata.get("doi", doi),
            url=metadata.get("url", f"https://doi.org/{doi}"),
        ),
        encoding="utf-8",
    )
    return str(output_path)


async def cmd_fallback(args: argparse.Namespace) -> int:
    """Try downloading a paper by DOI across sources in priority order."""
    _init_searchers()
    doi = args.doi.strip()
    save_path = args.save_path

    for source_name in FALLBACK_DOWNLOAD_ORDER:
        searcher = SEARCHERS.get(source_name)
        if not searcher:
            continue
        try:
            result = await asyncio.to_thread(searcher.download_pdf, doi, save_path)
            if _looks_like_pdf_path(result):
                print(json.dumps({"status": "ok", "source": source_name, "path": result}))
                return 0
        except NotImplementedError:
            continue
        except Exception as e:
            logging.debug(f"[fallback] {source_name} failed for {doi}: {e}")

    abstract_path = await asyncio.to_thread(_save_abstract_only_metadata, doi, save_path)
    print(json.dumps({"status": "abstract_only", "path": abstract_path}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    default_output = get_default_output_dir()
    parser = argparse.ArgumentParser(
        prog="paper-search",
        description="Search, download, and read academic papers from 20+ sources.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    p_search = sub.add_parser("search", help="Search for papers across academic platforms")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("-n", "--max-results", type=int, default=5, help="Max results per source (default: 5)")
    p_search.add_argument("-s", "--sources", default="all",
                          help="Comma-separated sources or 'all' (default: all)")
    p_search.add_argument("-y", "--year", default=None,
                          help="Year filter for Semantic Scholar (e.g. '2020', '2018-2022')")

    # download
    p_dl = sub.add_parser("download", help="Download a paper PDF")
    p_dl.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_dl.add_argument("paper_id", help="Paper identifier")
    p_dl.add_argument(
        "-o",
        "--save-path",
        default=default_output,
        help=f"Save directory (default: {default_output})",
    )

    # read
    p_read = sub.add_parser("read", help="Download and extract text from a paper")
    p_read.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_read.add_argument("paper_id", help="Paper identifier")
    p_read.add_argument(
        "-o",
        "--save-path",
        default=default_output,
        help=f"Save directory (default: {default_output})",
    )

    # sources
    sub.add_parser("sources", help="List available sources")

    # cite
    p_cite = sub.add_parser("cite", help="Search and export BibTeX or RIS citations")
    p_cite.add_argument("query", help="Search query")
    p_cite.add_argument("-n", "--max-results", type=int, default=5, help="Max results per source")
    p_cite.add_argument("-s", "--sources", default="europepmc,semantic,arxiv",
                        help="Comma-separated sources (default: europepmc,semantic,arxiv)")
    p_cite.add_argument("-f", "--format", default="bibtex", choices=["bibtex", "ris"],
                        help="Output format (default: bibtex)")
    p_cite.add_argument(
        "-o", "--output-file",
        default=None,
        help="Save citations to a file (e.g. refs.bib). Omit to print to stdout.",
    )

    # resolve
    p_resolve = sub.add_parser("resolve", help="Resolve a paper title to a DOI via CrossRef")
    p_resolve.add_argument("title", help="Paper title to resolve")

    # fallback
    p_fallback = sub.add_parser("fallback", help="Download a paper by DOI, cascading through all sources")
    p_fallback.add_argument("doi", help="DOI of the paper")
    p_fallback.add_argument(
        "-o",
        "--save-path",
        default=default_output,
        help=f"Save directory (default: {default_output})",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "search": cmd_search,
        "download": cmd_download,
        "read": cmd_read,
        "sources": cmd_sources,
        "cite": cmd_cite,
        "resolve": cmd_resolve,
        "fallback": cmd_fallback,
    }

    exit_code = asyncio.run(dispatch[args.command](args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
