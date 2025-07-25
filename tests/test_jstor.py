import unittest
import sys
import os
from playwright.sync_api import sync_playwright
from paper_search_mcp.academic_platforms.jstor import JstorSearcher


def check_jstor_accessible() -> bool:
    """Return True if JSTOR loads in a headless Playwright page (status <400)."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-web-security",
                ],
            )
            page = browser.new_page()
            resp = page.goto("https://www.jstor.org/", timeout=15_000)
            status = resp.status if resp else 0
            browser.close()
            return status and status < 400
    except Exception:
        return False


class TestJstorSearcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jstor_accessible = check_jstor_accessible()
        if not cls.jstor_accessible:
            print("\nWarning: JSTOR is not accessible in headless mode; tests skipped.")

    def setUp(self):
        self.searcher = JstorSearcher()

    def test_search_headless_mode(self):
        if not self.jstor_accessible:
            self.skipTest("JSTOR blocked (status is not 400)")

        papers = self.searcher.search("frantz fanon and feminism", max_results=5)
        self.assertIsInstance(papers, list)
        if papers:  # CAPTCHA may yield zero results
            self.assertTrue(papers[0].title)
            self.assertEqual(papers[0].source, "jstor")

    def test_download_pdf_not_supported(self):
        with self.assertRaises(NotImplementedError):
            self.searcher.download_pdf("dummy", "./downloads")

    def test_read_paper_not_supported(self):
        msg = self.searcher.read_paper("dummy")
        self.assertIn("JSTOR requires institutional access", msg)

    def test_playwright_compatibility(self):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                resp = page.goto("https://www.jstor.org/", timeout=10_000)
                self.assertIsNotNone(resp)
                browser.close()
        except Exception as e:
            self.fail(f"Playwright headless launch failed: {e}")

if __name__ == "__main__":
    os.environ["AUTOMATED_TESTING"] = "1"
    unittest.main(verbosity=2)
