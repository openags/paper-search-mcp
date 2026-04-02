import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from paper_search_mcp import server
from paper_search_mcp.paper import Paper


class TestUnpaywallSearchSource(unittest.TestCase):
    def test_search_unpaywall_empty_without_access(self):
        with patch.object(server.unpaywall_resolver, "has_api_access", return_value=False):
            result = asyncio.run(server.search_unpaywall("10.1000/test"))
        self.assertEqual(result, [])

    def test_search_unpaywall_empty_without_doi(self):
        with patch.object(server.unpaywall_resolver, "has_api_access", return_value=True):
            result = asyncio.run(server.search_unpaywall("machine learning"))
        self.assertEqual(result, [])

    def test_search_unpaywall_returns_one_record(self):
        paper = Paper(
            paper_id="unpaywall:10.1000/test",
            title="Unpaywall Record",
            authors=["Alice Example"],
            abstract="",
            doi="10.1000/test",
            published_date=datetime(2023, 1, 1),
            pdf_url="https://example.org/paper.pdf",
            url="https://doi.org/10.1000/test",
            source="unpaywall",
        )

        with patch.object(server.unpaywall_resolver, "has_api_access", return_value=True), \
             patch.object(server.unpaywall_resolver, "get_paper_by_doi", return_value=paper):
            result = asyncio.run(server.search_unpaywall("doi:10.1000/test"))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "unpaywall")
        self.assertEqual(result[0]["doi"], "10.1000/test")


if __name__ == "__main__":
    unittest.main()
