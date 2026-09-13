# tests/test_arxiv.py
import unittest
from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher

class TestArxivSearcher(unittest.TestCase):
    def test_search(self):
        searcher = ArxivSearcher()
        papers = searcher.search("machine learning", max_results=10)
        print(f"Found {len(papers)} papers for query 'machine learning':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id})")
        if not papers:
            self.skipTest("arXiv API is unavailable or rate-limited")
        self.assertEqual(len(papers), 10)
        self.assertTrue(papers[0].title)

    def test_base_url_uses_https(self):
        # #101: the plain-HTTP endpoint hangs on read; HTTPS answers fast
        self.assertTrue(ArxivSearcher.BASE_URL.startswith("https://"))

    def test_build_search_query_quotes_plain_multiword(self):
        # #101: unquoted multi-word `all:` queries hang; phrase-quote them
        b = ArxivSearcher._build_search_query
        self.assertEqual(b("Attention Is All You Need"), 'all:"Attention Is All You Need"')
        self.assertEqual(b("transformer"), "all:transformer")

    def test_build_search_query_leaves_structured_queries_alone(self):
        b = ArxivSearcher._build_search_query
        self.assertEqual(b("ti:Attention Is All You Need"), "ti:Attention Is All You Need")
        self.assertEqual(b("au:Hinton AND ti:dropout"), "au:Hinton AND ti:dropout")
        self.assertEqual(b('all:"quoted phrase"'), 'all:"quoted phrase"')


if __name__ == '__main__':
    unittest.main()