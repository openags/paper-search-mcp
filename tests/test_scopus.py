# tests/test_scopus.py
"""Unit tests for the REST-based ScopusSearcher.

All network access is mocked at the requests.Session level (the searcher's
``session`` attribute is replaced with a MagicMock). No live calls are made
and no real API keys appear in this file.
"""
import os
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

from paper_search_mcp.academic_platforms.scopus import ScopusSearcher
from paper_search_mcp.paper import Paper

FAKE_KEY = "test-key"


def _mock_response(status_code=200, json_data=None, headers=None):
    """Build a MagicMock that looks like a requests.Response."""
    response = MagicMock(name=f"MockResponse<{status_code}>")
    response.status_code = status_code
    response.headers = headers if headers is not None else {}
    response.json.return_value = json_data if json_data is not None else {}
    if status_code < 400:
        response.raise_for_status.return_value = None
    else:
        import requests

        response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"{status_code} Error", response=response
        )
    return response


def _make_searcher():
    """Create a searcher with a fake key and a fully mocked session."""
    searcher = ScopusSearcher(api_key=FAKE_KEY)
    searcher.session = MagicMock(name="MockSession")
    return searcher


SAMPLE_ENTRY = {
    "dc:identifier": "SCOPUS_ID:123",
    "dc:title": "Deep Learning for Testing",
    "author": [
        {"authname": "Author A"},
        {"authname": "Author B"},
    ],
    "dc:description": "A realistic abstract about deep learning.",
    "prism:doi": "10.1000/test.123",
    "prism:url": "https://api.elsevier.com/content/abstract/scopus_id/123",
    "prism:coverDate": "2024-01-15",
    "citedby-count": "7",
    "subject-area": [{"@abbrev": "COMP"}],
}


class TestScopusSearcherInit(unittest.TestCase):
    """API key resolution from env var / constructor / absent."""

    def test_api_key_from_env(self):
        with patch.dict(os.environ, {"SCOPUS_API_KEY": "env-test-key"}, clear=True):
            searcher = ScopusSearcher()
        self.assertEqual(searcher.api_key, "env-test-key")
        self.assertEqual(searcher.session.headers.get("X-ELS-APIKey"), "env-test-key")

    def test_api_key_from_constructor(self):
        with patch.dict(os.environ, {"SCOPUS_API_KEY": "env-test-key"}, clear=True):
            searcher = ScopusSearcher(api_key=FAKE_KEY)
        self.assertEqual(searcher.api_key, FAKE_KEY)
        self.assertEqual(searcher.session.headers.get("X-ELS-APIKey"), FAKE_KEY)

    def test_api_key_missing_raises_value_error(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Scopus API key"):
                ScopusSearcher()


class TestScopusSearch(unittest.TestCase):
    """search() behavior against mocked Scopus Search API responses."""

    def setUp(self):
        self.searcher = _make_searcher()

    def test_search_success(self):
        payload = {"search-results": {"entry": [SAMPLE_ENTRY]}}
        self.searcher.session.get.return_value = _mock_response(200, payload)

        papers = self.searcher.search("deep learning", max_results=5)

        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertIsInstance(paper, Paper)
        self.assertEqual(paper.paper_id, "123")  # SCOPUS_ID: prefix stripped
        self.assertEqual(paper.title, "Deep Learning for Testing")
        self.assertEqual(paper.authors, ["Author A", "Author B"])
        self.assertEqual(paper.abstract, "A realistic abstract about deep learning.")
        self.assertEqual(paper.doi, "10.1000/test.123")
        self.assertEqual(paper.published_date, datetime(2024, 1, 15))
        self.assertEqual(paper.citations, 7)
        self.assertEqual(paper.source, "scopus")
        self.assertEqual(paper.categories, ["COMP"])
        # Exactly one (mocked) HTTP call, no live network
        self.assertEqual(self.searcher.session.get.call_count, 1)

    def test_search_single_entry_as_dict(self):
        # Scopus sometimes returns a lone entry as a dict instead of a list
        payload = {"search-results": {"entry": SAMPLE_ENTRY}}
        self.searcher.session.get.return_value = _mock_response(200, payload)

        papers = self.searcher.search("deep learning", max_results=5)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].paper_id, "123")

    def test_search_empty_results(self):
        payload = {"search-results": {"entry": []}}
        self.searcher.session.get.return_value = _mock_response(200, payload)

        papers = self.searcher.search("nothing matches", max_results=5)

        self.assertEqual(papers, [])

    def test_search_empty_result_sentinel_entry(self):
        # Zero hits are reported as a sentinel entry, not an absent 'entry' key
        payload = {
            "search-results": {
                "opensearch:totalResults": "0",
                "entry": [{"@_fa": "true", "error": "Result set was empty"}],
            }
        }
        self.searcher.session.get.return_value = _mock_response(200, payload)

        papers = self.searcher.search("nothing matches", max_results=5)

        self.assertEqual(papers, [])

    def test_search_no_search_results_key_raises(self):
        # A 200 without the search-results envelope is malformed, not "no matches"
        self.searcher.session.get.return_value = _mock_response(200, {})

        with self.assertRaisesRegex(RuntimeError, "search-results"):
            self.searcher.search("weird payload", max_results=5)

    def test_search_raises_on_request_api_error(self):
        # API errors must be distinguishable from an empty result set
        error = {"error": "rate_limited", "status_code": 429, "message": "Too many requests"}
        with patch.object(self.searcher, "request_api", return_value=error):
            with self.assertRaisesRegex(RuntimeError, "rate_limited.*Too many requests"):
                self.searcher.search("anything", max_results=5)


class TestScopusRequestApi(unittest.TestCase):
    """request_api() rate-limit (429) retry behavior."""

    def setUp(self):
        self.searcher = _make_searcher()

    @patch("paper_search_mcp.academic_platforms.scopus.time.sleep")
    def test_request_api_retries_on_429_then_succeeds(self, mock_sleep):
        rate_limited = _mock_response(
            429,
            {"error-response": {"error-code": "TOO_MANY_REQUESTS"}},
            headers={"X-RateLimit-Remaining": "5"},
        )
        success = _mock_response(
            200,
            {"search-results": {"entry": []}},
            headers={"X-RateLimit-Remaining": "4"},
        )
        self.searcher.session.get.side_effect = [rate_limited, rate_limited, success]

        result = self.searcher.request_api({"query": "test"})

        # Eventual success: not an error dict, and it's the 200 response
        self.assertFalse(isinstance(result, dict) and "error" in result)
        self.assertEqual(getattr(result, "status_code", None), 200)
        self.assertEqual(self.searcher.session.get.call_count, 3)
        # Backoff slept between retries, but the test itself never waited
        self.assertGreaterEqual(mock_sleep.call_count, 1)


class TestScopusUnauthorized(unittest.TestCase):
    """401/403 entitlement errors return a hint about network/IP entitlement."""

    def setUp(self):
        self.searcher = _make_searcher()

    def test_401_with_view_returns_network_hint(self):
        unauthorized = _mock_response(
            401,
            {
                "service-error": {
                    "status": {
                        "statusCode": "AUTHORIZATION_ERROR",
                        "statusText": "The requestor is not authorized to access the requested view or fields of the resource",
                    }
                }
            },
        )
        self.searcher.session.get.return_value = unauthorized

        result = self.searcher.request_api({"query": "test", "view": "COMPLETE"})

        self.assertIsInstance(result, dict)
        self.assertEqual(result["error"], "unauthorized")
        self.assertEqual(result["status_code"], 401)
        self.assertIn("not authorized to access the requested view", result["message"])
        self.assertIn("network or VPN", result["message"])
        # No retries: an entitlement error will not fix itself
        self.assertEqual(self.searcher.session.get.call_count, 1)

    def test_403_without_view_returns_plain_error(self):
        self.searcher.session.get.return_value = _mock_response(403, {})

        result = self.searcher.request_api({"query": "test"})

        self.assertEqual(result["error"], "unauthorized")
        self.assertEqual(result["status_code"], 403)
        self.assertNotIn("VPN", result["message"])

    def test_search_raises_on_unauthorized(self):
        unauthorized = _mock_response(401, {})
        self.searcher.session.get.return_value = unauthorized

        with self.assertRaisesRegex(RuntimeError, "unauthorized"):
            self.searcher.search("anything", max_results=5)


class TestScopusPaperDetails(unittest.TestCase):
    """_get_paper_details_by_id input validation and prefix stripping."""

    def setUp(self):
        self.searcher = _make_searcher()

    def test_rejects_non_numeric_id(self):
        result = self.searcher._get_paper_details_by_id("not-a-number")
        self.assertIsNone(result)
        self.searcher.session.get.assert_not_called()

    def test_rejects_path_traversal_id(self):
        result = self.searcher._get_paper_details_by_id("../../../etc/passwd")
        self.assertIsNone(result)
        self.searcher.session.get.assert_not_called()

    def test_strips_scopus_id_prefix_for_numeric_id(self):
        payload = {
            "abstracts-retrieval-response": {
                "coredata": {
                    "dc:title": "A Title",
                    "prism:doi": "10.1000/abc",
                    "prism:coverDate": "2024-01-15",
                },
                "authors": {"author": [{"ce:indexed-name": "Author A"}]},
            }
        }
        self.searcher.session.get.return_value = _mock_response(200, payload)

        details = self.searcher._get_paper_details_by_id("SCOPUS_ID:123")

        self.assertIsNotNone(details)
        self.assertEqual(details["title"], "A Title")
        self.assertEqual(details["doi"], "10.1000/abc")
        self.assertEqual(details["authors"], ["Author A"])
        # The URL must contain the bare numeric ID, not the prefixed form
        called_url = self.searcher.session.get.call_args[0][0]
        self.assertIn("/123", called_url)
        self.assertNotIn("SCOPUS_ID", called_url)


class TestScopusParseDate(unittest.TestCase):
    """_parse_date format handling."""

    def setUp(self):
        self.searcher = _make_searcher()

    def test_parse_full_date(self):
        self.assertEqual(self.searcher._parse_date("2024-01-15"), datetime(2024, 1, 15))

    def test_parse_year_only(self):
        self.assertEqual(self.searcher._parse_date("2024"), datetime(2024, 1, 1))

    def test_parse_garbage_returns_none(self):
        self.assertIsNone(self.searcher._parse_date("not a date"))


if __name__ == "__main__":
    unittest.main()
