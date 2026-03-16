import unittest
import requests

from paper_search_mcp.academic_platforms.base_search import BASESearcher
from paper_search_mcp.paper import Paper


def check_api_accessible() -> bool:
    """Check whether BASE OAI-PMH endpoint is reachable."""
    try:
        response = requests.get(
            "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
            params={"verb": "Identify"},
            timeout=10,
        )
        if response.status_code != 200:
            return False
        body = (response.text or "").lower()
        return "access denied" not in body and "forbidden" not in body
    except Exception:
        return False


class TestBASESearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible()
        if not cls.api_accessible:
            print("\nWarning: BASE OAI-PMH endpoint is not accessible, network tests will be skipped")

    def setUp(self):
        self.searcher = BASESearcher()

    def test_search_basic(self):
        if not self.api_accessible:
            self.skipTest("BASE OAI-PMH endpoint is not accessible")

        papers = self.searcher.search("machine learning", max_results=3)
        self.assertIsInstance(papers, list)
        self.assertTrue(len(papers) >= 0)

        if papers:
            first = papers[0]
            self.assertTrue(first.title)
            self.assertEqual(first.source, "base")

    def test_filter_language(self):
        paper = Paper(
            paper_id="base-1",
            title="Language Test",
            authors=["Alice"],
            abstract="",
            doi="",
            published_date=None,
            pdf_url="",
            url="",
            source="base",
            categories=[],
            keywords=[],
            extra={"language": "en"},
        )

        self.assertTrue(self.searcher._filter_paper(paper, {"language": "en"}))
        self.assertFalse(self.searcher._filter_paper(paper, {"language": "de"}))

    def test_filter_subject_and_fulltext(self):
        paper = Paper(
            paper_id="base-2",
            title="Subject Test",
            authors=["Bob"],
            abstract="",
            doi="",
            published_date=None,
            pdf_url="https://example.org/paper.pdf",
            url="",
            source="base",
            categories=["Computer Science"],
            keywords=["machine learning"],
            extra={},
        )

        self.assertTrue(self.searcher._filter_paper(paper, {"subject": "computer"}))
        self.assertTrue(self.searcher._filter_paper(paper, {"has_fulltext": True}))

    def test_filter_subject_miss(self):
        paper = Paper(
            paper_id="base-3",
            title="No Match",
            authors=["Carol"],
            abstract="",
            doi="",
            published_date=None,
            pdf_url="",
            url="",
            source="base",
            categories=["Physics"],
            keywords=["quantum"],
            extra={},
        )

        self.assertFalse(self.searcher._filter_paper(paper, {"subject": "biology"}))


if __name__ == "__main__":
    unittest.main()
