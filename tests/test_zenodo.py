# tests/test_zenodo.py
import unittest
import os
import requests
import tempfile
import shutil
from datetime import datetime

from paper_search_mcp.academic_platforms.zenodo import ZenodoSearcher


def check_zenodo_accessible():
    """Check if Zenodo API is accessible."""
    try:
        r = requests.get("https://zenodo.org/api/records", params={"q": "*", "size": 1}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False


class TestZenodoSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zenodo_accessible = check_zenodo_accessible()
        if not cls.zenodo_accessible:
            print("\nWarning: Zenodo is not accessible, some tests will be skipped")
        cls.searcher = ZenodoSearcher()

        # Try to locate a generic sample publication for deeper tests
        cls.sample_record_id = None
        cls.sample_pdf_available = False
        if cls.zenodo_accessible:
            try:
                papers = cls.searcher.search(
                    query="machine learning",
                    max_results=3,
                    resource_type="publication",
                    sort="mostrecent",
                )
                if papers:
                    cls.sample_record_id = str(papers[0].paper_id)
                    cls.sample_pdf_available = bool(papers[0].pdf_url)
                    print(f"Sample Zenodo record selected for tests: {cls.sample_record_id}")
            except Exception as e:
                print(f"Could not prefetch sample record: {e}")

    def setUp(self):
        self.searcher = self.__class__.searcher

    @unittest.skipUnless(check_zenodo_accessible(), "Zenodo not accessible")
    def test_search_basic(self):
        papers = self.searcher.search("control systems", max_results=3)
        self.assertIsInstance(papers, list)
        self.assertLessEqual(len(papers), 3)
        if papers:
            p = papers[0]
            self.assertEqual(p.source, "zenodo")
            self.assertTrue(hasattr(p, "title"))
            self.assertTrue(p.url.startswith("http"))

    @unittest.skipUnless(check_zenodo_accessible(), "Zenodo not accessible")
    def test_search_with_date_filter(self):
        papers = self.searcher.search(
            query="metadata.publication_date:[2025-01-01 TO 2025-12-31]",
            max_results=5,
            resource_type="publication",
            sort="mostrecent",
        )
        self.assertIsInstance(papers, list)
        for p in papers:
            self.assertEqual(p.source, "zenodo")
            # published_date should parse ISO 8601 or YYYY-MM-DD
            if p.published_date:
                self.assertIsInstance(p.published_date, datetime)

    @unittest.skipUnless(check_zenodo_accessible(), "Zenodo not accessible")
    def test_search_communities(self):
        communities = self.searcher.search_communities(query="open", max_results=10, sort="bestmatch")
        self.assertIsInstance(communities, list)
        if communities:
            # each community dict should have slug/title/links
            c = communities[0]
            self.assertIn("slug", c)
            self.assertIn("links", c)

    @unittest.skipUnless(check_zenodo_accessible(), "Zenodo not accessible")
    def test_get_record_details_and_list_files(self):
        if not self.__class__.sample_record_id:
            self.skipTest("No sample record id available")
        details = self.searcher.get_record_details(self.__class__.sample_record_id)
        self.assertTrue(details)
        self.assertIn("id", details)
        files = self.searcher.list_files(self.__class__.sample_record_id)
        self.assertIsInstance(files, list)
        # Non-fatal if no files or PDF not present
        has_pdf = any((f.get("mimetype") == "application/pdf") or str(f.get("key", "")).lower().endswith(".pdf") for f in files)
        if not has_pdf:
            print("No PDF file listed for the sample record; proceeding without download test")

    @unittest.skipUnless(check_zenodo_accessible(), "Zenodo not accessible")
    def test_download_and_read_pdf_if_available(self):
        if not self.__class__.sample_record_id:
            self.skipTest("No sample record id available")
        # Check whether a PDF seems to be available
        files = self.searcher.list_files(self.__class__.sample_record_id)
        pdf_candidates = [f for f in files if (f.get("mimetype") == "application/pdf") or str(f.get("key", "")).lower().endswith(".pdf")]
        if not pdf_candidates:
            self.skipTest("Sample record has no PDF to download")

        temp_dir = tempfile.mkdtemp(prefix="zenodo_test_")
        try:
            pdf_path = self.searcher.download_pdf(self.__class__.sample_record_id, temp_dir)
            # download_pdf returns a file path on success, or an error string
            if isinstance(pdf_path, str) and os.path.isfile(pdf_path):
                self.assertTrue(pdf_path.endswith('.pdf'))
                self.assertGreater(os.path.getsize(pdf_path), 1024)

                # Try reading text (best-effort)
                result = self.searcher.read_paper(self.__class__.sample_record_id, temp_dir)
                self.assertIsInstance(result, str)
                # Do not strictly assert text length due to varied PDFs
            else:
                print(f"PDF download not successful: {pdf_path}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @unittest.skipUnless(check_zenodo_accessible(), "Zenodo not accessible")
    def test_search_by_creator(self):
        # Best-effort; ensure call succeeds and returns a list
        results = self.searcher.search_by_creator("Hinton", max_results=3)
        self.assertIsInstance(results, list)
        if results:
            self.assertEqual(results[0].source, "zenodo")


if __name__ == "__main__":
    unittest.main()
