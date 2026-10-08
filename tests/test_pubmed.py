import unittest
from unittest.mock import Mock, patch

import requests

from paper_search_mcp.academic_platforms.pmc import PMCSearcher
from paper_search_mcp.academic_platforms.pubmed import PubMedSearcher

LINK_GET = "paper_search_mcp.academic_platforms.pubmed.requests.get"


def _links(*pmc_ids):
    """An ELink pubmed_pmc answer naming the given PMC ids, or none."""
    linksetdbs = [{"dbto": "pmc", "linkname": "pubmed_pmc", "links": list(pmc_ids)}] if pmc_ids else []
    response = Mock(spec=requests.Response)
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {"linksets": [{"dbfrom": "pubmed", "linksetdbs": linksetdbs}]}
    return response


class TestPubMedSearcher(unittest.TestCase):
    def test_search(self):
        searcher = PubMedSearcher()
        papers = searcher.search("machine learning", max_results=10)
        print(f"Found {len(papers)} papers for query 'machine learning':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id})")
        self.assertEqual(len(papers), 10)
        self.assertTrue(papers[0].title)

    def test_pdf_unsupported_without_a_pmc_copy(self):
        searcher = PubMedSearcher()
        with patch(LINK_GET, return_value=_links()):
            with self.assertRaises(NotImplementedError):
                searcher.download_pdf("12345678", "./downloads")

    def test_read_paper_message_without_a_pmc_copy(self):
        searcher = PubMedSearcher()
        with patch(LINK_GET, return_value=_links()):
            message = searcher.read_paper("12345678")
        self.assertIn("PubMed papers cannot be read directly", message)

    def test_read_paper_returns_the_pmc_full_text(self):
        searcher = PubMedSearcher()
        with (
            patch(LINK_GET, return_value=_links("7095418")) as link,
            patch.object(PMCSearcher, "read_paper", return_value="the whole article") as read,
        ):
            text = searcher.read_paper("32015507", "/tmp/papers")
        self.assertEqual(text, "the whole article")
        read.assert_called_once_with("PMC7095418", "/tmp/papers")
        self.assertEqual(link.call_args.kwargs["params"]["id"], "32015507")

    def test_download_pdf_returns_the_pmc_pdf(self):
        searcher = PubMedSearcher()
        with (
            patch(LINK_GET, return_value=_links("7095418")),
            patch.object(PMCSearcher, "download_pdf", return_value="/tmp/papers/PMC7095418.pdf") as download,
        ):
            path = searcher.download_pdf("32015507", "/tmp/papers")
        self.assertEqual(path, "/tmp/papers/PMC7095418.pdf")
        download.assert_called_once_with("PMC7095418", "/tmp/papers")

    def test_a_failed_pmc_lookup_is_not_reported_as_no_copy(self):
        searcher = PubMedSearcher()
        with patch(LINK_GET, side_effect=requests.ConnectionError("reset")):
            message = searcher.read_paper("32015507")
        self.assertIn("lookup failed", message)


if __name__ == "__main__":
    unittest.main()
