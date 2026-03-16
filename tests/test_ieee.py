"""Tests for IEEE Xplore optional skeleton connector."""
import os
import unittest
import unittest.mock


class TestIEEEDisabledByDefault(unittest.TestCase):
    """Verify IEEE Xplore skeleton is disabled when IEEE_API_KEY is not set."""

    def setUp(self):
        # Ensure the key is absent for these tests
        self._original = os.environ.pop("IEEE_API_KEY", None)

    def tearDown(self):
        if self._original is not None:
            os.environ["IEEE_API_KEY"] = self._original
        else:
            os.environ.pop("IEEE_API_KEY", None)

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
        # Reload server module with key absent to get a clean ALL_SOURCES
        import importlib
        import paper_search_mcp.server as srv_module
        importlib.reload(srv_module)
        self.assertNotIn("ieee", srv_module.ALL_SOURCES)


class TestIEEEIsConfiguredWithKey(unittest.TestCase):
    """Verify IEEE Xplore skeleton reports configured when API key env var is present."""

    def test_is_configured_with_key(self):
        # Use the prefixed key so it takes priority over any empty value loaded from .env
        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "dummy_test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()
            self.assertTrue(searcher.is_configured())

    def test_search_raises_not_implemented_even_with_key(self):
        """Real search logic is ToD; should still raise NotImplementedError."""
        with unittest.mock.patch.dict(os.environ, {"PAPER_SEARCH_MCP_IEEE_API_KEY": "dummy_test_key"}):
            from paper_search_mcp.academic_platforms.ieee import IEEESearcher
            searcher = IEEESearcher()
            with self.assertRaises(NotImplementedError) as ctx:
                searcher.search("quantum computing")
            # Should NOT mention missing key — it's a "not yet implemented" error
            self.assertNotIn("IEEE_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
