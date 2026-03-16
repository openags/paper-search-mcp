import unittest
import requests

from paper_search_mcp.academic_platforms.hal import HALSearcher


def check_api_accessible() -> bool:
    """Check whether HAL API is reachable."""
    try:
        response = requests.get(
            "https://api.archives-ouvertes.fr/search/",
            params={"q": "machine learning", "rows": 1, "wt": "json"},
            timeout=10,
        )
        return response.status_code == 200
    except Exception:
        return False


class TestHALSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible()
        if not cls.api_accessible:
            print("\nWarning: HAL API is not accessible, network tests will be skipped")

    def setUp(self):
        self.searcher = HALSearcher()

    def test_search_basic(self):
        if not self.api_accessible:
            self.skipTest("HAL API is not accessible")

        papers = self.searcher.search("machine learning", max_results=3)
        self.assertIsInstance(papers, list)
        self.assertTrue(len(papers) >= 0)

        if papers:
            first = papers[0]
            self.assertTrue(first.title)
            self.assertEqual(first.source, "hal")

    def test_normalise_id(self):
        self.assertEqual(self.searcher._normalise_id("hal:hal-01234567"), "hal-01234567")
        self.assertEqual(self.searcher._normalise_id("hal-01234567"), "hal-01234567")

    def test_parse_doc_minimal(self):
        doc = {
            "halId_s": "hal-01234567",
            "title_s": ["HAL Parser Test"],
            "authFullName_s": ["Alice Example", "Bob Example"],
            "abstract_s": ["This is a test abstract"],
            "doiId_s": "10.1000/hal-test",
            "publicationDateY_i": 2023,
            "fileMain_s": "https://hal.science/hal-01234567/document",
            "uri_s": "https://hal.science/hal-01234567",
        }

        paper = self.searcher._parse_doc(doc)
        self.assertIsNotNone(paper)
        if paper:
            self.assertEqual(paper.source, "hal")
            self.assertEqual(paper.paper_id, "hal:hal-01234567")
            self.assertEqual(paper.title, "HAL Parser Test")
            self.assertEqual(paper.doi, "10.1000/hal-test")

    def test_parse_doc_invalid(self):
        paper = self.searcher._parse_doc({})
        self.assertIsNone(paper)


if __name__ == "__main__":
    unittest.main()
