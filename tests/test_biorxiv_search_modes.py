import unittest
from unittest.mock import Mock

from paper_search_mcp.academic_platforms.biorxiv import BioRxivSearcher


def _mock_response(collection):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"collection": collection}
    return response


def _paper_item(doi: str = "10.1101/2024.01.01.123456"):
    return {
        "doi": doi,
        "title": "Test paper",
        "authors": "A One; B Two",
        "abstract": "Abstract",
        "date": "2024-01-01",
        "version": "1",
        "category": "bioinformatics",
    }


class TestBioRxivSearchModes(unittest.TestCase):
    def setUp(self):
        self.searcher = BioRxivSearcher()
        self.searcher.session.get = Mock(return_value=_mock_response([_paper_item()]))

    def test_category_search_uses_json_endpoint(self):
        papers = self.searcher.search("bioinformatics", max_results=1, days=30)
        self.assertEqual(len(papers), 1)
        called_url = self.searcher.session.get.call_args[0][0]
        self.assertIn("/0/json", called_url)
        self.assertIn("?category=bioinformatics", called_url)

    def test_doi_lookup_uses_doi_endpoint(self):
        doi = "10.1101/2024.01.01.999999"
        papers = self.searcher.search(doi, max_results=1)
        self.assertEqual(len(papers), 1)
        called_url = self.searcher.session.get.call_args[0][0]
        self.assertEqual(called_url, f"https://api.biorxiv.org/details/biorxiv/{doi}/na/json")

    def test_date_range_query_uses_interval(self):
        papers = self.searcher.search("2024-01-01/2024-01-31", max_results=1)
        self.assertEqual(len(papers), 1)
        called_url = self.searcher.session.get.call_args[0][0]
        self.assertEqual(
            called_url,
            "https://api.biorxiv.org/details/biorxiv/2024-01-01/2024-01-31/0/json"
        )

    def test_blank_query_returns_recent_without_category(self):
        papers = self.searcher.search("", max_results=1, days=7)
        self.assertEqual(len(papers), 1)
        called_url = self.searcher.session.get.call_args[0][0]
        self.assertIn("/0/json", called_url)
        self.assertNotIn("?category=", called_url)


if __name__ == "__main__":
    unittest.main()
