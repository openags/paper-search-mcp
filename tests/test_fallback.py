import unittest
import asyncio
from unittest.mock import patch, AsyncMock

from paper_search_mcp import server
from paper_search_mcp import cli


class TestDownloadWithFallback(unittest.TestCase):
    def test_repository_fallback_before_scihub(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=("/tmp/repo.pdf", ""))), \
             patch("paper_search_mcp.server.SciHubFetcher.download_pdf", side_effect=AssertionError("Sci-Hub should not be called")):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=True,
                )
            )
            self.assertEqual(result, "/tmp/repo.pdf")

    def test_unpaywall_fallback_after_repositories(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(None, "repo failed"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value="https://example.org/oa.pdf"), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/unpaywall.pdf")):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=True,
                )
            )
            self.assertEqual(result, "/tmp/unpaywall.pdf")

    def test_no_scihub_returns_oa_chain_error(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(None, "repo failed"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=False,
                )
            )
            self.assertIn("OA fallback chain", result)

    def test_parse_sources_defaults_to_fast_set(self):
        cli.SEARCHERS.clear()
        selected = cli._parse_sources("fast")
        self.assertEqual(selected, [s for s in cli._fast_sources() if s in cli._available_sources()])
        self.assertEqual(cli.SEARCHERS, {})

        fastest = cli._parse_sources("fastest")
        self.assertEqual(fastest, [s for s in cli.FASTEST_SOURCES if s in cli._available_sources()])
        self.assertLess(len(fastest), len(selected))

        selected_from_all = cli._parse_sources("all")
        self.assertEqual(selected_from_all, selected)

        exhaustive = cli._parse_sources("all", exhaustive=True)
        self.assertIn("google_scholar", exhaustive)
        self.assertGreater(len(exhaustive), len(selected))

    def test_fast_sources_include_semantic_when_key_is_configured(self):
        with patch.dict("os.environ", {"PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "test-key"}):
            selected = cli._parse_sources("fast")

        self.assertIn("semantic", selected)

if __name__ == "__main__":
    unittest.main()
