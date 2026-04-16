import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from paper_search_mcp import server


class TestGoogleScholarToolTimeout(unittest.TestCase):
    @patch("paper_search_mcp.server.async_search", new_callable=AsyncMock)
    def test_search_google_scholar_returns_results_when_within_timeout(self, mock_async_search):
        expected = [{"paper_id": "1", "title": "paper"}]
        mock_async_search.return_value = expected

        with patch("paper_search_mcp.server.GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS", 0.1):
            result = asyncio.run(server.search_google_scholar("machine learning", max_results=5))

        self.assertEqual(result, expected)

    @patch("paper_search_mcp.server.async_search", new_callable=AsyncMock)
    def test_search_google_scholar_returns_empty_list_on_timeout(self, mock_async_search):
        async def slow_search(*args, **kwargs):
            await asyncio.sleep(0.05)
            return [{"paper_id": "1", "title": "paper"}]

        mock_async_search.side_effect = slow_search

        with patch("paper_search_mcp.server.GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS", 0.01):
            result = asyncio.run(server.search_google_scholar("machine learning", max_results=5))

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
