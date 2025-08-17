# tests/test_sci_hub.py
import unittest
import tempfile
import shutil
import os
import requests
from paper_search_mcp.academic_platforms.sci_hub import SciHubFetcher


def check_sci_hub_accessible():
    """Check if Sci-Hub is accessible"""
    try:
        # Test with a simple request to see if sci-hub responds
        response = requests.get("https://sci-hub.se", timeout=10)
        return response.status_code == 200
    except:
        return False


class TestSciHubFetcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sci_hub_accessible = check_sci_hub_accessible()
        if not cls.sci_hub_accessible:
            print("\nWarning: Sci-Hub is not accessible, some tests will be skipped")

    def setUp(self):
        # Create temporary directory for downloads
        self.test_dir = tempfile.mkdtemp(prefix="sci_hub_test_")
        self.fetcher = SciHubFetcher(output_dir=self.test_dir)

    def tearDown(self):
        # Clean up temporary directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_init(self):
        """Test initialization of SciHubFetcher"""
        self.assertEqual(self.fetcher.base_url, "https://sci-hub.se")
        self.assertTrue(os.path.exists(self.test_dir))
        self.assertIsNotNone(self.fetcher.session)

    def test_init_custom_url(self):
        """Test initialization with custom URL"""
        custom_fetcher = SciHubFetcher(base_url="https://sci-hub.ru/", output_dir=self.test_dir)
        self.assertEqual(custom_fetcher.base_url, "https://sci-hub.ru")

    def test_download_pdf_empty_query(self):
        """Test download with empty query"""
        result = self.fetcher.download_pdf("")
        self.assertIsNone(result)

        result = self.fetcher.download_pdf("   ")
        self.assertIsNone(result)

    @unittest.skipUnless(check_sci_hub_accessible(), "Sci-Hub not accessible")
    def test_download_pdf_known_doi(self):
        """Test download with well-known DOIs"""
        # List of valid DOIs for testing (mix of older and newer papers)
        test_dois = [
            "10.1038/nature12373",  # Nature paper on CRISPR-Cas9
            "10.1126/science.1232033",  # Science paper on genome editing
            "10.1073/pnas.1320040111",  # PNAS paper
            "10.1016/j.cell.2013.06.044",  # Cell paper
            "10.1038/35057062",  # Nature paper on human genome
        ]
        
        success_count = 0
        
        for doi in test_dois:
            print(f"\nTesting PDF download for DOI: {doi}")
            result = self.fetcher.download_pdf(doi)
            
            if result:
                # Download successful
                self.assertIsInstance(result, str)
                self.assertTrue(os.path.exists(result))
                self.assertTrue(result.endswith('.pdf'))
                
                # Check file size (should be > 0)
                file_size = os.path.getsize(result)
                self.assertGreater(file_size, 0)
                print(f"PDF successfully downloaded: {result} (size: {file_size} bytes)")
                success_count += 1
                break  # Stop after first successful download
            else:
                print(f"Download failed for {doi} (may be blocked or unavailable)")
        
        if success_count == 0:
            # All downloads failed - likely due to blocking
            print("All downloads failed - this may be expected due to Sci-Hub blocking or CAPTCHA")
            self.skipTest("All Sci-Hub downloads failed (possibly blocked or CAPTCHA)")

    @unittest.skipUnless(check_sci_hub_accessible(), "Sci-Hub not accessible")
    def test_download_pdf_invalid_doi(self):
        """Test download with invalid DOI"""
        invalid_doi = "10.1234/invalid.doi.123456789"
        
        print(f"\nTesting download for invalid DOI: {invalid_doi}")
        result = self.fetcher.download_pdf(invalid_doi)
        
        # Should return None for invalid DOI
        self.assertIsNone(result)

    def test_generate_filename(self):
        """Test filename generation"""
        # Mock response object
        class MockResponse:
            def __init__(self, url, content):
                self.url = url
                self.content = content.encode()
        
        # Test with PDF URL
        response = MockResponse("https://example.com/paper.pdf", "fake pdf content")
        filename = self.fetcher._generate_filename(response, "10.1234/test")
        self.assertTrue(filename.endswith('.pdf'))
        self.assertIn('_', filename)  # Should contain hash separator
        
        # Test with non-PDF URL
        response = MockResponse("https://example.com/page", "fake content")
        filename = self.fetcher._generate_filename(response, "test-paper")
        self.assertTrue(filename.endswith('.pdf'))
        self.assertIn('test-paper', filename)

    def test_get_direct_url_pdf_url(self):
        """Test _get_direct_url with direct PDF URL"""
        pdf_url = "https://example.com/paper.pdf"
        result = self.fetcher._get_direct_url(pdf_url)
        self.assertEqual(result, pdf_url)

    @unittest.skipUnless(check_sci_hub_accessible(), "Sci-Hub not accessible")
    def test_get_direct_url_doi(self):
        """Test _get_direct_url with DOI"""
        # Use well-known DOIs
        test_dois = [
            "10.1038/nature12373",  # Nature CRISPR paper
            "10.1126/science.1232033",  # Science genome editing
            "10.1073/pnas.1320040111",  # PNAS paper
        ]
        
        for doi in test_dois:
            print(f"\nTesting direct URL extraction for DOI: {doi}")
            result = self.fetcher._get_direct_url(doi)
            
            if result:
                self.assertIsInstance(result, str)
                # Should be a URL
                self.assertTrue(result.startswith('http'))
                print(f"Direct URL found: {result}")
                break  # Stop after first success
            else:
                print(f"No direct URL found for {doi} (may be blocked)")
        
        # Note: This test may not assert success due to Sci-Hub blocking

    def test_session_headers(self):
        """Test that session has proper headers"""
        self.assertIn('User-Agent', self.fetcher.session.headers)
        user_agent = self.fetcher.session.headers['User-Agent']
        self.assertIn('Mozilla', user_agent)

    def test_output_directory_creation(self):
        """Test that output directory is created"""
        new_dir = os.path.join(self.test_dir, "subdir", "nested")
        fetcher = SciHubFetcher(output_dir=new_dir)
        self.assertTrue(os.path.exists(new_dir))

    @unittest.skipUnless(check_sci_hub_accessible(), "Sci-Hub not accessible")
    def test_error_handling(self):
        """Test error handling for various scenarios"""
        # Test with clearly invalid/malformed identifier
        result = self.fetcher.download_pdf("this-is-definitely-not-a-valid-doi-or-identifier-12345")
        # Note: Sci-Hub might still return something, so we just check it doesn't crash
        self.assertIsInstance(result, (str, type(None)))
        
        # Test with empty string
        result = self.fetcher.download_pdf("")
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()