#!/usr/bin/env python3
"""CLI interface for paper-search — search, download, and read academic papers."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from .config import get_env
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

# ---------------------------------------------------------------------------
# Searcher registry
# ---------------------------------------------------------------------------

SEARCHERS: Dict[str, Any] = {}


def _available_sources() -> list[str]:
    sources = list(ALL_SOURCES)
    if get_env("IEEE_API_KEY", ""):
        sources.append("ieee")
    # Preserve main's keyless ACM availability, without broadening presets.
    sources.append("acm")
    sources.extend(["wos", "scopus"])
    return sources


def _get_searcher(source: str) -> Any:
    """Initialize only the searcher requested by the current command."""
    if source in SEARCHERS:
        return SEARCHERS[source]

    factories = {
        "arxiv": ArxivSearcher,
        "pubmed": PubMedSearcher,
        "biorxiv": BioRxivSearcher,
        "medrxiv": MedRxivSearcher,
        "google_scholar": GoogleScholarSearcher,
        "iacr": IACRSearcher,
        "semantic": SemanticSearcher,
        "crossref": CrossRefSearcher,
        "openalex": OpenAlexSearcher,
        "pmc": PMCSearcher,
        "core": CORESearcher,
        "europepmc": EuropePMCSearcher,
        "dblp": DBLPSearcher,
        "openaire": OpenAiresearcher,
        "citeseerx": CiteSeerXSearcher,
        "doaj": DOAJSearcher,
        "base": BASESearcher,
        "zenodo": ZenodoSearcher,
        "hal": HALSearcher,
        "ssrn": SSRNSearcher,
    }

    if source == "unpaywall":
        searcher = UnpaywallSearcher(resolver=UnpaywallResolver())
    elif source == "ieee" and get_env("IEEE_API_KEY", ""):
        from .academic_platforms.ieee import IEEESearcher
        searcher = IEEESearcher()
    elif source == "scopus":
        from .academic_platforms.scopus import ScopusSearcher
        searcher = ScopusSearcher()
    elif source == "wos":
        from .academic_platforms.wos import WebOfScienceSearcher
        searcher = WebOfScienceSearcher()
    elif source == "acm":
        from .academic_platforms.acm import ACMSearcher
        searcher = ACMSearcher()
    elif source in factories:
        searcher = factories[source]()
    else:
        raise KeyError(source)

    SEARCHERS[source] = searcher
    return searcher


ALL_SOURCES = [
    "arxiv", "pubmed", "biorxiv", "medrxiv", "google_scholar", "iacr",
    "semantic", "crossref", "openalex", "pmc", "core", "europepmc",
    "dblp", "openaire", "citeseerx", "doaj", "base", "zenodo", "hal",
    "ssrn", "unpaywall",
]

FASTEST_SOURCES = [
    "openalex", "crossref",
]

FAST_SOURCES = [
    "openalex", "crossref", "arxiv", "pubmed", "europepmc",
]


def _fast_sources() -> list[str]:
    sources = list(FAST_SOURCES)
    if get_env("SEMANTIC_SCHOLAR_API_KEY", "").strip():
        sources.insert(2, "semantic")
    return sources


def _parse_sources(sources: str) -> List[str]:
    """Resolve presets without narrowing or extending an explicit selection."""
    preset = sources.strip().lower() if sources else "all"
    if preset == "all":
        source_names = ALL_SOURCES
    elif preset == "fast":
        source_names = _fast_sources()
    elif preset == "fastest":
        source_names = FASTEST_SOURCES
    else:
        source_names = [part.strip() for part in preset.split(",") if part.strip()]
    available = set(_available_sources())
    # Avoid replacing an unawaited coroutine when the same source is repeated.
    return list(dict.fromkeys(source for source in source_names if source in available))


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


def _citation_sort_key(paper: Dict[str, Any]) -> tuple[bool, Decimal]:
    """Put valid nonnegative counts first, including numeric strings."""
    value = paper.get("citations")
    try:
        count = Decimal(str(value))
    except InvalidOperation:
        return False, Decimal(0)
    if not count.is_finite() or count < 0:
        return False, Decimal(0)
    return True, count


def _date_sort_key(paper: Dict[str, Any]) -> tuple[bool, datetime]:
    """Compare ISO dates/timestamps consistently; treat naive values as UTC."""
    value = paper.get("published_date")
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            parsed = datetime.combine(value, datetime.min.time())
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        else:
            raise ValueError("Missing or unsupported date")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return True, parsed
    except ValueError:
        return False, datetime.min.replace(tzinfo=timezone.utc)


def _sort_papers(papers: List[Dict[str, Any]], order: str) -> List[Dict[str, Any]]:
    """Sort retrieved papers stably, without changing connector queries."""
    if order == "citations":
        return sorted(papers, key=_citation_sort_key, reverse=True)
    if order == "date":
        return sorted(papers, key=_date_sort_key, reverse=True)
    return papers


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
    selected = _parse_sources(args.sources)
    if not selected:
        print(json.dumps({"error": "No valid sources selected", "available": sorted(_available_sources())}))
        return 1

    tasks = {}
    for src in selected:
        searcher = _get_searcher(src)
        extra = {}
        if src == "wos":
            extra["db"] = getattr(args, "wos_db", "WOS")
        if src == "semantic" and args.year:
            extra["year"] = args.year
        if src == "scopus":
            extra = {"view": getattr(args, "scopus_view", "STANDARD"),
                     "sort": getattr(args, "scopus_sort", "relevance"),
                     "field": getattr(args, "scopus_field", ""),
                     "date": getattr(args, "scopus_date", "")}
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

    deduped = _sort_papers(_dedupe(merged), getattr(args, "sort", "relevance"))

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
    source = args.source.strip().lower()

    if source not in _available_sources():
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(_available_sources())}))
        return 1

    searcher = _get_searcher(source)
    try:
        result = await asyncio.to_thread(searcher.download_pdf, args.paper_id, args.save_path)
        print(json.dumps({"status": "ok", "path": result}))
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1


async def cmd_read(args: argparse.Namespace) -> int:
    source = args.source.strip().lower()
    if getattr(args, "full_text", False) and source != "scopus":
        print(json.dumps({"status": "error", "message": "--full-text is supported only for Scopus"}))
        return 1

    if source not in _available_sources():
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(_available_sources())}))
        return 1

    searcher = _get_searcher(source)
    try:
        if source == "scopus":
            result = await asyncio.to_thread(searcher.read_paper, args.paper_id, args.save_path,
                                             full_text=getattr(args, "full_text", False))
            print(json.dumps(result, indent=2))
            if result["status"] == "unavailable":
                return 1
            return 2 if getattr(args, "full_text", False) and result["status"] != "full_text" else 0
        text = await asyncio.to_thread(searcher.read_paper, args.paper_id, args.save_path)
        print(text)
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        return 1


async def cmd_sources(args: argparse.Namespace) -> int:
    print(json.dumps({"sources": sorted(_available_sources())}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
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
                          help="Comma-separated sources, 'fastest', 'fast', or 'all' (default: all)")
    p_search.add_argument("-y", "--year", default=None,
                          help="Year filter for Semantic Scholar (e.g. '2020', '2018-2022')")
    p_search.add_argument("--exhaustive", action="store_true",
                          help="Compatibility no-op: broad search is already the default; "
                               "explicit --sources always takes precedence")

    p_search.add_argument("--sort", choices=("relevance", "citations", "date"),
                          default="relevance",
                          help="Order retrieved results: relevance preserves source order (default); "
                               "citations sorts highest first; date sorts newest first")

    p_search.add_argument("--scopus-view", choices=("STANDARD", "COMPLETE"), default="STANDARD",
                          help="Scopus metadata view (COMPLETE may require institutional entitlement)")
    p_search.add_argument("--scopus-sort", choices=("relevance", "coverDate", "citedby-count", "creator"), default="relevance")
    p_search.add_argument("--scopus-field", choices=("", "TITLE", "ABS", "KEY", "AUTH", "AFFILORG"), default="")
    p_search.add_argument("--scopus-date", default="", help="Scopus YYYY or YYYY-YYYY date filter")

    p_search.add_argument("--wos-db", default="WOS",
                          choices=("BCI", "BIOABS", "BIOSIS", "CCC", "DIIDW", "DRCI", "MEDLINE", "PPRN", "RC", "WOK", "WOS", "ZOOREC"),
                          help="Web of Science database (default: WOS)")

    # download
    p_dl = sub.add_parser("download", help="Download a paper PDF")
    p_dl.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_dl.add_argument("paper_id", help="Paper identifier")
    p_dl.add_argument("-o", "--save-path", default="./downloads", help="Save directory (default: ./downloads)")

    # read
    p_read = sub.add_parser("read", help="Download and extract text from a paper")
    p_read.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_read.add_argument("paper_id", help="Paper identifier")
    p_read.add_argument("-o", "--save-path", default="./downloads", help="Save directory (default: ./downloads)")

    p_read.add_argument("--full-text", action="store_true",
                        help="For Scopus only: explicitly try identity-verified ScienceDirect full text")

    # sources
    sub.add_parser("sources", help="List available sources")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "search": cmd_search,
        "download": cmd_download,
        "read": cmd_read,
        "sources": cmd_sources,
    }

    exit_code = asyncio.run(dispatch[args.command](args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
