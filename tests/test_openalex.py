import unittest

import requests

from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher


def check_api_accessible() -> bool:
    """Check whether the OpenAlex API is reachable."""
    try:
        response = requests.get("https://api.openalex.org/works?per_page=1", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


class TestOpenAlexSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible()
        if not cls.api_accessible:
            print("\nWarning: OpenAlex API is not accessible, some tests will be skipped")

    def setUp(self):
        self.searcher = OpenAlexSearcher()

    def test_search(self):
        if not self.api_accessible:
            self.skipTest("OpenAlex API is not accessible")

        papers = self.searcher.search("machine learning", max_results=5)
        self.assertGreater(len(papers), 0)
        self.assertTrue(papers[0].title)

    def test_search_with_filter(self):
        if not self.api_accessible:
            self.skipTest("OpenAlex API is not accessible")

        papers = self.searcher.search(
            "artificial intelligence",
            max_results=3,
            filter="is_oa:true,has_pdf_url:true",
        )
        self.assertGreaterEqual(len(papers), 0)

    def test_user_agent_header(self):
        self.assertIn("paper-search-mcp", self.searcher.session.headers.get("User-Agent", ""))
        self.assertIn("mailto:", self.searcher.session.headers.get("User-Agent", ""))


if __name__ == "__main__":
    unittest.main()
