import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

# Ensure the local path is prioritized for imports, especially for 'paper'
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from paper_search_mcp.paper import Paper
from paper_search_mcp.academic_platforms.shodhganga import ShodhgangaSearcher

# --- Sample HTML Snippets based on DSpace structure assumptions ---

# This is a HYPOTHETICAL HTML structure. It needs to be validated against the actual Shodhganga website.
SAMPLE_SHODHGANGA_RESULT_ITEM_HTML = """
<div class="ds-artifact-item">
    <div class="row">
        <div class="col-sm-10">
            <h4 class="discovery-result-title">
                <a href="/jspui/handle/10603/12345">A Study on Fictional Technology</a>
            </h4>
            <div class="authors">
                <span title="author">Researcher, Anand R.</span>;
                <span title="author">Guide, Priya S.</span>
            </div>
            <div class="publisher">University of Example, Department of Studies</div>
            <div class="dateinfo">Issued Date: 2023-01-15</div>
            <div class="abstract-full">
                This thesis explores the impact of fictional technology on modern society.
                It covers various aspects and provides detailed analysis.
            </div>
            <div class="item-uri">
                <a href="/jspui/handle/10603/12345">/jspui/handle/10603/12345</a>
            </div>
        </div>
    </div>
</div>
"""

SAMPLE_SHODHGANGA_SEARCH_PAGE_HTML_TEMPLATE = """
<html>
<head><title>Search Results</title></head>
<body>
    <div class="discovery-result-results">
        {items_html}
    </div>
    {pagination_html}
</body>
</html>
"""

SAMPLE_SHODHGANGA_PAGINATION_NEXT = '<a class="next-page" href="/simple-search?query=test&start=10">Next</a>'
NO_RESULTS_HTML = """
<html><body><div class="discovery-result-results"></div></body></html>
"""


class TestShodhgangaSearcher(unittest.TestCase):

    def setUp(self):
        self.searcher = ShodhgangaSearcher()
        # Keep a reference to the original session for tests that might need it (though not typical for unit tests)
        self.original_session = self.searcher.session

    def tearDown(self):
        # Restore original session if it was modified
        self.searcher.session = self.original_session

    @patch('requests.Session.get')
    def test_search_url_construction_and_basic_call(self, mock_get):
        """Test if the search method constructs the URL and params correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = NO_RESULTS_HTML # No results needed for this test
        mock_get.return_value = mock_response

        self.searcher.search("test query", max_results=5)

        expected_url = "https://shodhganga.inflibnet.ac.in/simple-search"
        expected_params = {
            'query': "test query",
            'rpp': 5,
            'sort_by': 'score',
            'order': 'desc',
            'start': 0
        }
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], expected_url)
        self.assertDictEqual(kwargs['params'], expected_params)

    def test_parse_single_item_success(self):
        """Test parsing a single valid HTML item."""
        from bs4 import BeautifulSoup
        item_soup = BeautifulSoup(SAMPLE_SHODHGANGA_RESULT_ITEM_HTML, 'html.parser')

        paper = self.searcher._parse_single_item(item_soup.select_one('div.ds-artifact-item'), self.searcher.BASE_URL)

        self.assertIsNotNone(paper)
        self.assertEqual(paper.title, "A Study on Fictional Technology")
        self.assertListEqual(paper.authors, ["Researcher, Anand R.", "Guide, Priya S."])
        self.assertEqual(paper.url, "https://shodhganga.inflibnet.ac.in/jspui/handle/10603/12345")
        self.assertEqual(paper.abstract[:50], "This thesis explores the impact of fictional tech")
        self.assertEqual(paper.published_date, datetime(2023, 1, 1))
        self.assertEqual(paper.source, "shodhganga")
        self.assertTrue(paper.paper_id.startswith("shodhganga_"))

    def test_parse_single_item_missing_fields(self):
        """Test parsing an item with some missing fields."""
        from bs4 import BeautifulSoup
        html_missing_author_abstract = """
        <div class="ds-artifact-item">
            <h4 class="discovery-result-title"><a href="/handle/123/broken">Minimal Item</a></h4>
            <div class="dateinfo">2021</div>
        </div>
        """
        item_soup = BeautifulSoup(html_missing_author_abstract, 'html.parser')
        paper = self.searcher._parse_single_item(item_soup.select_one('div.ds-artifact-item'), self.searcher.BASE_URL)

        self.assertIsNotNone(paper)
        self.assertEqual(paper.title, "Minimal Item")
        self.assertListEqual(paper.authors, ["Unknown Author"]) # Default value
        self.assertEqual(paper.abstract, "No abstract available.") # Default value
        self.assertEqual(paper.published_date, datetime(2021, 1, 1))
        self.assertEqual(paper.url, "https://shodhganga.inflibnet.ac.in/handle/123/broken")


    @patch('requests.Session.get')
    def test_search_parses_results(self, mock_get):
        """Test that the search method uses _parse_single_item and returns Paper objects."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Simulate a page with one item and no next page link
        mock_response.content = SAMPLE_SHODHGANGA_SEARCH_PAGE_HTML_TEMPLATE.format(
            items_html=SAMPLE_SHODHGANGA_RESULT_ITEM_HTML,
            pagination_html=""
        )
        mock_get.return_value = mock_response

        papers = self.searcher.search("fictional technology", max_results=1)

        self.assertEqual(len(papers), 1)
        self.assertIsInstance(papers[0], Paper)
        self.assertEqual(papers[0].title, "A Study on Fictional Technology")

    @patch('requests.Session.get')
    def test_search_handles_no_results(self, mock_get):
        """Test search with a response that contains no results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = NO_RESULTS_HTML
        mock_get.return_value = mock_response

        papers = self.searcher.search("nonexistent query", max_results=10)
        self.assertEqual(len(papers), 0)

    @patch('requests.Session.get')
    def test_search_pagination_logic(self, mock_get):
        """Test that search attempts to fetch multiple pages if max_results implies it."""
        # First response: one item + next page link
        response1_html = SAMPLE_SHODHGANGA_SEARCH_PAGE_HTML_TEMPLATE.format(
            items_html=SAMPLE_SHODHGANGA_RESULT_ITEM_HTML.replace("12345", "page1item"),
            pagination_html=SAMPLE_SHODHGANGA_PAGINATION_NEXT
        )
        mock_response1 = MagicMock(status_code=200, content=response1_html.encode('utf-8'))

        # Second response: another item, no next page link
        item2_html = SAMPLE_SHODHGANGA_RESULT_ITEM_HTML.replace(
            "A Study on Fictional Technology", "Another Study"
        ).replace("12345", "page2item")
        response2_html = SAMPLE_SHODHGANGA_SEARCH_PAGE_HTML_TEMPLATE.format(
            items_html=item2_html,
            pagination_html=""
        )
        mock_response2 = MagicMock(status_code=200, content=response2_html.encode('utf-8'))

        # Third response (should not be strictly needed if max_results is met, but good for safety)
        mock_response_empty = MagicMock(status_code=200, content=NO_RESULTS_HTML.encode('utf-8'))

        mock_get.side_effect = [mock_response1, mock_response2, mock_response_empty]

        papers = self.searcher.search("test query", max_results=2) # Request 2 results

        self.assertEqual(len(papers), 2)
        self.assertEqual(mock_get.call_count, 2) # Should make two calls due to pagination

        # Check params for the second call (start index should have incremented)
        args1, kwargs1 = mock_get.call_args_list[0]
        args2, kwargs2 = mock_get.call_args_list[1]

        self.assertEqual(kwargs1['params']['start'], 0)
        # rpp is min(max_results_remaining, configured_rpp_cap_in_shodhganga.py)
        # max_results=2, so initial rpp = min(2, 20) = 2
        self.assertEqual(kwargs1['params']['rpp'], 2)

        self.assertEqual(kwargs2['params']['start'], 2) # start = 0 + 2 (rpp from first call)
        self.assertEqual(kwargs2['params']['rpp'], 2) # rpp = min(2-1, 20) = 1, but code uses initial rpp throughout loop.
                                                      # This might be an area for refinement in shodhganga.py if needed,
                                                      # but current test reflects current code.
                                                      # The loop condition `len(papers) < max_results` will stop it.

    def test_download_pdf_not_implemented(self):
        """Test that download_pdf raises NotImplementedError."""
        with self.assertRaisesRegex(NotImplementedError, "Shodhganga does not provide direct PDF downloads"):
            self.searcher.download_pdf("some_id", "./downloads")

    def test_read_paper_returns_message(self):
        """Test that read_paper returns the correct informational message."""
        message = self.searcher.read_paper("some_id")
        self.assertIn("Shodhganga papers cannot be read directly", message)

    @patch('requests.Session.get')
    def test_search_http_error(self, mock_get):
        """Test search handling of HTTP errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("Server Error")
        mock_get.return_value = mock_response

        papers = self.searcher.search("test query", max_results=10)
        self.assertEqual(len(papers), 0) # Should return empty list on error

    def test_parse_single_item_no_title_tag(self):
        """Test _parse_single_item when the main title tag is missing."""
        from bs4 import BeautifulSoup
        html_no_title = """<div class="ds-artifact-item"><div>No title here</div></div>"""
        item_soup = BeautifulSoup(html_no_title, 'html.parser')
        paper = self.searcher._parse_single_item(item_soup.select_one('div.ds-artifact-item'), self.searcher.BASE_URL)
        self.assertIsNone(paper)

if __name__ == '__main__':
    print("NOTE: These tests for ShodhgangaSearcher rely on ASSUMED HTML structures.")
    print("Actual functionality needs validation against the live Shodhganga website.\n")
    unittest.main()
