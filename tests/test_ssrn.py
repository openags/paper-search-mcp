import unittest
from unittest.mock import patch

from paper_search_mcp.academic_platforms.ssrn import SSRNSearcher


class TestSSRNSearcher(unittest.TestCase):
    def setUp(self):
        self.searcher = SSRNSearcher()

    def test_parse_results_minimal_html(self):
        html = """
        <html><body>
          <div class="result-item">
            <h3><a href="/sol3/papers.cfm?abstract_id=1234567">SSRN Parser Test</a></h3>
            <div class="authors">Alice Example, Bob Example</div>
            <div class="abstract-text">This is a sample abstract from SSRN.</div>
            <span class="date">2024-01-15</span>
          </div>
        </body></html>
        """

        papers = self.searcher._parse_results(html)
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.source, "ssrn")
        self.assertEqual(paper.title, "SSRN Parser Test")
        self.assertTrue(paper.paper_id.startswith("ssrn:"))
        self.assertIn("Alice", paper.authors)
        self.assertIn("sample abstract", paper.abstract)

    def test_parse_results_legacy_layout(self):
        html = """
        <html><body>
          <div class="srp-item">
            <div class="title"><a href="/sol3/papers.cfm?abstract_id=7654321">Legacy SSRN Item</a></div>
            <div class="srp-authors">Carol Example</div>
            <div class="srp-snippet">Legacy snippet abstract text.</div>
            <div class="srp-date">2021</div>
          </div>
        </body></html>
        """

        papers = self.searcher._parse_results(html)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].title, "Legacy SSRN Item")
        self.assertIn("Carol", papers[0].authors)

    def test_parse_results_invalid_html(self):
        papers = self.searcher._parse_results("<html><body><p>no results here</p></body></html>")
        self.assertEqual(papers, [])

    def test_extract_abstract_id(self):
        self.assertEqual(self.searcher._extract_abstract_id("ssrn:1234567"), "1234567")
        self.assertEqual(
            self.searcher._extract_abstract_id("https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1234567"),
            "1234567",
        )
        self.assertEqual(self.searcher._extract_abstract_id("invalid"), "")

    def test_fetch_page_falls_back_to_alt_endpoint(self):
        class _Resp:
            def __init__(self, status_code, text):
                self.status_code = status_code
                self.text = text

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests
                    raise requests.HTTPError(f"status={self.status_code}")

        with patch.object(
            self.searcher.session,
            "get",
            side_effect=[
                _Resp(403, "<html><title>Just a moment...</title></html>"),
                _Resp(200, "<html><div class='result-item'></div></html>"),
            ],
        ):
            html, error = self.searcher._fetch_page("machine learning", 1)

        self.assertIn("result-item", html)
        self.assertEqual(error, "")

    def test_download_returns_message_when_pdf_unavailable(self):
        with patch.object(self.searcher, "_resolve_pdf_url", return_value=""):
            result = self.searcher.download_pdf("ssrn:1234567")
        self.assertIn("no publicly accessible ssrn pdf url", result.lower())

    def test_read_returns_message_when_download_fails(self):
        with patch.object(self.searcher, "download_pdf", return_value="No PDF available"):
            result = self.searcher.read_paper("ssrn:1234567")
        self.assertIn("no pdf", result.lower())


if __name__ == "__main__":
    unittest.main()
