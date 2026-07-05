import os
import unittest
from unittest.mock import Mock, patch

from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher


class TestOpenAlexSearcher(unittest.TestCase):
    def test_search_sends_api_key_from_env(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "PAPER_SEARCH_MCP_OPENALEX_API_KEY": "test-openalex-key",
            },
            clear=True,
        ):
            searcher = OpenAlexSearcher()

        response = Mock(status_code=200)
        response.json.return_value = {"results": []}

        with patch.object(searcher.session, "get", return_value=response) as get:
            papers = searcher.search("graph neural networks", max_results=7)

        self.assertEqual(papers, [])
        params = get.call_args[1]["params"]
        self.assertEqual(params["api_key"], "test-openalex-key")
        self.assertEqual(params["per_page"], 7)

    def test_search_omits_empty_api_key(self):
        searcher = OpenAlexSearcher(api_key="")
        response = Mock(status_code=200)
        response.json.return_value = {"results": []}

        with patch.object(searcher.session, "get", return_value=response) as get:
            searcher.search("protein design")

        self.assertNotIn("api_key", get.call_args[1]["params"])

    def test_email_customizes_user_agent(self):
        searcher = OpenAlexSearcher(email="researcher@example.com")

        self.assertIn(
            "mailto:researcher@example.com",
            searcher.session.headers["User-Agent"],
        )


if __name__ == "__main__":
    unittest.main()
