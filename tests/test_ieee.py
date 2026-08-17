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

    def test_search_returns_empty_on_api_error(self):
        """Search should return empty list when API fails."""
        mock_response = _MockResponse(status_code=500)

        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()
            with unittest.mock.patch.object(searcher.session, "get", return_value=mock_response):
                papers = searcher.search("test query")

        self.assertEqual(papers, [])

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
