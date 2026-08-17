# tests/test_openalex_graph.py
"""Tests for the key-less citation-graph and open-access lookups on OpenAlex.

Two layers:
  * Offline tests swap the requests.Session at the HTTP boundary for a recording fake,
    so identifier handling, ranking, truncation and error paths are deterministic.
  * Live tests hit the real API. Their expected values are independently sourced
    (well-known papers) and asserted as lower bounds, since citation counts only grow.
"""
import asyncio
import unittest
from unittest.mock import patch

import requests

from paper_search_mcp import server
from paper_search_mcp.academic_platforms import openalex
from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher


def check_api_accessible():
    """Can the live suite actually run?

    OpenAlex meters usage against a daily budget that resets at midnight UTC. Single
    entity lookups are unmetered but filter and search calls are not, so reachability
    alone is not enough -- with an exhausted budget the metered tests would all fail
    rather than skip. Ask via an unmetered lookup and read the remaining budget off the
    response headers.
    """
    try:
        response = requests.get(
            "https://api.openalex.org/works/doi:10.1038/nature14539",
            headers={"User-Agent": "paper-search-mcp/1.0 (tests)"},
            timeout=15,
        )
    except Exception:
        return False

    if response.status_code != 200:
        return False

    try:
        return float(response.headers["X-RateLimit-Remaining-USD"]) > 0
    except (KeyError, TypeError, ValueError):
        # No budget header: nothing to rule the suite out, so let it run.
        return True


def make_work(work_id, title="A Title", cited=0, refs=None,
              primary_pdf=None, best_oa_pdf=None, oa_url=None):
    """Build a minimal OpenAlex work object shaped like the real API response."""
    return {
        "id": f"https://openalex.org/{work_id}",
        "title": title,
        "publication_date": "2020-01-01",
        "cited_by_count": cited,
        "referenced_works": [f"https://openalex.org/{r}" for r in (refs or [])],
        "primary_location": {"landing_page_url": f"https://example.org/{work_id}",
                             "pdf_url": primary_pdf},
        "best_oa_location": {"pdf_url": best_oa_pdf} if best_oa_pdf else None,
        "open_access": {"is_oa": bool(oa_url), "oa_url": oa_url},
        "authorships": [{"author": {"display_name": "A. Author"}}],
        "concepts": [{"display_name": "Topic"}],
        "doi": "https://doi.org/10.1234/abc",
    }


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, headers=None, text=""):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _RecordingSession:
    """Stands in for requests.Session at the HTTP boundary and records every call."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}))
        return self._handler(url, params or {})

    def filters(self):
        """Every 'filter' param sent, in order."""
        return [p.get("filter") for _, p in self.calls if p.get("filter")]


def searcher_with(handler):
    searcher = OpenAlexSearcher()
    searcher.session = _RecordingSession(handler)
    return searcher


class TestOpenAlexGraphOffline(unittest.TestCase):
    """Deterministic tests -- no network."""

    # --- identifier handling -------------------------------------------------

    def test_bare_work_id_is_used_without_a_lookup(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        searcher.get_citations("W123")

        self.assertEqual(len(searcher.session.calls), 1,
                         "a bare work id should not need a resolution request")
        self.assertEqual(searcher.session.filters(), ["cites:W123"])

    def test_all_doi_spellings_resolve_identically(self):
        def handler(url, params):
            if url.endswith("doi:10.1234/abc"):
                return _FakeResponse(make_work("W555"))
            return _FakeResponse({"results": []})

        for spelling in ("10.1234/abc", "https://doi.org/10.1234/abc",
                         "http://doi.org/10.1234/abc", "doi:10.1234/abc"):
            searcher = searcher_with(handler)
            searcher.get_citations(spelling)
            self.assertEqual(searcher.session.filters(), ["cites:W555"],
                             f"{spelling!r} should resolve to the same work")

    def test_openalex_url_is_accepted(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        searcher.get_citations("https://openalex.org/W123")
        self.assertEqual(searcher.session.filters(), ["cites:W123"])

    def test_title_is_matched_against_the_title_field_first(self):
        """A plain `search` also matches abstracts, so it can land on a citing paper."""
        def handler(url, params):
            if str(params.get("filter", "")).startswith("title.search:"):
                return _FakeResponse({"results": [make_work("W777")]})
            return _FakeResponse({"results": []})

        searcher = searcher_with(handler)
        searcher.get_citations("Attention Is All You Need")

        self.assertEqual(searcher.session.calls[0][1].get("filter"),
                         "title.search:Attention Is All You Need")
        self.assertEqual(searcher.session.filters()[-1], "cites:W777")

    def test_title_falls_back_to_full_text_search(self):
        def handler(url, params):
            if params.get("search"):
                return _FakeResponse({"results": [make_work("W888")]})
            return _FakeResponse({"results": []})   # title.search finds nothing

        searcher = searcher_with(handler)
        searcher.get_citations("some paper only findable by abstract")

        self.assertEqual(searcher.session.filters()[-1], "cites:W888")

    def test_unresolvable_identifier_returns_empty_list(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        self.assertEqual(searcher.get_citations("no such paper anywhere"), [])
        self.assertIsNone(searcher.resolve_oa_pdf("no such paper anywhere"))

    def test_blank_identifier_makes_no_request(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        self.assertEqual(searcher.get_citations("   "), [])
        self.assertEqual(searcher.session.calls, [])

    # --- citations -----------------------------------------------------------

    def test_citations_are_requested_most_cited_first(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        searcher.get_citations("W1")
        self.assertEqual(searcher.session.calls[0][1].get("sort"), "cited_by_count:desc")

    def test_citations_respect_max_results(self):
        works = [make_work(f"W{i}", cited=i) for i in range(20)]
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": works}))

        papers = searcher.get_citations("W1", max_results=3)
        self.assertEqual(len(papers), 3)

    def test_citations_skip_untitled_records(self):
        broken = make_work("W9")
        broken["title"] = None
        searcher = searcher_with(
            lambda url, p: _FakeResponse({"results": [broken, make_work("W8", "Real")]})
        )
        papers = searcher.get_citations("W1")

        self.assertEqual([p.title for p in papers], ["Real"])

    # --- references ----------------------------------------------------------

    def test_references_use_the_cited_by_edge(self):
        """The whole reference set must be ranked by OpenAlex, not a client-side batch.

        Fetching the work and OR-batching its referenced_works ids caps the ranking at
        one page, so a paper with hundreds of references reports the most-cited of an
        arbitrary slice as if it were the most-cited overall.
        """
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        searcher.get_references("W1")

        self.assertEqual(searcher.session.filters(), ["cited_by:W1"])
        self.assertEqual(len(searcher.session.calls), 1,
                         "a resolved id needs exactly one request, not fetch-then-batch")
        self.assertEqual(searcher.session.calls[0][1].get("sort"), "cited_by_count:desc")

    def test_references_empty_when_publisher_deposited_none(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        self.assertEqual(searcher.get_references("W1"), [])

    # --- open access ---------------------------------------------------------

    def test_oa_pdf_falls_back_to_best_oa_location(self):
        work = make_work("W1", primary_pdf=None, best_oa_pdf="https://repo.org/paper.pdf")
        searcher = searcher_with(lambda url, p: _FakeResponse(work))

        paper = searcher.resolve_oa_pdf("W1")
        self.assertEqual(paper.pdf_url, "https://repo.org/paper.pdf")

    def test_oa_pdf_prefers_primary_location_when_present(self):
        work = make_work("W1", primary_pdf="https://publisher.org/paper.pdf",
                         best_oa_pdf="https://repo.org/paper.pdf")
        searcher = searcher_with(lambda url, p: _FakeResponse(work))

        self.assertEqual(searcher.resolve_oa_pdf("W1").pdf_url,
                         "https://publisher.org/paper.pdf")

    def test_openness_is_tracked_separately_from_having_a_pdf(self):
        """OpenAlex reports is_oa=True for papers whose only link is a landing page."""
        work = make_work("W1", oa_url="https://publisher.org/landing")
        work["best_oa_location"] = None
        searcher = searcher_with(lambda url, p: _FakeResponse(work))

        paper = searcher.resolve_oa_pdf("W1")
        self.assertTrue(paper.extra["is_oa"], "OpenAlex's own verdict must be preserved")
        self.assertFalse(paper.extra["pdf_is_direct"],
                         "oa_url is a best-free-link, not an asserted PDF")
        self.assertEqual(paper.pdf_url, "https://publisher.org/landing")

    def test_asserted_pdf_fields_are_marked_direct(self):
        work = make_work("W1", best_oa_pdf="https://repo.org/paper.pdf")
        searcher = searcher_with(lambda url, p: _FakeResponse(work))

        paper = searcher.resolve_oa_pdf("W1")
        self.assertTrue(paper.extra["pdf_is_direct"])

    def test_oa_pdf_empty_when_no_open_copy_exists(self):
        searcher = searcher_with(lambda url, p: _FakeResponse(make_work("W1")))

        paper = searcher.resolve_oa_pdf("W1")
        self.assertIsNotNone(paper, "a closed-access paper still resolves")
        self.assertFalse(paper.extra["is_oa"])
        self.assertEqual(paper.pdf_url, "")

    # --- parsing and failure handling ---------------------------------------

    def test_search_exposes_citations_and_reference_ids(self):
        work = make_work("W1", cited=42, refs=["W2", "W3"])
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": [work]}))

        paper = searcher.search("anything")[0]
        self.assertEqual(paper.citations, 42)
        self.assertEqual(paper.references, ["W2", "W3"])

    def test_json_nulls_do_not_crash_the_parser(self):
        """OpenAlex sends JSON null for absent fields, so .get(k, default) yields None."""
        nulled = make_work("W1")
        for key in ("authorships", "concepts", "open_access", "primary_location",
                    "best_oa_location", "referenced_works"):
            nulled[key] = None
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": [nulled]}))

        papers = searcher.search("anything")
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].authors, [])
        self.assertEqual(papers[0].references, [])
        self.assertEqual(papers[0].pdf_url, "")

    def test_null_results_list_does_not_crash(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": None}))
        self.assertEqual(searcher.search("anything"), [])
        self.assertEqual(searcher.get_citations("W1"), [])

    def test_rate_limiting_is_retried_rather_than_read_as_no_results(self):
        """A silent [] on 429 is indistinguishable from 'no such paper'."""
        attempts = []

        def handler(url, params):
            attempts.append(url)
            if len(attempts) < 3:
                return _FakeResponse({}, status_code=429)
            return _FakeResponse({"results": [make_work("W1", "Recovered")]})

        searcher = searcher_with(handler)
        with patch.object(openalex, "time") as fake_time:
            papers = searcher.search("anything")

        self.assertEqual([p.title for p in papers], ["Recovered"])
        self.assertEqual(len(attempts), 3)
        self.assertTrue(fake_time.sleep.called, "must back off between retries")

    def test_retry_budget_is_finite(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({}, status_code=429))
        with patch.object(openalex, "time"):
            self.assertEqual(searcher.search("anything"), [])

        self.assertEqual(len(searcher.session.calls), openalex._MAX_RETRIES)

    def test_exhausted_budget_is_not_retried(self):
        """OpenAlex meters usage; an exhausted budget won't clear inside a backoff."""
        budget_429 = _FakeResponse(
            {}, status_code=429,
            text='{"error":"Rate limit exceeded","message":"Insufficient budget. '
                 'This request costs $0.001 but you only have $0 remaining."}',
        )
        searcher = searcher_with(lambda url, p: budget_429)
        with patch.object(openalex, "time") as fake_time:
            self.assertEqual(searcher.search("anything"), [])

        self.assertEqual(len(searcher.session.calls), 1, "budget errors are not transient")
        self.assertFalse(fake_time.sleep.called, "must not sleep on an exhausted budget")

    def test_retry_after_header_is_honoured(self):
        searcher = searcher_with(
            lambda url, p: _FakeResponse({}, status_code=429, headers={"Retry-After": "7"})
        )
        with patch.object(openalex, "time") as fake_time:
            searcher.search("anything")

        self.assertEqual(fake_time.sleep.call_args_list[0].args[0], 7.0)

    def test_server_errors_are_retried_but_client_errors_are_not(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({}, status_code=503))
        with patch.object(openalex, "time"):
            self.assertEqual(searcher.get_citations("W1"), [])
        self.assertEqual(len(searcher.session.calls), openalex._MAX_RETRIES)

        searcher = searcher_with(lambda url, p: _FakeResponse({}, status_code=404))
        self.assertEqual(searcher.get_citations("W1"), [])
        self.assertEqual(len(searcher.session.calls), 1, "404 is final, not transient")

    def test_http_error_returns_empty_instead_of_raising(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({}, status_code=400))
        self.assertEqual(searcher.get_citations("W1"), [])
        self.assertEqual(searcher.get_references("W1"), [])
        self.assertIsNone(searcher.resolve_oa_pdf("W1"))
        self.assertEqual(searcher.search("anything"), [])

    def test_network_exception_returns_empty_instead_of_raising(self):
        def boom(url, params):
            raise requests.ConnectionError("network down")

        searcher = searcher_with(boom)
        self.assertEqual(searcher.get_citations("W1"), [])
        self.assertEqual(searcher.search("anything"), [])

    def test_non_json_body_returns_empty_instead_of_raising(self):
        searcher = searcher_with(lambda url, p: _FakeResponse(ValueError("not json")))
        self.assertEqual(searcher.get_citations("W1"), [])


class TestCitationCountsByDoi(unittest.TestCase):
    """Citation counts for sources that don't publish any."""

    @staticmethod
    def _counts_response(dois):
        return _FakeResponse({"results": [
            {"doi": f"https://doi.org/{d}", "cited_by_count": i * 10}
            for i, d in enumerate(dois, start=1)
        ]})

    def test_counts_are_keyed_by_bare_lowercased_doi(self):
        searcher = searcher_with(
            lambda url, p: self._counts_response(["10.1/a", "10.2/b"])
        )
        counts = searcher.citation_counts_by_doi(["10.1/A", "https://doi.org/10.2/b"])

        self.assertEqual(counts, {"10.1/a": 10, "10.2/b": 20})

    def test_duplicate_and_non_doi_inputs_are_dropped(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        searcher.citation_counts_by_doi(
            ["10.1/a", "10.1/a", "", "some paper title", "W123"]
        )

        sent = searcher.session.filters()[0].split(":", 1)[1]
        self.assertEqual(sent, "10.1/a")

    def test_requests_are_batched_at_the_hundred_value_ceiling(self):
        """OpenAlex 400s an OR-filter above 100 values (verified against the live API)."""
        dois = [f"10.1/{i}" for i in range(250)]
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        searcher.citation_counts_by_doi(dois)

        batch_sizes = [len(f.split(":", 1)[1].split("|")) for f in searcher.session.filters()]
        self.assertEqual(batch_sizes, [100, 100, 50])

    def test_no_request_when_nothing_looks_like_a_doi(self):
        searcher = searcher_with(lambda url, p: _FakeResponse({"results": []}))
        self.assertEqual(searcher.citation_counts_by_doi(["not a doi", ""]), {})
        self.assertEqual(searcher.session.calls, [])

    def test_unknown_dois_are_simply_absent(self):
        searcher = searcher_with(lambda url, p: self._counts_response(["10.1/a"]))
        counts = searcher.citation_counts_by_doi(["10.1/a", "10.9/missing"])

        self.assertIn("10.1/a", counts)
        self.assertNotIn("10.9/missing", counts)


class TestCitationGraphTools(unittest.TestCase):
    """The MCP tools, with the searcher mocked -- no network."""

    def test_get_citing_papers_returns_dicts(self):
        papers = [OpenAlexSearcher()._parse_work(make_work("W1", "Citing work", cited=7))]
        with patch.object(server.openalex_searcher, "get_citations", return_value=papers):
            result = asyncio.run(server.get_citing_papers("10.1234/abc", max_results=5))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Citing work")
        self.assertEqual(result[0]["citations"], 7)
        self.assertEqual(result[0]["source"], "openalex")

    def test_get_referenced_papers_returns_empty_list_when_none_deposited(self):
        with patch.object(server.openalex_searcher, "get_references", return_value=[]):
            result = asyncio.run(server.get_referenced_papers("W1"))
        self.assertEqual(result, [])

    def test_find_open_access_pdf_reports_both_openness_flags(self):
        work = make_work("W1", best_oa_pdf="https://repo.org/paper.pdf", oa_url="x")
        paper = OpenAlexSearcher()._parse_work(work)
        with patch.object(server.openalex_searcher, "resolve_oa_pdf", return_value=paper):
            result = asyncio.run(server.find_open_access_pdf("10.1234/abc"))

        self.assertEqual(result["pdf_url"], "https://repo.org/paper.pdf")
        self.assertTrue(result["is_open_access"])
        self.assertTrue(result["has_direct_pdf"])
        self.assertNotIn("error", result)

    def test_find_open_access_pdf_marks_a_landing_page_as_not_direct(self):
        work = make_work("W1", oa_url="https://publisher.org/landing")
        work["best_oa_location"] = None
        paper = OpenAlexSearcher()._parse_work(work)
        with patch.object(server.openalex_searcher, "resolve_oa_pdf", return_value=paper):
            result = asyncio.run(server.find_open_access_pdf("10.1234/abc"))

        self.assertTrue(result["is_open_access"])
        self.assertFalse(result["has_direct_pdf"])

    def test_find_open_access_pdf_reports_an_unresolvable_paper(self):
        with patch.object(server.openalex_searcher, "resolve_oa_pdf", return_value=None):
            result = asyncio.run(server.find_open_access_pdf("no such paper"))
        self.assertIn("error", result)


class TestCitationEnrichment(unittest.TestCase):
    """Backfilling citation counts onto aggregate search results."""

    def test_only_papers_without_a_count_are_looked_up(self):
        papers = [
            {"doi": "10.1/a", "citations": 0},
            {"doi": "10.2/b", "citations": 5},     # already has one
            {"doi": "", "citations": 0},           # nothing to look up by
        ]
        with patch.object(server.openalex_searcher, "citation_counts_by_doi",
                          return_value={"10.1/a": 99}) as lookup:
            enriched = asyncio.run(server._add_missing_citation_counts(papers))

        lookup.assert_called_once_with(["10.1/a"])
        self.assertEqual(enriched, 1)
        self.assertEqual([p["citations"] for p in papers], [99, 5, 0])

    def test_dois_are_matched_regardless_of_spelling(self):
        papers = [{"doi": "https://doi.org/10.1/A", "citations": 0}]
        with patch.object(server.openalex_searcher, "citation_counts_by_doi",
                          return_value={"10.1/a": 12}):
            asyncio.run(server._add_missing_citation_counts(papers))

        self.assertEqual(papers[0]["citations"], 12)

    def test_no_lookup_when_nothing_is_missing(self):
        papers = [{"doi": "10.1/a", "citations": 3}]
        with patch.object(server.openalex_searcher, "citation_counts_by_doi") as lookup:
            enriched = asyncio.run(server._add_missing_citation_counts(papers))

        lookup.assert_not_called()
        self.assertEqual(enriched, 0)

    def test_unknown_dois_leave_the_paper_untouched(self):
        papers = [{"doi": "10.9/missing", "citations": 0}]
        with patch.object(server.openalex_searcher, "citation_counts_by_doi",
                          return_value={}):
            enriched = asyncio.run(server._add_missing_citation_counts(papers))

        self.assertEqual(enriched, 0)
        self.assertEqual(papers[0]["citations"], 0)


class TestOpenAlexGraphLive(unittest.TestCase):
    """Live API tests. Expectations are lower bounds on well-known papers."""

    # LeCun, Bengio & Hinton, "Deep learning", Nature 521 (2015) -- tens of
    # thousands of citations, so any lower bound here is safe for years.
    DEEP_LEARNING_DOI = "10.1038/nature14539"

    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible()
        if not cls.api_accessible:
            print("\nWarning: OpenAlex is unreachable or its daily budget is spent "
                  "(resets midnight UTC); live tests will be skipped")

    def setUp(self):
        if not self.api_accessible:
            self.skipTest("OpenAlex unreachable or daily budget exhausted")
        self.searcher = OpenAlexSearcher()

    def test_citing_papers_are_found_and_ranked(self):
        papers = self.searcher.get_citations(self.DEEP_LEARNING_DOI, max_results=10)

        self.assertGreaterEqual(len(papers), 5, "a landmark paper must have citing works")
        counts = [p.citations for p in papers]
        self.assertEqual(counts, sorted(counts, reverse=True),
                         "citing papers should come back most-cited first")
        for paper in papers:
            self.assertTrue(paper.title)
            self.assertEqual(paper.source, "openalex")

    def test_doi_and_title_resolve_to_the_same_work(self):
        by_doi = self.searcher.resolve_oa_pdf(self.DEEP_LEARNING_DOI)
        self.assertIsNotNone(by_doi)
        self.assertIn("deep learning", by_doi.title.lower())

        by_title = self.searcher.resolve_oa_pdf(by_doi.title)
        self.assertIsNotNone(by_title)
        self.assertEqual(by_doi.paper_id, by_title.paper_id)

    def test_citation_count_matches_a_known_landmark(self):
        paper = self.searcher.resolve_oa_pdf(self.DEEP_LEARNING_DOI)
        self.assertGreater(paper.citations, 30000,
                           "Nature 2015 'Deep learning' is cited far above this bound")

    def test_references_of_a_heavily_citing_paper_are_globally_ranked(self):
        """Ranking must span the whole reference list, not just its first page.

        Schmidhuber's overview cites >1000 works. Its most-cited reference is LSTM
        (~10^5 citations); any client-side batch of the first N ids tops out orders of
        magnitude below that.
        """
        papers = self.searcher.get_references(
            "Deep learning in neural networks: An overview", max_results=5
        )
        self.assertTrue(papers, "this paper has a large deposited reference list")

        counts = [p.citations for p in papers]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertGreater(
            counts[0], 50000,
            f"top reference should be a landmark paper, got {counts[0]} for {papers[0].title!r}"
        )

    def test_open_access_pdf_is_found_for_an_open_paper(self):
        paper = self.searcher.resolve_oa_pdf("Attention Is All You Need")
        self.assertIsNotNone(paper)
        self.assertTrue(paper.pdf_url.startswith("http"),
                        f"expected an OA PDF link, got {paper.pdf_url!r}")

    def test_citation_counts_backfill_for_dois_from_other_sources(self):
        counts = self.searcher.citation_counts_by_doi(
            [self.DEEP_LEARNING_DOI, "10.1038/nature16961"]  # Deep learning; AlphaGo
        )
        self.assertEqual(set(counts), {"10.1038/nature14539", "10.1038/nature16961"})
        for doi, count in counts.items():
            self.assertGreater(count, 10000, f"{doi} is a landmark paper")

    def test_unresolvable_identifier_is_handled_live(self):
        self.assertEqual(
            self.searcher.get_citations("zzzz no such paper qqqq 12345 xyzzy"), []
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
