import unittest

from paper_search_mcp.academic_platforms.pubmed import PubMedSearcher


class TestPubMedSearcher(unittest.TestCase):
    def test_search(self):
        searcher = PubMedSearcher()
        papers = searcher.search("machine learning", max_results=10)
        print(f"Found {len(papers)} papers for query 'machine learning':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id})")
        self.assertEqual(len(papers), 10)
        self.assertTrue(papers[0].title)

    def test_pdf_unsupported(self):
        searcher = PubMedSearcher()
        with self.assertRaises(NotImplementedError):
            searcher.download_pdf("12345678", "./downloads")

    def test_read_paper_message(self):
        searcher = PubMedSearcher()
        message = searcher.read_paper("12345678")
        self.assertIn("PubMed papers cannot be read directly", message)


if __name__ == "__main__":
    unittest.main()
