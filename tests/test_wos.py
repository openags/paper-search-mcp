"""Tests for Web of Science optional connector."""
import importlib
import os
import unittest
import unittest.mock

from paper_search_mcp.academic_platforms.wos import WebOfScienceSearcher


class TestWOSDisabledByDefault(unittest.TestCase):
    def setUp(self):
        self._original = os.environ.pop("WOS_API_KEY", None)
        self._prefixed = os.environ.pop("PAPER_SEARCH_MCP_WOS_API_KEY", None)

    def tearDown(self):
        if self._original is not None:
            os.environ["WOS_API_KEY"] = self._original
        else:
            os.environ.pop("WOS_API_KEY", None)
        if self._prefixed is not None:
            os.environ["PAPER_SEARCH_MCP_WOS_API_KEY"] = self._prefixed
        else:
            os.environ.pop("PAPER_SEARCH_MCP_WOS_API_KEY", None)

    def test_is_not_configured_without_key(self):
        self.assertFalse(WebOfScienceSearcher().is_configured())

    def test_search_raises_without_key(self):
        with self.assertRaises(NotImplementedError) as ctx:
            WebOfScienceSearcher().search("deep learning")
        self.assertIn("WOS_API_KEY", str(ctx.exception))

    def test_not_in_all_sources_without_key(self):
        import paper_search_mcp.server as srv_module
        importlib.reload(srv_module)
        self.assertNotIn("wos", srv_module.ALL_SOURCES)


class TestWOSConfigured(unittest.TestCase):
    def test_is_configured_with_key(self):
        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_WOS_API_KEY": "dummy_wos_key"}):
            self.assertTrue(WebOfScienceSearcher().is_configured())

    def test_search_maps_response(self):
        mock_payload = {
            "hits": [
                {
                    "uid": "WOS:0001",
                    "title": "Transformer Models in Biology",
                    "names": {"authors": [{"displayName": "Alice"}, {"displayName": "Bob"}]},
                    "abstract": "Paper abstract with DOI 10.1000/xyz123.",
                    "publishYear": 2024,
                    "links": {"record": "https://www.webofscience.com/wos/woscc/full-record/WOS:0001"},
                    "identifiers": {"doi": "10.1000/xyz123"},
                    "timesCited": 12,
                }
            ]
        }

        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_WOS_API_KEY": "dummy_wos_key"}):
            searcher = WebOfScienceSearcher()
            with unittest.mock.patch.object(searcher.session, "get") as mock_get:
                mock_get.return_value.raise_for_status.return_value = None
                mock_get.return_value.json.return_value = mock_payload
                papers = searcher.search("transformer", max_results=1)

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].paper_id, "WOS:0001")
        self.assertEqual(papers[0].source, "wos")
        self.assertEqual(papers[0].doi, "10.1000/xyz123")

    def test_in_all_sources_with_key(self):
        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_WOS_API_KEY": "dummy_wos_key"}):
            import paper_search_mcp.server as srv_module
            importlib.reload(srv_module)
            self.assertIn("wos", srv_module.ALL_SOURCES)


if __name__ == "__main__":
    unittest.main()
