#!/usr/bin/env python3
"""CLI interface for paper-search — search, download, and read academic papers."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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

METADATA_SOURCES = ["crossref", "openalex", "unpaywall"]


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


def _extract_dois(values: list[str]) -> list[str]:
    seen: set[str] = set()
    dois: list[str] = []
    for value in values:
        for match in DOI_RE.findall(value or ""):
            doi = match.rstrip(".,;)]}").lower()
            if doi and doi not in seen:
                seen.add(doi)
                dois.append(doi)
    return dois


def _paper_has_content(paper: Dict[str, Any], field: str) -> bool:
    value = paper.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _coerce_year(value: Any) -> Optional[int]:
    if value is None:
        return None
    if hasattr(value, "year"):
        try:
            return int(value.year)
        except (TypeError, ValueError):
            return None
    text = str(value)
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return int(match.group(0)) if match else None


def _score_recency(year: Optional[int]) -> int:
    if not year:
        return 0
    age = max(0, datetime.now(timezone.utc).year - year)
    if age <= 2:
        return 100
    if age <= 5:
        return 80
    if age <= 10:
        return 60
    if age <= 20:
        return 35
    return 15


def _score_metadata_record(merged: Dict[str, Any]) -> Dict[str, Any]:
    sources = merged.get("sources") or []
    oa_pdf_sources = sorted(merged.get("oa_pdf_sources") or [])
    citations = max(0, int(merged.get("citations") or 0))
    title = str(merged.get("title") or "")
    abstract = str(merged.get("abstract") or "")
    categories = str(merged.get("categories") or "")
    keywords = str(merged.get("keywords") or "")
    combined_text = " ".join([title, abstract, categories, keywords]).lower()

    source_coverage = min(100, len(sources) * 34)
    recency = _score_recency(_coerce_year(merged.get("published_date")))
    citation_signal = min(100, round(math.log10(citations + 1) / math.log10(1001) * 100)) if citations else 0
    availability = 100 if _paper_has_content(merged, "pdf_url") or oa_pdf_sources else (45 if _paper_has_content(merged, "url") else 0)

    metadata_confidence = 0
    metadata_confidence += 20 if _paper_has_content(merged, "title") else 0
    metadata_confidence += 15 if _paper_has_content(merged, "authors") else 0
    metadata_confidence += 25 if _paper_has_content(merged, "abstract") else 0
    metadata_confidence += 15 if _paper_has_content(merged, "published_date") else 0
    metadata_confidence += 10 if _paper_has_content(merged, "doi") else 0
    metadata_confidence += 15 if _paper_has_content(merged, "categories") or _paper_has_content(merged, "keywords") else 0
    metadata_confidence = min(100, metadata_confidence + min(20, len(sources) * 5))

    review_terms = ["systematic review", "meta-analysis", "metaanalysis", "review", "synthesis", "survey"]
    literature_fit = 45 if title else 20
    if abstract:
        literature_fit += 15
    if any(term in combined_text for term in review_terms):
        literature_fit += 40
    literature_fit = min(100, literature_fit)

    components = {
        "literature_fit": literature_fit,
        "recency": recency,
        "citation_signal": citation_signal,
        "availability": availability,
        "metadata_confidence": metadata_confidence,
    }
    rank_score = round(
        components["literature_fit"] * 0.25
        + components["recency"] * 0.20
        + components["citation_signal"] * 0.20
        + components["availability"] * 0.15
        + components["metadata_confidence"] * 0.20
    )

    reasons: list[str] = []
    if any(term in combined_text for term in review_terms):
        reasons.append("Review/synthesis keywords detected")
    if recency >= 80:
        reasons.append("Recent publication year")
    elif recency == 0:
        reasons.append("Publication year unavailable")
    if citations >= 100:
        reasons.append(f"Strong citation signal ({citations} citations)")
    elif citations > 0:
        reasons.append(f"Citation signal available ({citations} citations)")
    if availability == 100:
        if oa_pdf_sources:
            reasons.append(f"Open access PDF found via {', '.join(oa_pdf_sources)}")
        else:
            reasons.append("Open access PDF available")
    elif availability > 0:
        reasons.append("Landing page available")
    if len(sources) >= 2:
        reasons.append(f"Metadata confirmed by {len(sources)} sources")
    if metadata_confidence >= 80:
        reasons.append("Rich title/abstract metadata")

    return {
        "rank_score": max(0, min(100, rank_score)),
        "rank_reasons": reasons[:6],
        "rank_components": components,
        "source_coverage": source_coverage,
    }


def _merge_metadata_records(doi: str, records: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    source_priority = ["crossref", "openalex", "semantic", "unpaywall"]
    merged: Dict[str, Any] = {
        "doi": doi,
        "title": "",
        "authors": "",
        "abstract": "",
        "published_date": "",
        "url": f"https://doi.org/{doi}",
        "pdf_url": "",
        "citations": 0,
        "categories": "",
        "keywords": "",
        "sources": sorted(records.keys()),
        "records": records,
    }

    for field in ["title", "authors", "abstract", "published_date", "url", "pdf_url", "categories", "keywords"]:
        for source in source_priority:
            paper = records.get(source)
            if paper and _paper_has_content(paper, field):
                merged[field] = paper[field]
                break

    citations = []
    for paper in records.values():
        try:
            citations.append(int(paper.get("citations") or 0))
        except (TypeError, ValueError):
            pass
    if citations:
        merged["citations"] = max(citations)

    oa_sources = []
    for source, paper in records.items():
        if _paper_has_content(paper, "pdf_url"):
            oa_sources.append(source)
    merged["oa_pdf_sources"] = sorted(oa_sources)
    merged.update(_score_metadata_record(merged))
    return merged


def _lookup_doi_with_source(source: str, doi: str) -> Optional[Dict[str, Any]]:
    searcher = _get_searcher(source)
    paper = None

    if source == "crossref":
        paper = searcher.get_paper_by_doi(doi)
    elif source == "openalex":
        paper = searcher.get_paper_by_doi(doi)
    elif source == "unpaywall":
        paper = searcher.resolver.get_paper_by_doi(doi)
    elif source == "semantic":
        paper = searcher.get_paper_details(f"DOI:{doi}")
    else:
        results = searcher.search(doi, max_results=1)
        paper = results[0] if results else None

    return paper.to_dict() if paper else None


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

async def _async_search(searcher: Any, query: str, max_results: int, **kwargs) -> List[Dict]:
    if kwargs:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results, **kwargs)
    else:
        papers = await asyncio.to_thread(searcher.search, query, max_results=max_results)
    return [p.to_dict() for p in papers]


async def _with_timeout(coro: Any, timeout: float, label: str = "operation") -> Any:
    if timeout <= 0:
        return await coro
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"{label} timed out after {timeout:g}s") from exc


async def _lookup_doi_source_async(source: str, doi: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_lookup_doi_with_source, source, doi)


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
        tasks[src] = _with_timeout(
            _async_search(searcher, args.query, args.max_results, **extra),
            args.source_timeout,
            src,
        )

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


async def cmd_metadata_dois(args: argparse.Namespace) -> int:
    input_values = list(args.dois or [])
    if args.input:
        input_path = Path(args.input)
        input_values.extend(input_path.read_text().splitlines())

    dois = _extract_dois(input_values)
    if not dois:
        print(json.dumps({"error": "No DOI values found", "metadata": []}, indent=2))
        return 1

    sources = _parse_sources(args.sources)
    if args.sources == "metadata":
        sources = [s for s in METADATA_SOURCES if s in _available_sources()]
    if args.include_semantic or get_env("SEMANTIC_SCHOLAR_API_KEY", ""):
        if "semantic" in _available_sources() and "semantic" not in sources:
            sources.append("semantic")
    if not sources:
        print(json.dumps({"error": "No valid sources selected", "available": sorted(_available_sources())}, indent=2))
        return 1

    doi_outputs: list[Dict[str, Any]] = []
    for doi in dois:
        tasks = {
            source: _with_timeout(
                _lookup_doi_source_async(source, doi),
                args.source_timeout,
                f"{source}:{doi}",
            )
            for source in sources
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        records: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        for source, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                errors[source] = str(result)
            elif result:
                records[source] = result

        doi_outputs.append(
            {
                "doi": doi,
                "sources_used": list(tasks.keys()),
                "source_results": {source: source in records for source in tasks.keys()},
                "errors": errors,
                "metadata": _merge_metadata_records(doi, records) if records else None,
            }
        )

    output = {
        "dois": dois,
        "sources_used": sources,
        "total": len(doi_outputs),
        "results": doi_outputs,
    }

    text = json.dumps(output, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n")
        print(json.dumps({"status": "ok", "path": args.output, "total": len(doi_outputs)}, indent=2))
    else:
        print(text)
    return 0


async def cmd_download_doi(args: argparse.Namespace) -> int:
    from .server import download_with_fallback

    try:
        result = await download_with_fallback(
            source=args.source,
            paper_id=args.doi,
            doi=args.doi,
            title=args.title or "",
            save_path=args.save_path,
            use_scihub=not args.no_scihub,
            scihub_base_url=args.scihub_base_url,
        )
        status = "ok" if isinstance(result, str) and not result.lower().startswith("download failed") else "error"
        print(json.dumps({"status": status, "path": result if status == "ok" else "", "message": "" if status == "ok" else result}))
        return 0 if status == "ok" else 1
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
    p_search.add_argument("--source-timeout", type=float, default=12,
                          help="Seconds before an individual source search times out (0 disables)")
    p_search.add_argument("--exhaustive", action="store_true",
                          help="Use the old broad source set when sources are omitted or set to 'all'")

    # download
    p_dl = sub.add_parser("download", help="Download a paper PDF")
    p_dl.add_argument("source", help="Source platform (e.g. arxiv, semantic)")
    p_dl.add_argument("paper_id", help="Paper identifier")
    p_dl.add_argument("-o", "--save-path", default="./downloads", help="Save directory (default: ./downloads)")

    # download-doi
    p_dl_doi = sub.add_parser("download-doi", help="Download a DOI using source-native, OA, repository, and optional Sci-Hub fallback")
    p_dl_doi.add_argument("doi", help="DOI to download")
    p_dl_doi.add_argument("-o", "--save-path", default="./downloads", help="Save directory (default: ./downloads)")
    p_dl_doi.add_argument("--source", default="crossref", help="Primary source to try before fallbacks (default: crossref)")
    p_dl_doi.add_argument("--title", default="", help="Optional title for repository/Sci-Hub fallback")
    p_dl_doi.add_argument("--no-scihub", action="store_true", help="Disable Sci-Hub fallback")
    p_dl_doi.add_argument("--scihub-base-url", default="", help="Preferred Sci-Hub mirror")

    # metadata-dois
    p_meta = sub.add_parser("metadata-dois", help="Fetch and merge metadata for one or more DOIs")
    p_meta.add_argument("dois", nargs="*", help="DOIs or text containing DOIs")
    p_meta.add_argument("-i", "--input", help="Text file containing DOI values")
    p_meta.add_argument("-o", "--output", help="Write JSON output to this file")
    p_meta.add_argument("-s", "--sources", default="metadata",
                        help="Comma-separated sources or 'metadata' (default: metadata = crossref,openalex,unpaywall)")
    p_meta.add_argument("--include-semantic", action="store_true",
                        help="Include Semantic Scholar even without SEMANTIC_SCHOLAR_API_KEY")
    p_meta.add_argument("--source-timeout", type=float, default=12,
                        help="Seconds before an individual source lookup times out (0 disables)")

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
        "metadata-dois": cmd_metadata_dois,
        "download-doi": cmd_download_doi,
        "read": cmd_read,
        "sources": cmd_sources,
    }

    exit_code = asyncio.run(dispatch[args.command](args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
