# tests/test_scopus.py
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

# Adjust import path based on your project structure
from paper_search_mcp.academic_platforms.scopus import ScopusSearcher
from paper_search_mcp.paper import Paper

# Use the same API key for consistency if needed by tests, though it will be mocked
API_KEY = "84331d94db0ffe11c2b7c199fbdc8f52" # This is not used directly anymore but good for reference

class TestScopusSearcher(unittest.TestCase):

    def setUp(self):
        # We will set the API key via environment variable in each test method
        # or pass it directly if needed for a specific test.
        pass

    @patch.dict(os.environ, {"SCOPUS_API_KEY": "test_api_key_from_env"})
    @patch('paper_search_mcp.academic_platforms.scopus.ElsClient')
    @patch('paper_search_mcp.academic_platforms.scopus.ElsSearch')
    def test_search_success_from_env_key(self, MockElsSearch, MockElsClient):
        searcher = ScopusSearcher() # Should pick up from mocked env
        # Mock ElsClient instance
        mock_client_instance = MockElsClient.return_value

        # Mock ElsSearch instance and its execute method
        mock_search_instance = MockElsSearch.return_value
        mock_search_instance.results = [
            {
                'dc:identifier': 'SCOPUS_ID:12345',
                'dc:title': 'Test Paper 1',
                'author': [{'authname': 'Author A'}, {'authname': 'Author B'}],
                'dc:description': 'This is a test abstract.',
                'prism:doi': '10.1000/test1',
                'prism:url': 'http://scopus.com/test1',
                'prism:coverDate': '2023-01-15',
            },
            {
                'dc:identifier': 'SCOPUS_ID:67890',
                'dc:title': 'Test Paper 2',
                'author': [{'authname': 'Author C'}],
                'dc:description': 'Another test abstract.',
                'prism:doi': '10.1000/test2',
                'prism:url': 'http://scopus.com/test2',
                'prism:coverDate': '2023-02-20',
                # Missing some fields to test robustness
            }
        ]
        mock_search_instance.execute = MagicMock()

        MockElsSearch.return_value = mock_search_instance

        query = "test query"
        max_results = 2
        papers = searcher.search(query, max_results=max_results)

        # Assertions
        MockElsClient.assert_called_once_with("test_api_key_from_env")
        MockElsSearch.assert_called_once_with(query, 'scopus')
        mock_search_instance.execute.assert_called_once_with(mock_client_instance, get_all=False, count=max_results)

        self.assertEqual(len(papers), 2)

        # Check paper 1
        self.assertIsInstance(papers[0], Paper)
        self.assertEqual(papers[0].paper_id, '12345')
        self.assertEqual(papers[0].title, 'Test Paper 1')
        self.assertEqual(papers[0].authors, ['Author A', 'Author B'])
        self.assertEqual(papers[0].abstract, 'This is a test abstract.')
        self.assertEqual(papers[0].doi, '10.1000/test1')
        self.assertEqual(papers[0].url, 'http://scopus.com/test1')
        self.assertEqual(papers[0].published_date, datetime(2023, 1, 15))
        self.assertEqual(papers[0].source, 'scopus')

        # Check paper 2 (testing defaults for missing fields)
        self.assertIsInstance(papers[1], Paper)
        self.assertEqual(papers[1].paper_id, '67890')
        self.assertEqual(papers[1].title, 'Test Paper 2')

    @patch('paper_search_mcp.academic_platforms.scopus.ElsClient')
    @patch('paper_search_mcp.academic_platforms.scopus.ElsSearch')
    def test_search_success_with_direct_key(self, MockElsSearch, MockElsClient):
        # Test with API key passed directly, should override env var if present
        with patch.dict(os.environ, {"SCOPUS_API_KEY": "env_key_to_be_overridden"}, clear=True):
            searcher = ScopusSearcher(api_key="direct_test_key")

        mock_client_instance = MockElsClient.return_value
        mock_search_instance = MockElsSearch.return_value
        mock_search_instance.results = [
            {
                'dc:identifier': 'SCOPUS_ID:12345',
                'dc:title': 'Test Paper Direct Key',
                'prism:doi': '10.1000/testdirect',
            }
        ]
        mock_search_instance.execute = MagicMock()
        MockElsSearch.return_value = mock_search_instance

        papers = searcher.search("direct key query", max_results=1)
        MockElsClient.assert_called_once_with("direct_test_key")
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].title, 'Test Paper Direct Key')


    @patch.dict(os.environ, {"SCOPUS_API_KEY": "test_api_key_for_error"})
    @patch('paper_search_mcp.academic_platforms.scopus.ElsClient')
    @patch('paper_search_mcp.academic_platforms.scopus.ElsSearch')
    def test_search_api_error(self, MockElsSearch, MockElsClient):
        searcher = ScopusSearcher()
        # Mock ElsClient instance
        mock_client_instance = MockElsClient.return_value

        # Mock ElsSearch instance to simulate an API error during execute
        mock_search_instance = MockElsSearch.return_value
        mock_search_instance.execute = MagicMock(side_effect=Exception("API Communication Error"))

        MockElsSearch.return_value = mock_search_instance

        query = "error query"
        with self.assertRaises(Exception) as context: # Or a more specific exception if ElsPy raises one
            searcher.search(query, max_results=5)

        self.assertTrue("API Communication Error" in str(context.exception))
        MockElsClient.assert_called_once_with("test_api_key_for_error")


    @patch.dict(os.environ, {"SCOPUS_API_KEY": "test_api_key_no_results"})
    @patch('paper_search_mcp.academic_platforms.scopus.ElsClient')
    @patch('paper_search_mcp.academic_platforms.scopus.ElsSearch')
    def test_search_no_results(self, MockElsSearch, MockElsClient):
        searcher = ScopusSearcher()
        # Mock ElsClient instance
        mock_client_instance = MockElsClient.return_value

        # Mock ElsSearch instance with empty results
        mock_search_instance = MockElsSearch.return_value
        mock_search_instance.results = []
        mock_search_instance.execute = MagicMock()

        MockElsSearch.return_value = mock_search_instance

        papers = searcher.search("empty query", max_results=5)
        self.assertEqual(len(papers), 0)
        MockElsClient.assert_called_once_with("test_api_key_no_results")

    def test_api_key_missing(self):
        # Ensure SCOPUS_API_KEY is not set for this test
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Scopus API key not provided"):
                ScopusSearcher()

    @patch.dict(os.environ, {"SCOPUS_API_KEY": "test_api_key_for_download"})
    def test_download_pdf_not_implemented(self):
        searcher = ScopusSearcher()
        with self.assertRaisesRegex(NotImplementedError, "Direct PDF download from Scopus is not supported via this API."):
            searcher.download_pdf("some_id", "./downloads")

    @patch.dict(os.environ, {"SCOPUS_API_KEY": "test_api_key_for_read"})
    def test_read_paper_not_implemented(self):
        searcher = ScopusSearcher()
        with self.assertRaisesRegex(NotImplementedError, "Reading paper content directly from Scopus is not supported."):
            searcher.read_paper("some_id")

if __name__ == '__main__':
    unittest.main()
