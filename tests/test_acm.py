"""Tests for ACM Digital Library optional skeleton connector."""
import os
import unittest
import unittest.mock


class TestACMDisabledByDefault(unittest.TestCase):
    """Verify ACM DL skeleton is disabled when ACM_API_KEY is not set."""

    def setUp(self):
        self._original = os.environ.pop("ACM_API_KEY", None)

    def tearDown(self):
        if self._original is not None:
            os.environ["ACM_API_KEY"] = self._original
        else:
            os.environ.pop("ACM_API_KEY", None)

    def test_is_not_configured_without_key(self):
        from paper_search_mcp.academic_platforms.acm import ACMSearcher
        searcher = ACMSearcher()
        self.assertFalse(searcher.is_configured())

    def test_search_raises_not_implemented_without_key(self):
        from paper_search_mcp.academic_platforms.acm import ACMSearcher
        searcher = ACMSearcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.search("distributed systems")
        self.assertIn("ACM_API_KEY", str(ctx.exception))

    def test_download_raises_not_implemented_without_key(self):
        from paper_search_mcp.academic_platforms.acm import ACMSearcher
        searcher = ACMSearcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.download_pdf("3458817.3476175")
        self.assertIn("ACM_API_KEY", str(ctx.exception))

    def test_read_raises_not_implemented_without_key(self):
        from paper_search_mcp.academic_platforms.acm import ACMSearcher
        searcher = ACMSearcher()
        with self.assertRaises(NotImplementedError) as ctx:
            searcher.read_paper("3458817.3476175")
        self.assertIn("ACM_API_KEY", str(ctx.exception))

    def test_not_in_all_sources_without_key(self):
        """acm must NOT appear in ALL_SOURCES when the key is absent."""
        import importlib
        import paper_search_mcp.server as srv_module
        importlib.reload(srv_module)
        self.assertNotIn("acm", srv_module.ALL_SOURCES)


class TestACMIsConfiguredWithKey(unittest.TestCase):
    """Verify ACM DL skeleton reports configured when ACM_API_KEY is present."""

    def test_is_configured_with_key(self):
        # Use the prefixed key so it takes priority over any empty value loaded from .env
        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_ACM_API_KEY": "dummy_test_key"}):
            from paper_search_mcp.academic_platforms.acm import ACMSearcher
            searcher = ACMSearcher()
            self.assertTrue(searcher.is_configured())

    def test_search_raises_not_implemented_even_with_key(self):
        """Real search logic is ToD; should still raise NotImplementedError."""
        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_ACM_API_KEY": "dummy_test_key"}):
            from paper_search_mcp.academic_platforms.acm import ACMSearcher
            searcher = ACMSearcher()
            with self.assertRaises(NotImplementedError) as ctx:
                searcher.search("consensus algorithms")
            self.assertNotIn("ACM_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
