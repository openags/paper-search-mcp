"""Bounded, one-hop OpenAlex lookups. Never resolves ambiguous titles."""
from dataclasses import dataclass, field
import math
import re
import time
from urllib.parse import quote, unquote, urljoin, urlsplit

import requests


class OpenAlexRelationError(RuntimeError):
    """An upstream failure, distinct from a successful empty relationship set."""


def normalize_identifier(identifier: str) -> str:
    if not isinstance(identifier, str):
        raise ValueError("identifier must be a DOI or OpenAlex work ID")
    value = identifier.strip()
    if value.lower().startswith(("https://", "http://")):
        url = urlsplit(value)
        if url.netloc.lower() not in {"doi.org", "dx.doi.org", "openalex.org"}:
            raise ValueError("identifier URL must be from doi.org or openalex.org")
        if url.query or url.fragment:
            raise ValueError("identifier URL must not contain a query or fragment")
        value = unquote(url.path.lstrip("/"))
        if url.netloc.lower() == "openalex.org":
            if not re.fullmatch(r"W\d+", value, re.IGNORECASE):
                raise ValueError("expected an OpenAlex work ID such as W2741809807")
    elif value.lower().startswith("doi:"):
        value = value[4:].strip()
    if re.fullmatch(r"W\d+", value, re.IGNORECASE):
        return value.upper()
    if re.fullmatch(r"10\.\d{4,9}/[^\s\x00-\x1f\x7f]+", value):
        return "doi:" + value.lower()
    raise ValueError("use a DOI or OpenAlex work ID; paper titles are ambiguous")


def validate_options(max_results, max_pages, max_requests, timeout_seconds):
    for name, value, minimum, maximum in (
        ("max_results", max_results, 1, 500),
        ("max_pages", max_pages, 1, 5),
        ("max_requests", max_requests, 2, 8),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60):
        raise ValueError("timeout_seconds must be greater than 0 and at most 60")


@dataclass
class _Budget:
    max_requests: int
    seconds: float
    requests_made: int = 0
    deadline: float = field(init=False)

    def __post_init__(self):
        self.deadline = time.monotonic() + self.seconds

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise OpenAlexRelationError("OpenAlex lookup exceeded its time budget")
        return remaining

    def request_json(self, searcher, url, params=None):
        while True:
            remaining = self.remaining()
            if self.requests_made >= self.max_requests:
                raise OpenAlexRelationError("OpenAlex lookup exhausted its request budget")
            self.requests_made += 1
            try:
                # Redirects count against the same budget. Never send credentials
                # to an upstream-supplied external redirect target.
                response = searcher.session.get(
                    url, params=params, timeout=min(10.0, remaining),
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise OpenAlexRelationError("OpenAlex lookup failed: network/timeout error") from exc
            try:
                self.remaining()
                if response.status_code in (301, 302, 307, 308):
                    target = urljoin(url, response.headers.get("Location", ""))
                    parts = urlsplit(target)
                    if (parts.scheme != "https" or parts.netloc != "api.openalex.org"
                            or not re.fullmatch(r"/works/W\d+", parts.path)
                            or parts.query or parts.fragment):
                        raise OpenAlexRelationError("OpenAlex returned an unsafe or unsupported redirect")
                    url = target
                    continue
                if response.status_code == 404:
                    raise OpenAlexRelationError("OpenAlex work was not found (HTTP 404)")
                if response.status_code != 200:
                    # Avoid echoing response bodies or exception URLs with secrets.
                    raise OpenAlexRelationError(
                        f"OpenAlex lookup failed (HTTP {response.status_code}); "
                        "check API access, key, or rate/request budget"
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise OpenAlexRelationError("OpenAlex returned invalid JSON") from exc
                self.remaining()
                if not isinstance(data, dict):
                    raise OpenAlexRelationError("OpenAlex returned an invalid response object")
                return data
            finally:
                response.close()


def related_works(searcher, identifier: str, edge: str, max_results: int = 10, *,
                  filter: str = "", max_pages: int = 5, max_requests: int = 8,
                  timeout_seconds: float = 30.0) -> dict:
    """Fetch at most five pages, including an explicit seed existence lookup.

    No automatic retries: 429/auth/budget errors are surfaced immediately. The
    MCP wrapper enforces a wall-clock timeout; a synchronous caller also gets
    per-request socket timeouts and deadline checks between/after requests.
    """
    validate_options(max_results, max_pages, max_requests, timeout_seconds)
    normalized = normalize_identifier(identifier)
    if edge not in {"cites", "cited_by"}:
        raise ValueError("unsupported relationship")
    if not isinstance(filter, str):
        raise ValueError("filter must be a string")
    budget = _Budget(max_requests, timeout_seconds)
    seed = budget.request_json(
        searcher, f"{searcher.BASE_URL}/{quote(normalized, safe=':')}",
        {"select": "id"},
    )
    try:
        work_id = normalize_identifier(seed.get("id"))
        if not work_id.startswith("W"):
            raise ValueError("not a work ID")
    except ValueError as exc:
        raise OpenAlexRelationError("OpenAlex seed response has no valid work ID") from exc

    relation_filter = f"{edge}:{work_id}"
    if filter.strip():
        relation_filter += "," + filter.strip()
    page_size = min(max_results, 100)
    papers, seen = [], set()
    scanned = pages = skipped = 0
    total_available = 0
    stop_reason = "complete"
    for page in range(1, max_pages + 1):
        budget.remaining()
        if budget.requests_made >= max_requests:
            stop_reason = "request_budget"
            break
        data = budget.request_json(searcher, searcher.BASE_URL, {
            "filter": relation_filter, "sort": "cited_by_count:desc",
            "per_page": page_size, "page": page,
        })
        results = data.get("results")
        meta = data.get("meta")
        count = meta.get("count") if isinstance(meta, dict) else None
        if (not isinstance(results, list) or isinstance(count, bool)
                or not isinstance(count, int) or count < 0 or len(results) > page_size):
            raise OpenAlexRelationError("OpenAlex returned an invalid relationship page")
        pages += 1
        total_available = count
        for item in results:
            scanned += 1
            try:
                paper = searcher._parse_work(item)
            except (TypeError, ValueError, AttributeError) as exc:
                raise OpenAlexRelationError("OpenAlex returned a malformed work") from exc
            if paper is None or not paper.paper_id or paper.paper_id in seen:
                skipped += 1
                continue
            seen.add(paper.paper_id)
            papers.append(paper.to_dict())
            if len(papers) == max_results:
                break
        if not results or scanned >= count:
            break
        if len(papers) >= max_results:
            stop_reason = "max_results"
            break
    else:
        stop_reason = "page_budget"
    budget.remaining()
    return {
        "work_id": work_id,
        "direction": "citing" if edge == "cites" else "referenced",
        "papers": papers, "total": len(papers), "total_available": total_available,
        "truncated": stop_reason != "complete", "stop_reason": stop_reason,
        "pages_fetched": pages, "requests_made": budget.requests_made,
        "skipped_results": skipped,
    }
