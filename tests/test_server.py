import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

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

        with patch.object(
            server.arxiv_searcher,
            "download_pdf",
            side_effect=lambda paper_id, save_path: f"{save_path}/{paper_id}.pdf",
        ):
            for paper in search_results:
                paper_id = paper["paper_id"]
                result = asyncio.run(server.download_arxiv(paper_id, "/tmp/paper-search-test"))
                self.assertEqual(result, f"/tmp/paper-search-test/{paper_id}.pdf")


if __name__ == "__main__":
    unittest.main()
