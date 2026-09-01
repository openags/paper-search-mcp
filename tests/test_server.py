# tests/test_server.py
import unittest
import asyncio
import os
from unittest.mock import AsyncMock, patch
from paper_search_mcp import server

class TestPaperSearchServer(unittest.TestCase):
    def test_all_sources_include_new_platforms(self):
        self.assertIn("dblp", server.ALL_SOURCES)
        self.assertIn("openaire", server.ALL_SOURCES)
        self.assertIn("citeseerx", server.ALL_SOURCES)
        self.assertIn("doaj", server.ALL_SOURCES)
        self.assertIn("base", server.ALL_SOURCES)
        self.assertIn("zenodo", server.ALL_SOURCES)
        self.assertIn("hal", server.ALL_SOURCES)
        self.assertIn("ssrn", server.ALL_SOURCES)
        self.assertIn("unpaywall", server.ALL_SOURCES)

    def test_parse_sources_with_new_platforms(self):
        parsed = server._parse_sources("dblp,doaj,base,zenodo,hal,ssrn,unpaywall,invalid")
        self.assertEqual(parsed, ["dblp", "doaj", "base", "zenodo", "hal", "ssrn", "unpaywall"])

    def test_search_arxiv(self):
        """Test the search_arxiv tool returns 10 results."""
        result = asyncio.run(server.search_arxiv("machine learning", max_results=10))
        self.assertIsInstance(result, list, "Result should be a list")
        self.assertEqual(len(result), 10, "Should return exactly 10 results")
        for paper in result:
            self.assertIn('title', paper, "Each result should contain a title")
            self.assertIn('paper_id', paper, "Each result should contain a paper_id")

    def test_download_arxiv_from_search(self):
        """Test downloading 10 arXiv papers based on search results."""
        # 先搜索 10 个结果
        search_results = asyncio.run(server.search_arxiv("machine learning", max_results=10))
        self.assertEqual(len(search_results), 10, "Search should return 10 results")

        # 下载目录
        save_path = "./downloads"
        os.makedirs(save_path, exist_ok=True)  # 确保目录存在

        # 下载每个搜索结果的 PDF
        for paper in search_results:
            paper_id = paper['paper_id']
            result = asyncio.run(server.download_arxiv(paper_id, save_path))
            self.assertIsInstance(result, str, f"Result for {paper_id} should be a file path")
            self.assertTrue(result.endswith(".pdf"), f"Result for {paper_id} should be a PDF file path")
            self.assertTrue(os.path.exists(result), f"PDF file for {paper_id} should exist on disk")


class SearchPapersSourceValidationTests(unittest.IsolatedAsyncioTestCase):
    @patch("paper_search_mcp.server.search_pubmed", new_callable=AsyncMock)
    async def test_mixed_sources_preserve_results_and_report_invalid_source(self, search_pubmed):
        search_pubmed.return_value = [{"paper_id": "1", "title": "ECG", "source": "pubmed"}]

        result = await server.search_papers("electrocardiogram", 1, "pubmed,invalid_source")

        self.assertEqual(result["sources_used"], ["pubmed"])
        self.assertEqual(result["source_results"], {"pubmed": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["errors"], {"invalid_source": "Unknown or unavailable source."})

    async def test_invalid_source_only_reports_individual_and_aggregate_errors(self):
        result = await server.search_papers("electrocardiogram", 1, "invalid_source")

        self.assertEqual(result["sources_used"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(
            result["errors"],
            {
                "invalid_source": "Unknown or unavailable source.",
                "sources": "No valid sources selected.",
            },
        )

    @patch("paper_search_mcp.server.search_pubmed", new_callable=AsyncMock)
    async def test_valid_source_does_not_add_validation_error(self, search_pubmed):
        search_pubmed.return_value = [{"paper_id": "1", "title": "ECG", "source": "pubmed"}]

        result = await server.search_papers("electrocardiogram", 1, "pubmed")

        self.assertEqual(result["sources_used"], ["pubmed"])
        self.assertEqual(result["errors"], {})
        self.assertEqual(result["total"], 1)

if __name__ == "__main__":
    unittest.main()
