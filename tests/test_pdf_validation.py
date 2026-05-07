import unittest

from paper_search_mcp.utils import is_pdf_content


class TestPdfValidation(unittest.TestCase):
    def test_empty_content_is_not_pdf(self):
        self.assertFalse(is_pdf_content(b"", content_type="application/pdf"))

    def test_pdf_magic_is_valid(self):
        self.assertTrue(is_pdf_content(b"%PDF-1.7\n...", content_type="application/octet-stream"))

    def test_pdf_content_type_is_valid(self):
        self.assertTrue(is_pdf_content(b"content", content_type="application/pdf"))


if __name__ == "__main__":
    unittest.main()
