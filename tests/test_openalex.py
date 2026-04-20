import unittest

from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher
from paper_search_mcp.academic_platforms.ssrn import SSRNSearcher


class _MockResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class TestOpenAlexSearcher(unittest.TestCase):
    def setUp(self):
        self.searcher = OpenAlexSearcher()

    @staticmethod
    def _make_ssrn_result(abstract_id: str, title: str | None = None):
        return {
            "id": f"https://openalex.org/W{abstract_id}",
            "title": title or f"SSRN Paper {abstract_id}",
            "authorships": [
                {"author": {"display_name": "Alice Example"}},
                {"author": {"display_name": "Bob Example"}},
            ],
            "abstract_inverted_index": {"Test": [0], "abstract": [1]},
            "doi": "https://doi.org/10.1234/example",
            "primary_location": {
                "landing_page_url": (
                    f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={abstract_id}"
                ),
                "pdf_url": (
                    f"https://papers.ssrn.com/sol3/Delivery.cfm?abstract_id={abstract_id}"
                ),
            },
            "open_access": {"is_oa": False, "oa_url": ""},
            "publication_date": "2024-01-15",
            "cited_by_count": 7,
        }

    def test_search_ssrn_normalizes_paper_id_to_ssrn_prefix(self):
        payload = {
            "results": [self._make_ssrn_result("1234567")],
            "meta": {"next_cursor": None},
        }

        original_get = self.searcher.session.get
        self.searcher.session.get = lambda *args, **kwargs: _MockResponse(payload)
        try:
            papers = self.searcher.search_ssrn("contract law", max_results=5)
        finally:
            self.searcher.session.get = original_get

        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.paper_id, "ssrn:1234567")
        self.assertEqual(SSRNSearcher._extract_abstract_id(paper.paper_id), "1234567")
        self.assertEqual(paper.source, "ssrn")
        self.assertIn("abstract_id=1234567", paper.url)

    def test_search_ssrn_uses_cursor_pagination_until_max_results(self):
        first_page = [self._make_ssrn_result(str(1000000 + i)) for i in range(100)]
        second_page = [self._make_ssrn_result(str(1000100 + i)) for i in range(100)]
        third_page = [self._make_ssrn_result(str(1000200 + i)) for i in range(100)]
        responses = {
            "*": _MockResponse({"results": first_page, "meta": {"next_cursor": "cursor-2"}}),
            "cursor-2": _MockResponse(
                {"results": second_page, "meta": {"next_cursor": "cursor-3"}}
            ),
            "cursor-3": _MockResponse({"results": third_page, "meta": {"next_cursor": None}}),
        }
        seen_params = []

        def _fake_get(url, params=None, timeout=30):
            seen_params.append(dict(params))
            return responses[params["cursor"]]

        original_get = self.searcher.session.get
        self.searcher.session.get = _fake_get
        try:
            papers = self.searcher.search_ssrn("machine learning", max_results=250)
        finally:
            self.searcher.session.get = original_get

        self.assertEqual(len(papers), 250)
        self.assertEqual([params["cursor"] for params in seen_params], ["*", "cursor-2", "cursor-3"])
        self.assertTrue(all(params["per_page"] == 100 for params in seen_params))
        self.assertEqual(papers[-1].paper_id, "ssrn:1000249")

    def test_search_ssrn_stops_when_next_cursor_is_null(self):
        payload = {
            "results": [self._make_ssrn_result(str(2000000 + i)) for i in range(80)],
            "meta": {"next_cursor": None},
        }

        call_count = 0
        original_get = self.searcher.session.get

        def _fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _MockResponse(payload)

        self.searcher.session.get = _fake_get
        try:
            papers = self.searcher.search_ssrn("behavioral economics", max_results=150)
        finally:
            self.searcher.session.get = original_get

        self.assertEqual(len(papers), 80)
        self.assertEqual(call_count, 1)

    def test_search_ssrn_skips_rows_without_ssrn_compatible_locator(self):
        valid = self._make_ssrn_result("7654321", title="Valid SSRN paper")
        invalid = {
            "id": "https://openalex.org/W9999999",
            "title": "Missing locator paper",
            "authorships": [],
            "abstract_inverted_index": {},
            "primary_location": {
                "landing_page_url": "https://openalex.org/W9999999",
                "pdf_url": "",
            },
            "open_access": {"is_oa": False, "oa_url": ""},
            "publication_date": "2024-01-15",
            "cited_by_count": 0,
        }
        payload = {"results": [valid, invalid], "meta": {"next_cursor": None}}

        original_get = self.searcher.session.get
        self.searcher.session.get = lambda *args, **kwargs: _MockResponse(payload)
        try:
            papers = self.searcher.search_ssrn("finance", max_results=10)
        finally:
            self.searcher.session.get = original_get

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].paper_id, "ssrn:7654321")


if __name__ == "__main__":
    unittest.main()
