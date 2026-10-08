import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from paper_search_mcp.academic_platforms.europepmc import EuropePMCSearcher
from paper_search_mcp.academic_platforms.pmc import PMCSearcher


def _response(content=b"", status_code=200, content_type="application/pdf"):
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.content = content
    response.headers = {"Content-Type": content_type}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error")
    else:
        response.raise_for_status.return_value = None
    return response


def _details(pmcid="", pdf_url=""):
    details: dict = {"title": "A paper", "pmcid": pmcid}
    if pdf_url:
        details["fullTextUrlList"] = {"fullTextUrl": [{"documentStyle": "pdf", "url": pdf_url}]}
    return details


class TestEuropePMCFullText(unittest.TestCase):
    def setUp(self):
        self.searcher = EuropePMCSearcher()
        self.save_path = tempfile.mkdtemp()

    def test_download_pdf_takes_the_pmc_copy_first(self):
        self.searcher.session.get = Mock()
        with (
            patch.object(self.searcher, "_get_paper_details", return_value=_details("PMC7095418", "https://x.org/a.pdf")),
            patch.object(PMCSearcher, "download_pdf", return_value="/downloads/PMC7095418.pdf") as download,
        ):
            path = self.searcher.download_pdf("PMID:32015507", self.save_path)

        self.assertEqual(path, "/downloads/PMC7095418.pdf")
        download.assert_called_once_with("PMC7095418", self.save_path)
        self.searcher.session.get.assert_not_called()

    def test_download_pdf_falls_back_to_the_listed_pdf(self):
        self.searcher.session.get = Mock(return_value=_response(b"%PDF-1.7 publisher copy"))
        with (
            patch.object(self.searcher, "_get_paper_details", return_value=_details("PMC1", "https://x.org/a.pdf")),
            patch.object(PMCSearcher, "download_pdf", side_effect=Exception("no open access PDF")),
        ):
            path = self.searcher.download_pdf("PMC1", self.save_path)

        self.assertEqual(Path(path).read_bytes(), b"%PDF-1.7 publisher copy")
        self.assertEqual(self.searcher.session.get.call_args.args[0], "https://x.org/a.pdf")

    def test_no_pmc_copy_and_no_listed_pdf_never_asks_the_pmc_article_page(self):
        self.searcher.session.get = Mock()
        with (
            patch.object(self.searcher, "_get_paper_details", return_value=_details("PMC2")),
            patch.object(PMCSearcher, "download_pdf", side_effect=Exception("no open access PDF")),
        ):
            with self.assertRaises(Exception):
                self.searcher.download_pdf("PMC2", self.save_path)

        self.searcher.session.get.assert_not_called()

    def test_read_paper_returns_the_pmc_text(self):
        with (
            patch.object(self.searcher, "_get_paper_details", return_value=_details("PMC3")),
            patch.object(PMCSearcher, "open_access_text", return_value="the whole article") as text,
            patch.object(self.searcher, "download_pdf") as download,
        ):
            result = self.searcher.read_paper("PMC3", self.save_path)

        self.assertEqual(result, "the whole article")
        text.assert_called_once_with("PMC3")
        download.assert_not_called()

    def test_read_paper_falls_back_to_the_pdf_without_pmc_text(self):
        page = Mock()
        page.extract_text.return_value = "extracted " * 20
        pdf = Path(self.save_path) / "a.pdf"
        pdf.write_bytes(b"%PDF-1.7")
        with (
            patch.object(self.searcher, "_get_paper_details", return_value=_details("PMC4")),
            patch.object(PMCSearcher, "open_access_text", return_value=None),
            patch.object(self.searcher, "download_pdf", return_value=str(pdf)),
            patch("paper_search_mcp.academic_platforms.europepmc.PdfReader", return_value=Mock(pages=[page])),
        ):
            result = self.searcher.read_paper("PMC4", self.save_path)

        self.assertIn("extracted", result)


if __name__ == "__main__":
    unittest.main()
