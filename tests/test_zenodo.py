import unittest
import requests

from paper_search_mcp.academic_platforms.zenodo import ZenodoSearcher


def check_api_accessible() -> bool:
    """Check whether Zenodo API is reachable."""
    try:
        response = requests.get(
            "https://zenodo.org/api/records",
            params={"q": "machine learning", "size": 1},
            timeout=10,
        )
        return response.status_code == 200
    except Exception:
        return False


class TestZenodoSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible()
        if not cls.api_accessible:
            print("\nWarning: Zenodo API is not accessible, network tests will be skipped")

    def setUp(self):
        self.searcher = ZenodoSearcher()

    def test_search_basic(self):
        if not self.api_accessible:
            self.skipTest("Zenodo API is not accessible")

        papers = self.searcher.search("machine learning", max_results=3)
        self.assertIsInstance(papers, list)
        self.assertTrue(len(papers) >= 0)

        if papers:
            first = papers[0]
            self.assertTrue(first.title)
            self.assertEqual(first.source, "zenodo")

    def test_extract_record_id(self):
        self.assertEqual(self.searcher._extract_record_id("10.5281/zenodo.1234567"), "1234567")
        self.assertEqual(self.searcher._extract_record_id("1234567"), "1234567")
        self.assertEqual(self.searcher._extract_record_id("not-a-zenodo-id"), "")

    def test_parse_record_minimal(self):
        hit = {
            "id": 12345,
            "doi": "10.5281/zenodo.12345",
            "metadata": {
                "title": "Zenodo Parser Test",
                "creators": [{"name": "Alice Example"}, {"name": "Bob Example"}],
                "description": "<p>Test abstract</p>",
                "publication_date": "2024-01-15",
            },
            "files": [
                {
                    "key": "paper.pdf",
                    "links": {"self": "https://zenodo.org/records/12345/files/paper.pdf"},
                }
            ],
            "links": {"html": "https://zenodo.org/record/12345"},
        }

        paper = self.searcher._parse_record(hit)
        self.assertIsNotNone(paper)
        if paper:
            self.assertEqual(paper.source, "zenodo")
            self.assertEqual(paper.title, "Zenodo Parser Test")
            self.assertEqual(paper.doi, "10.5281/zenodo.12345")
            self.assertTrue(paper.pdf_url.endswith("paper.pdf"))

    def test_parse_record_invalid(self):
        paper = self.searcher._parse_record({"metadata": {}})
        self.assertIsNone(paper)


if __name__ == "__main__":
    unittest.main()
