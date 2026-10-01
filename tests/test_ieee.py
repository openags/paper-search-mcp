"""Tests for IEEE Xplore connector."""
import os
import unittest
import unittest.mock


class TestIEEEDisabledByDefault(unittest.TestCase):
    """Verify IEEE Xplore is disabled when IEEE_API_KEY is not set."""

    def setUp(self):
        # Ensure the key is absent for these tests
        self._original = os.environ.pop("IEEE_API_KEY", None)
        self._original_prefixed = os.environ.pop("PAPER_SEARCH_MCP_IEEE_API_KEY", None)

    def tearDown(self):
        if self._original is not None:
            os.environ["IEEE_API_KEY"] = self._original
        else:
            os.environ.pop("IEEE_API_KEY", None)
        if self._original_prefixed is not None:
            os.environ["PAPER_SEARCH_MCP_IEEE_API_KEY"] = self._original_prefixed
        else:
            os.environ.pop("PAPER_SEARCH_MCP_IEEE_API_KEY", None)

    def test_is_not_configured_without_key(self):
        from paper_search_mcp.academic_platforms.ieee import IEEESearcher
        searcher = IEEESearcher()
        self.assertFalse(searcher.is_configured())

    def test_search_raises_not_implemented_without_key(self):
        from paper_search_mcp.academic_platforms.ieee import IEEESearcher
        searcher = IEEESearcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.search("transformer attention")
        self.assertIn("IEEE_API_KEY", str(ctx.exception))

    def test_download_raises_not_implemented_without_key(self):
        from paper_search_mcp.academic_platforms.ieee import IEEESearcher
        searcher = IEEESearcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.download_pdf("12345")
        self.assertIn("IEEE_API_KEY", str(ctx.exception))

    def test_read_raises_not_implemented_without_key(self):
        from paper_search_mcp.academic_platforms.ieee import IEEESearcher
        searcher = IEEESearcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.read_paper("12345")
        self.assertIn("IEEE_API_KEY", str(ctx.exception))

    def test_not_in_all_sources_without_key(self):
        """ieee must NOT appear in ALL_SOURCES when the key is absent."""
        import importlib
        import paper_search_mcp.server as srv_module
        importlib.reload(srv_module)
        self.assertNotIn("ieee", srv_module.ALL_SOURCES)


class _MockResponse:
    """Minimal mock for requests.Response."""
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.headers = {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status={self.status_code}")


class TestIEEEIsConfiguredWithKey(unittest.TestCase):
    """Verify IEEE Xplore reports configured and search works with API key."""

    def test_is_configured_with_key(self):
        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "dummy_test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()
            self.assertTrue(searcher.is_configured())

    def test_search_returns_papers_with_mocked_response(self):
        """Search should return papers when API responds successfully."""
        mock_article = {
            "article_number": "12345",
            "title": "Test IEEE Paper",
            "authors": {"authors": [{"full_name": "Alice Smith"}]},
            "abstract": "A test abstract.",
            "doi": "10.1109/test.2024.12345",
            "publication_date": "2024-01-15",
            "pdf_url": "https://ieeexplore.ieee.org/test.pdf",
            "html_url": "https://ieeexplore.ieee.org/document/12345",
            "citing_paper_count": 5,
            "index_terms": {
                "ieee_terms": {"terms": ["machine learning"]},
                "author_terms": {"terms": ["deep learning"]},
            },
        }
        mock_response = _MockResponse(json_data={"articles": [mock_article]})

        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()
            with unittest.mock.patch.object(searcher.session, "get", return_value=mock_response):
                papers = searcher.search("quantum computing", max_results=5)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].paper_id, "12345")
        self.assertEqual(papers[0].title, "Test IEEE Paper")
        self.assertIn("Alice Smith", papers[0].authors)
        self.assertEqual(papers[0].doi, "10.1109/test.2024.12345")
        self.assertEqual(papers[0].citations, 5)
        self.assertIn("machine learning", papers[0].keywords)
        self.assertIn("deep learning", papers[0].keywords)

    def test_search_raises_on_api_error(self):
        """Search should distinguish API failure from a successful empty query."""
        mock_response = _MockResponse(status_code=500)

        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()
            with unittest.mock.patch.object(searcher.session, "get", return_value=mock_response), unittest.mock.patch("time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                    searcher.search("test query")

    def test_parse_date_multi_format(self):
        """Date parser should handle multiple IEEE date formats."""
        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()

            # YYYY-MM-DD
            from datetime import datetime
            dt = searcher._parse_date({"publication_date": "2024-01-15"})
            self.assertEqual(dt, datetime(2024, 1, 15))

            # YYYY-MM
            dt = searcher._parse_date({"publication_date": "2024-06"})
            self.assertEqual(dt, datetime(2024, 6, 1))

            # YYYY
            dt = searcher._parse_date({"publication_date": "2024"})
            self.assertEqual(dt, datetime(2024, 1, 1))

            # Fallback to publication_year
            dt = searcher._parse_date({"publication_year": 2023})
            self.assertEqual(dt, datetime(2023, 1, 1))

            # Empty
            dt = searcher._parse_date({})
            self.assertIsNone(dt)

    def test_url_constructed_from_article_number(self):
        """URL should be constructed from article_number when html_url missing."""
        mock_article = {
            "article_number": "99999",
            "title": "No URL Paper",
            "authors": {"authors": []},
            "abstract": "",
        }
        mock_response = _MockResponse(json_data={"articles": [mock_article]})

        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()
            with unittest.mock.patch.object(searcher.session, "get", return_value=mock_response):
                papers = searcher.search("test")

        self.assertEqual(papers[0].url, "https://ieeexplore.ieee.org/document/99999")

    def test_pagination(self):
        """Search should paginate when max_results > 200."""
        # First page: 200 articles, second page: 50 articles
        page1 = [{"article_number": str(i), "title": f"Paper {i}"} for i in range(200)]
        page2 = [{"article_number": str(200 + i), "title": f"Paper {200 + i}"} for i in range(50)]

        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()
            with unittest.mock.patch.object(
                searcher.session, "get",
                side_effect=[
                    _MockResponse(json_data={"articles": page1}),
                    _MockResponse(json_data={"articles": page2}),
                ],
            ):
                papers = searcher.search("test", max_results=250)

        self.assertEqual(len(papers), 250)


if __name__ == "__main__":
    unittest.main()


# Additional hardened behavior beyond the original PR #60 implementation.
import pytest
import requests
from unittest.mock import Mock, patch
from paper_search_mcp.academic_platforms.ieee import IEEESearcher


@pytest.fixture
def configured_ieee():
    with patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "secret-key"}):
        return IEEESearcher()


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500])
def test_http_errors_redacted_and_explicit(configured_ieee, status):
    configured_ieee.session.get = Mock(return_value=_MockResponse(status_code=status))
    with patch("time.sleep"), pytest.raises(RuntimeError) as error:
        configured_ieee.search("test")
    assert str(status) in str(error.value)
    assert "secret-key" not in str(error.value)
    if status in {401, 403, 301, 400}:
        assert configured_ieee.session.get.call_count == 1
    assert configured_ieee.session.get.call_args.kwargs["allow_redirects"] is False


def test_network_exception_url_and_key_not_leaked(configured_ieee):
    configured_ieee.session.get = Mock(side_effect=requests.ConnectionError("https://ieee.test/?apikey=secret-key"))
    with patch("time.sleep"), pytest.raises(RuntimeError) as error:
        configured_ieee.search("test")
    assert "secret-key" not in str(error.value)
    assert configured_ieee.session.get.call_count == 3


def test_long_retry_after_does_not_sleep_indefinitely(configured_ieee):
    result = _MockResponse(status_code=429); result.headers["Retry-After"] = "600"
    configured_ieee.session.get = Mock(return_value=result)
    with patch("time.sleep") as sleep, pytest.raises(RuntimeError, match="rate-limited"):
        configured_ieee.search("test")
    sleep.assert_not_called()


@pytest.mark.parametrize("data", [{}, {"error": "secret-key"}, {"articles": None}, [], {"articles": [{}]}, {"articles": [None]}])
def test_malformed_payloads_are_errors(configured_ieee, data):
    configured_ieee.session.get = Mock(return_value=_MockResponse(json_data=data))
    with pytest.raises(RuntimeError):
        configured_ieee.search("test")


def test_invalid_json_is_an_error(configured_ieee):
    result = _MockResponse(); result.json = Mock(side_effect=ValueError("invalid"))
    configured_ieee.session.get = Mock(return_value=result)
    with pytest.raises(RuntimeError, match="invalid JSON"):
        configured_ieee.search("test")


@pytest.mark.parametrize("data", [{"articles": []}, {"total_records": 0}, {"totalfound": "0"}])
def test_genuine_empty_response(configured_ieee, data):
    configured_ieee.session.get = Mock(return_value=_MockResponse(json_data=data))
    assert configured_ieee.search("test") == []


@pytest.mark.parametrize("limit", [-1, 1001, 1.5, True])
def test_ieee_limits_are_validated(configured_ieee, limit):
    configured_ieee.session.get = Mock()
    with pytest.raises(ValueError):
        configured_ieee.search("test", limit)
    configured_ieee.session.get.assert_not_called()


def test_zero_limit_has_no_network(configured_ieee):
    configured_ieee.session.get = Mock()
    assert configured_ieee.search("test", 0) == []
    configured_ieee.session.get.assert_not_called()


def test_second_page_error_does_not_return_partial_success(configured_ieee):
    first = [{"article_number": str(i + 1), "title": f"Paper {i}"} for i in range(200)]
    configured_ieee.session.get = Mock(side_effect=[_MockResponse(json_data={"articles": first}), _MockResponse(status_code=403)])
    with pytest.raises(RuntimeError, match="403"):
        configured_ieee.search("test", 201)


def test_duplicate_pagination_is_bounded(configured_ieee):
    first = [{"article_number": str(i + 1), "title": f"Paper {i}"} for i in range(200)]
    configured_ieee.session.get = Mock(return_value=_MockResponse(json_data={"articles": first}))
    with pytest.raises(RuntimeError, match="did not advance"):
        configured_ieee.search("test", 400)
    assert configured_ieee.session.get.call_count == 2


def test_identity_urls_are_canonical_and_not_spoofable(configured_ieee):
    article = {"article_number": "123", "title": "A paper", "html_url": "https://evil.test/123",
               "pdf_url": "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=999"}
    paper = configured_ieee._parse_article(article)
    assert paper.url == "https://ieeexplore.ieee.org/document/123"
    assert paper.pdf_url == ""
    article["pdf_url"] = "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=123"
    assert configured_ieee._parse_article(article).pdf_url.endswith("arnumber=123")


def test_key_does_not_promise_unimplemented_full_text(configured_ieee):
    for method in [configured_ieee.download_pdf, configured_ieee.read_paper]:
        with pytest.raises(NotImplementedError, match="metadata only"):
            method("123")


@pytest.mark.parametrize("raw,year", [("Jan 2024", 2024), ("January 2024", 2024), ("bogus", 2023), (None, 2023)])
def test_date_formats_and_fallback(configured_ieee, raw, year):
    assert configured_ieee._parse_date({"publication_date": raw, "publication_year": 2023}).year == year
