#!/usr/bin/env python3
"""CLI interface for paper-search — search, download, and read academic papers."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
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
    if get_env("ACM_API_KEY", ""):
        sources.append("acm")
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
    elif source == "acm" and get_env("ACM_API_KEY", ""):
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

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)


def _fast_sources() -> list[str]:
    sources = list(FAST_SOURCES)
    if get_env("SEMANTIC_SCHOLAR_API_KEY", ""):
        sources.insert(2, "semantic")
    return sources


def _parse_sources(sources: str, exhaustive: bool = False) -> List[str]:
    if not sources:
        source_names = ALL_SOURCES if exhaustive else _fast_sources()
        available = set(_available_sources())
        return [s for s in source_names if s in available]

    sources = sources.strip().lower()
    if sources == "all":
        source_names = ALL_SOURCES if exhaustive else _fast_sources()
        available = set(_available_sources())
        return [s for s in source_names if s in available]
    if sources == "fast":
        available = set(_available_sources())
        return [s for s in _fast_sources() if s in available]
    if sources == "fastest":
        available = set(_available_sources())
        return [s for s in FASTEST_SOURCES if s in available]

    normalized = [p.strip().lower() for p in sources.split(",") if p.strip()]
    available = set(_available_sources())
    return [s for s in normalized if s in available]


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
    selected = _parse_sources(args.sources, args.exhaustive)
    if DOI_RE.search(args.query) and "unpaywall" not in selected and "unpaywall" in _available_sources():
        selected.append("unpaywall")
    if not selected:
        print(json.dumps({"error": "No valid sources selected", "available": sorted(_available_sources())}))
        return 1

    tasks = {}
    for src in selected:
        searcher = _get_searcher(src)
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
        "search_mode": "exhaustive" if args.exhaustive else "fast",
        "source_preset": args.sources,
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

    if source not in _available_sources():
        print(json.dumps({"error": f"Unknown source: {source}", "available": sorted(_available_sources())}))
        return 1

    searcher = _get_searcher(source)
    try:
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
    p_search.add_argument("-s", "--sources", default="fast",
                          help="Comma-separated sources, 'fastest', 'fast', or 'all' (default: fast)")
    p_search.add_argument("-y", "--year", default=None,
                          help="Year filter for Semantic Scholar (e.g. '2020', '2018-2022')")
    p_search.add_argument("--exhaustive", action="store_true",
                          help="Use the old broad source set when sources are omitted or set to 'all'")

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
