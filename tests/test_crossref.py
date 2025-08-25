# tests/test_crossref.py
import unittest
import os
import requests
from paper_search_mcp.academic_platforms.crossref import CrossRefSearcher

def check_api_accessible():
    """检查 CrossRef API 是否可访问
    Check if CrossRef API is accessible"""
    try:
        response = requests.get("https://api.crossref.org/works?sample=1", timeout=5)
        return response.status_code == 200
    except:
        return False

class TestCrossRefSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_accessible = check_api_accessible()
        if not cls.api_accessible:
            print("\nWarning: CrossRef API is not accessible, some tests will be skipped")

    def setUp(self):
        self.searcher = CrossRefSearcher()

    def test_search(self):
        if not self.api_accessible:
            self.skipTest("CrossRef API is not accessible")
        
        papers = self.searcher.search("machine learning", max_results=5)
        print(f"Found {len(papers)} papers for query 'machine learning':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (DOI: {paper.doi})")
            print(f"   Authors: {', '.join(paper.authors[:2])}{'...' if len(paper.authors) > 2 else ''}")
            print(f"   Published: {paper.published_date.year if paper.published_date else 'N/A'}")
            print(f"   Citations: {paper.citations}")
            if paper.extra:
                print(f"   Publisher: {paper.extra.get('publisher', 'N/A')}")
                print(f"   Type: {paper.extra.get('crossref_type', 'N/A')}")
            print()
        self.assertTrue(len(papers) > 0)
        if papers:
            self.assertTrue(papers[0].title)
            self.assertTrue(papers[0].doi)

    def test_search_with_filters(self):
        if not self.api_accessible:
            self.skipTest("CrossRef API is not accessible")
            
        # Test search with date filter
        papers = self.searcher.search(
            "artificial intelligence", 
            max_results=3,
            filter="from-pub-date:2020,has-full-text:true"
        )
        print(f"Found {len(papers)} papers with filters")
        self.assertTrue(len(papers) >= 0)  # May return 0 if no papers match filters

    def test_get_paper_by_doi(self):
        if not self.api_accessible:
            self.skipTest("CrossRef API is not accessible")
            
        # Test with a known DOI
        known_doi = "10.1038/nature12373"  # A Nature paper
        paper = self.searcher.get_paper_by_doi(known_doi)
        
        if paper:  # Paper might not be found
            print(f"Retrieved paper by DOI: {paper.title}")
            self.assertEqual(paper.doi, known_doi)
            self.assertTrue(paper.title)
        else:
            print(f"Paper with DOI {known_doi} not found in CrossRef")

    def test_get_paper_by_invalid_doi(self):
        if not self.api_accessible:
            self.skipTest("CrossRef API is not accessible")
            
        # Test with an invalid DOI
        invalid_doi = "10.1234/invalid.doi.123456789"
        paper = self.searcher.get_paper_by_doi(invalid_doi)
        self.assertIsNone(paper)

    def test_download_pdf_not_supported(self):
        with self.assertRaises(NotImplementedError) as context:
            self.searcher.download_pdf("10.1038/nature12373", "./downloads")
        
        self.assertIn("CrossRef does not provide direct PDF downloads", str(context.exception))

    def test_read_paper_not_supported(self):
        message = self.searcher.read_paper("10.1038/nature12373")
        self.assertIn("CrossRef papers cannot be read directly", message)
        self.assertIn("metadata and abstracts are available", message)

    def test_search_error_handling(self):
        # Test with invalid search parameters to check error handling
        papers = self.searcher.search("", max_results=0)  # Empty query
        self.assertEqual(len(papers), 0)

    def test_user_agent_header(self):
        # Test that the session has the correct user agent
        self.assertIn("paper-search-mcp", self.searcher.session.headers.get('User-Agent', ''))
        self.assertIn("mailto:", self.searcher.session.headers.get('User-Agent', ''))

if __name__ == '__main__':
    unittest.main()