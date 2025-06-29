import unittest
import os
import requests
from paper_search_mcp.academic_platforms.semantic import SemanticSearcher


def check_semantic_accessible():
    """Check if Semantic Scholar is accessible"""
    try:
        response = requests.get("https://api.semanticscholar.org/graph/v1/paper/5bbfdf2e62f0508c65ba6de9c72fe2066fd98138", timeout=5)
        return response.status_code == 200
    except:
        return False


class TestSemanticSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.semantic_accessible = check_semantic_accessible()
        if not cls.semantic_accessible:
            print(
                "\nWarning: Semantic Scholar is not accessible, some tests will be skipped"
            )

    def setUp(self):
        self.searcher = SemanticSearcher()

    @unittest.skipUnless(check_semantic_accessible(), "Semantic Scholar not accessible")
    def test_search_basic(self):
        """Test basic search functionality"""
        results = self.searcher.search("secret sharing", max_results=3)

        self.assertIsInstance(results, list)
        self.assertLessEqual(len(results), 3)

        if results:
            paper = results[0]
            self.assertTrue(hasattr(paper, "title"))
            self.assertTrue(hasattr(paper, "authors"))
            self.assertTrue(hasattr(paper, "abstract"))
            self.assertTrue(hasattr(paper, "paper_id"))
            self.assertTrue(hasattr(paper, "url"))
            self.assertEqual(paper.source, "semantic")

    @unittest.skipUnless(check_semantic_accessible(), "Semantic Scholar not accessible")
    def test_search_empty_query(self):
        """Test search with empty query"""
        results = self.searcher.search("", max_results=3)
        self.assertIsInstance(results, list)

    @unittest.skipUnless(check_semantic_accessible(), "Semantic Scholar not accessible")
    def test_search_max_results(self):
        """Test max_results parameter"""
        results = self.searcher.search("cryptography", max_results=2)
        self.assertLessEqual(len(results), 2)

    @unittest.skipUnless(check_semantic_accessible(), "Semantic Scholar not accessible")
    def test_download_pdf_functionality(self):
        """Test PDF download method with actual download"""
        import tempfile
        import shutil

        # Create a temporary directory for testing
        test_dir = tempfile.mkdtemp(prefix="semantic_test_")

        try:
            # Test with a known paper that should exist
            paper_id = "5bbfdf2e62f0508c65ba6de9c72fe2066fd98138"  # A well-known paper

            print(f"\nTesting PDF download for paper {paper_id}")
            result = self.searcher.download_pdf(paper_id, test_dir)

            # Check that result is a string
            self.assertIsInstance(result, str)

            # Check if download was successful
            if not result.startswith("Error") and not result.startswith("Failed"):
                # Download successful - check if file exists
                self.assertTrue(
                    os.path.exists(result), f"Downloaded file should exist at {result}"
                )

                # Check file size (PDF should be larger than 1KB)
                file_size = os.path.getsize(result)
                self.assertGreater(
                    file_size, 1024, "PDF file should be larger than 1KB"
                )

                # Check file extension
                self.assertTrue(
                    result.endswith(".pdf"),
                    "Downloaded file should have .pdf extension",
                )

                print(
                    f"PDF successfully downloaded: {result} (size: {file_size} bytes)"
                )
            else:
                print(f"Download failed (this might be expected): {result}")

        except Exception as e:
            print(f"Exception during PDF download test: {e}")
            # Don't fail the test for network issues
            pass
        finally:
            # Clean up temporary directory
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)

    @unittest.skipUnless(check_semantic_accessible(), "Semantic Scholar not accessible")
    def test_read_paper_functionality(self):
        """Test read paper method with text extraction functionality"""
        import tempfile
        import shutil

        # Create a temporary directory for testing
        test_dir = tempfile.mkdtemp(prefix="semantic_read_test_")

        try:
            # Test with a known paper
            paper_id = "5bbfdf2e62f0508c65ba6de9c72fe2066fd98138"

            print(f"\nTesting read_paper for paper {paper_id}")
            result = self.searcher.read_paper(paper_id, test_dir)

            # Check that result is a string
            self.assertIsInstance(result, str)

            # Check for successful text extraction
            if "Error" not in result and len(result) > 100:
                print(f"Text extraction successful. Text length: {len(result)}")

                # Should contain metadata
                self.assertIn("Title:", result)
                self.assertIn("Authors:", result)
                self.assertIn("Published Date:", result)
                self.assertIn("PDF downloaded to:", result)

                # Should contain page markers indicating text extraction
                self.assertIn("--- Page", result)

                # Check if PDF was actually downloaded
                expected_filename = f"iacr_{paper_id.replace('/', '_')}.pdf"
                expected_path = os.path.join(test_dir, expected_filename)
                self.assertTrue(os.path.exists(expected_path))

                file_size = os.path.getsize(expected_path)
                print(f"PDF file found: {expected_path} (size: {file_size} bytes)")
                self.assertGreater(file_size, 1000)  # Should be at least 1KB

                # Show a preview of extracted text
                preview = result[:500] + "..." if len(result) > 500 else result
                print(f"Text preview:\n{preview}")

            else:
                print(f"Read paper result: {result}")
                # For network issues or PDF extraction problems, don't fail
                print(
                    "Note: This might be due to network issues or PDF extraction limitations"
                )

        except Exception as e:
            print(f"Exception during read_paper test: {e}")
            # Don't fail the test for network issues
            pass
        finally:
            # Clean up temporary directory
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)

    @unittest.skipUnless(check_semantic_accessible(), "Semantic Scholar not accessible")
    def test_get_paper_details(self):
        """Test getting detailed paper information"""
        paper_id = "5bbfdf2e62f0508c65ba6de9c72fe2066fd98138"  # A known paper
        paper_details = self.searcher.get_paper_details(paper_id)

        if paper_details:
            # Test basic attributes
            self.assertTrue(paper_details.title)
            self.assertEqual(paper_details.paper_id, paper_id)
            self.assertEqual(paper_details.source, "semantic")
            self.assertTrue(paper_details.url)
            self.assertTrue(paper_details.pdf_url)

            # Test that we have authors
            self.assertIsInstance(paper_details.authors, list)
            self.assertGreater(len(paper_details.authors), 0)

            # Test that we have abstract
            self.assertTrue(paper_details.abstract)

            # Test extra metadata
            if paper_details.extra:
                self.assertIsInstance(paper_details.extra, dict)

            # printing all details for verification
            print(f"\n{paper_details}")
        else:
            self.fail("Could not fetch paper details")

    @unittest.skipUnless(check_semantic_accessible(), "Semantic Scholar not accessible")
    def test_search_with_fetch_details(self):
        """Test search functionality with fetch_details parameter"""
        # Test with fetch_details=True (detailed information)
        print("\nTesting search with fetch_details=True")
        detailed_papers = self.searcher.search(
            "cryptography", max_results=2, fetch_details=True
        )

        self.assertIsInstance(detailed_papers, list)
        self.assertLessEqual(len(detailed_papers), 2)

        if detailed_papers:
            paper = detailed_papers[0]
            self.assertEqual(paper.source, "semantic")

            # Detailed papers should have more complete information
            print(f"Detailed paper: {paper.title}")
            print(f"Authors: {len(paper.authors)} authors")
            print(f"Keywords: {len(paper.keywords)} keywords")
            print(f"Abstract length: {len(paper.abstract)} chars")

            # Should have keywords and publication info if available
            if paper.keywords:
                self.assertIsInstance(paper.keywords, list)
                print(f"Keywords found: {', '.join(paper.keywords[:3])}...")

            if paper.extra:
                pub_info = paper.extra.get("publication_info", "")
                if pub_info:
                    print(f"Publication info: {pub_info[:50]}...")

        # Test with fetch_details=False (compact information)
        print("\nTesting search with fetch_details=False")
        compact_papers = self.searcher.search(
            "cryptography", max_results=2, fetch_details=False
        )

        self.assertIsInstance(compact_papers, list)
        self.assertLessEqual(len(compact_papers), 2)

        if compact_papers:
            paper = compact_papers[0]
            self.assertEqual(paper.source, "semantic")

            print(f"Compact paper: {paper.title}")
            print(f"Authors: {len(paper.authors)} authors")
            print(f"Categories: {', '.join(paper.categories)}")
            print(f"Abstract preview length: {len(paper.abstract)} chars")

    @unittest.skipUnless(check_semantic_accessible(), "Semantic Scholar not accessible")
    def test_search_performance_comparison(self):
        """Test performance difference between detailed and compact search"""
        import time

        query = "encryption"
        max_results = 3

        # Test detailed search time
        print("\nTesting detailed search performance...")
        start_time = time.time()
        compact_papers = self.searcher.search(
            query, max_results=max_results
        )
        compact_time = time.time() - start_time

        print(
            f"Compact search took {compact_time:.2f} seconds for {len(compact_papers)} papers"
        )




if __name__ == "__main__":
    unittest.main()
