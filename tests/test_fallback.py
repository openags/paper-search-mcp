import unittest
import asyncio
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from paper_search_mcp import server
from paper_search_mcp import cli


class TestDownloadWithFallback(unittest.TestCase):
    def test_repository_fallback_before_scihub(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None), \
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

    def test_unpaywall_fallback_before_repositories(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(side_effect=AssertionError("repositories should not be called"))), \
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

    def test_repository_fallback_handles_numeric_paper_id(self):
        paper = SimpleNamespace(paper_id=12345, pdf_url="https://example.org/paper.pdf")
        searcher = SimpleNamespace(search=lambda query, max_results=3: [paper])

        with patch.object(server, "openaire_searcher", searcher), \
             patch.object(server, "core_searcher", SimpleNamespace(search=lambda *args, **kwargs: [])), \
             patch.object(server, "europepmc_searcher", SimpleNamespace(search=lambda *args, **kwargs: [])), \
             patch.object(server, "pmc_searcher", SimpleNamespace(search=lambda *args, **kwargs: [])), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/repo.pdf")) as download:
            result, error = asyncio.run(server._try_repository_fallback("10.1000/test", "title", "/tmp"))

        self.assertEqual(result, "/tmp/repo.pdf")
        self.assertEqual(error, "")
        self.assertEqual(download.call_args.args[2], "openaire_12345")

    def test_search_source_timeout_is_reported(self):
        async def slow_result():
            await asyncio.sleep(1)
            return []

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(cli._with_timeout(slow_result(), 0.01))

    def test_download_doi_cli_uses_fallback(self):
        args = SimpleNamespace(
            doi="10.1000/test",
            source="crossref",
            title="",
            save_path="/tmp",
            no_scihub=True,
            scihub_base_url="https://sci-hub.se",
        )
        with patch("paper_search_mcp.server.download_with_fallback", new=AsyncMock(return_value="/tmp/paper.pdf")):
            result = asyncio.run(cli.cmd_download_doi(args))

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
