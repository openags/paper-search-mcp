import asyncio
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from paper_search_mcp import server
from paper_search_mcp.paper import Paper


def _mock_arxiv_papers(count: int = 3):
    return [
        Paper(
            paper_id=f"2401.0000{i}",
            title=f"Mock arXiv Paper {i}",
            authors=["Ada Lovelace"],
            abstract="mock abstract",
            doi="",
            published_date=datetime(2024, 1, 1),
            pdf_url=f"https://arxiv.org/pdf/2401.0000{i}",
            url=f"https://arxiv.org/abs/2401.0000{i}",
            source="arxiv",
        )
        for i in range(count)
    ]


class TestPaperSearchServer(unittest.TestCase):
    def test_main_uses_local_stdio_transport(self):
        with (
            patch.object(server.mcp, "run") as run,
            patch.object(server.threading, "Thread") as thread,
        ):
            server.main([])

        run.assert_called_once_with(transport="stdio")
        thread.return_value.start.assert_called_once_with()

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

    def test_search_papers_reports_invalid_source_in_mixed_request(self):
        paper = {
            "paper_id": "1234.5678",
            "title": "Test Paper",
            "authors": "Ada Lovelace",
            "doi": "",
            "source": "arxiv",
        }
        with patch.object(server, "search_arxiv", new=AsyncMock(return_value=[paper])):
            result = asyncio.run(
                server.search_papers("test", sources="arxiv,not-a-source")
            )

        self.assertEqual(result["sources_used"], ["arxiv"])
        self.assertEqual(result["source_results"], {"arxiv": 1})
        self.assertEqual(
            result["errors"]["not-a-source"],
            "Unknown or unavailable source.",
        )

    def test_search_papers_surfaces_connector_exception(self):
        with patch.object(
            server,
            "search_semantic",
            new=AsyncMock(side_effect=RuntimeError("HTTP 429: Too many requests")),
        ):
            result = asyncio.run(server.search_papers("test", sources="semantic"))

        self.assertEqual(result["source_results"], {"semantic": 0})
        self.assertEqual(result["errors"]["semantic"], "HTTP 429: Too many requests")

    def test_search_papers_reports_invalid_only_request(self):
        result = asyncio.run(
            server.search_papers("test", sources="not-a-source")
        )

        self.assertEqual(result["sources_used"], [])
        self.assertEqual(
            result["errors"],
            {
                "not-a-source": "Unknown or unavailable source.",
                "sources": "No valid sources selected.",
            },
        )

    def test_search_arxiv(self):
        papers = _mock_arxiv_papers(10)

        with patch.object(server.arxiv_searcher, "search", return_value=papers):
            result = asyncio.run(server.search_arxiv("machine learning", max_results=10))

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 10)
        for paper in result:
            self.assertIn("title", paper)
            self.assertIn("paper_id", paper)

    def test_download_arxiv_from_search(self):
        papers = _mock_arxiv_papers(3)

        with patch.object(server.arxiv_searcher, "search", return_value=papers):
            search_results = asyncio.run(server.search_arxiv("machine learning", max_results=3))

        self.assertEqual(len(search_results), 3)

        def download_pdf(paper_id, save_path):
            output_file = Path(save_path) / f"{paper_id}.pdf"
            output_file.write_bytes(b"%PDF-1.4\n")
            return str(output_file)

        with (
            tempfile.TemporaryDirectory() as save_path,
            patch.object(server.arxiv_searcher, "download_pdf", side_effect=download_pdf) as download,
        ):
            for paper in search_results:
                paper_id = paper["paper_id"]
                result = asyncio.run(server.download_arxiv(paper_id, save_path))
                self.assertIsInstance(result, str, f"Result for {paper_id} should be a file path")
                self.assertTrue(result.endswith(".pdf"), f"Result for {paper_id} should be a PDF file path")
                self.assertTrue(os.path.exists(result), f"PDF file for {paper_id} should exist on disk")
                download.assert_called_with(paper_id, save_path)


if __name__ == "__main__":
    unittest.main()
