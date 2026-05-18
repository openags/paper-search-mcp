import unittest
import asyncio
from unittest.mock import patch, AsyncMock

from paper_search_mcp import server


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


class TestRepositoryFallbackNumericPaperId(unittest.TestCase):
    """Regression test for issue #57: _try_repository_fallback crashed when a
    repository connector returned a Paper whose paper_id was a non-string
    (int) value, because the code called .strip() on it directly."""

    def test_numeric_paper_id_does_not_crash(self):
        class FakePaper:
            pdf_url = "https://example.org/oa.pdf"
            paper_id = 12345  # int, not str — caused 'int' object has no attribute 'strip'

        fake_searcher = type(
            "S", (), {"search": staticmethod(lambda q, max_results=3: [FakePaper()])}
        )

        # Patch one of the repository searchers to return our FakePaper.
        with patch.object(server, "openaire_searcher", fake_searcher), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/ok.pdf")):
            result, err = asyncio.run(
                server._try_repository_fallback(
                    doi="10.1000/test",
                    title="some title",
                    save_path="/tmp",
                )
            )
            self.assertEqual(result, "/tmp/ok.pdf")
            self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
