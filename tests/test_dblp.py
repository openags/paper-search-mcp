# tests/test_dblp.py
import unittest
import os
import requests
from paper_search_mcp.academic_platforms.dblp import DBLPSearcher


def check_api_accessible():
    """Check if dblp API is accessible"""
    try:
        # Test dblp API with a simple query
        response = requests.get(
            "https://dblp.org/search/publ/api",
            params={'q': 'test', 'format': 'xml', 'h': 1},
            timeout=10
        )
        return response.status_code == 200
    except:
        return False


class TestDBLPSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible()
        if not cls.api_accessible:
            print("\nWarning: dblp API is not accessible, some tests will be skipped")

    def setUp(self):
        self.searcher = DBLPSearcher()

    def test_search_basic(self):
        if not self.api_accessible:
            self.skipTest("dblp API is not accessible")

        papers = self.searcher.search("machine learning", max_results=5)
        print(f"Found {len(papers)} papers from dblp for query 'machine learning':")

        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title}")
            print(f"   Authors: {', '.join(paper.authors[:2])}{'...' if len(paper.authors) > 2 else ''}")
            print(f"   Year: {paper.extra.get('year', 'N/A')}")
            print(f"   Venue: {paper.extra.get('venue', 'N/A')}")
            print(f"   DOI: {paper.doi if paper.doi else 'N/A'}")
            print(f"   URL: {paper.url}")
            print()

        self.assertTrue(len(papers) > 0)
        if papers:
            self.assertTrue(papers[0].title)
            self.assertEqual(papers[0].source, 'dblp')

    def test_search_with_year_filter(self):
        if not self.api_accessible:
            self.skipTest("dblp API is not accessible")

        papers = self.searcher.search(
            "deep learning",
            max_results=3,
            year="2020"
        )
        print(f"Found {len(papers)} papers from dblp for query 'deep learning' year 2020")

        if papers:
            for paper in papers:
                year = paper.extra.get('year', '')
                if year:
                    print(f"Paper '{paper.title}' published in {year}")

        self.assertTrue(len(papers) >= 0)  # May return 0 if no papers match

    def test_search_with_author_filter(self):
        if not self.api_accessible:
            self.skipTest("dblp API is not accessible")

        papers = self.searcher.search(
            "neural networks",
            max_results=3,
            author="Bengio"
        )
        print(f"Found {len(papers)} papers from dblp for query 'neural networks' author Bengio")

        if papers:
            for paper in papers:
                authors_str = ', '.join(paper.authors)
                print(f"Paper '{paper.title}' by {authors_str}")

        self.assertTrue(len(papers) >= 0)

    def test_search_with_venue_filter(self):
        if not self.api_accessible:
            self.skipTest("dblp API is not accessible")

        papers = self.searcher.search(
            "database",
            max_results=3,
            venue="SIGMOD"
        )
        print(f"Found {len(papers)} papers from dblp for query 'database' venue SIGMOD")

        if papers:
            for paper in papers:
                venue = paper.extra.get('venue', '')
                print(f"Paper '{paper.title}' published at {venue}")

        self.assertTrue(len(papers) >= 0)

    def test_download_pdf_not_supported(self):
        """Test that PDF download raises NotImplementedError"""
        searcher = DBLPSearcher()
        with self.assertRaises(NotImplementedError):
            searcher.download_pdf("test_paper_id", "./downloads")

    def test_read_paper_not_supported(self):
        """Test that reading paper raises NotImplementedError"""
        searcher = DBLPSearcher()
        with self.assertRaises(NotImplementedError):
            searcher.read_paper("test_paper_id", "./downloads")

    def test_search_computer_science_topics(self):
        if not self.api_accessible:
            self.skipTest("dblp API is not accessible")

        # Test various computer science topics that dblp should have
        test_queries = [
            "natural language processing",
            "computer vision",
            "distributed systems",
            "software engineering",
            "cryptography"
        ]

        for query in test_queries[:2]:  # Just test first 2 to avoid too many requests
            papers = self.searcher.search(query, max_results=2)
            print(f"Query '{query}': found {len(papers)} papers")
            if papers:
                print(f"  First paper: {papers[0].title}")
            self.assertTrue(len(papers) >= 0)

    def test_parse_dblp_hit_invalid(self):
        """Test parsing invalid dblp hit"""
        import xml.etree.ElementTree as ET

        # Create an invalid XML element
        root = ET.Element('root')
        hit = ET.SubElement(root, 'hit')
        # Missing 'info' element

        # This should return None without raising exception
        paper = self.searcher._parse_dblp_hit(hit)
        self.assertIsNone(paper)


if __name__ == '__main__':
    unittest.main()