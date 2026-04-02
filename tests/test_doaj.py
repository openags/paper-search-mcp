import unittest
import requests

from paper_search_mcp.academic_platforms.doaj import DOAJSearcher


def check_api_accessible() -> bool:
    """Check whether DOAJ API is reachable."""
    try:
        response = requests.get(
            "https://doaj.org/api/search/articles/machine%20learning",
            params={"pageSize": 1},
            timeout=10,
        )
        return response.status_code == 200
    except Exception:
        return False


class TestDOAJSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible()
        if not cls.api_accessible:
            print("\nWarning: DOAJ API is not accessible, network tests will be skipped")

    def setUp(self):
        self.searcher = DOAJSearcher()

    def test_search_basic(self):
        if not self.api_accessible:
            self.skipTest("DOAJ API is not accessible")

        papers = self.searcher.search("machine learning", max_results=3)
        self.assertIsInstance(papers, list)
        self.assertTrue(len(papers) >= 0)

        if papers:
            first = papers[0]
            self.assertTrue(first.title)
            self.assertEqual(first.source, "doaj")

    def test_build_lucene_query(self):
        query = self.searcher._build_lucene_query(
            "transformer",
            {
                "year": "2020-2024",
                "journal": "1234-5678",
                "language": "en",
                "subject": "computer science",
            },
        )

        self.assertIn("transformer", query)
        self.assertIn("year:[2020 TO 2024]", query)
        self.assertIn("issn:1234-5678", query)
        self.assertIn("language:en", query)

    def test_parse_doaj_item_minimal(self):
        item = {
            "id": "abc123",
            "bibjson": {
                "title": "DOAJ Parser Test",
                "author": [{"name": "Alice"}],
                "identifier": [{"type": "doi", "id": "10.1000/doaj-test"}],
                "year": "2023",
                "link": [{"type": "fulltext", "url": "https://example.org/test.pdf"}],
            },
        }

        paper = self.searcher._parse_doaj_item(item)
        self.assertIsNotNone(paper)
        if paper:
            self.assertEqual(paper.source, "doaj")
            self.assertEqual(paper.title, "DOAJ Parser Test")
            self.assertEqual(paper.doi, "10.1000/doaj-test")

    def test_parse_doaj_item_invalid(self):
        paper = self.searcher._parse_doaj_item({"bibjson": {}})
        self.assertIsNone(paper)


if __name__ == "__main__":
    unittest.main()
