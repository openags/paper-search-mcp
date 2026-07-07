import unittest
import asyncio
import os
import tempfile
from unittest.mock import patch, AsyncMock, MagicMock

from paper_search_mcp import server
from paper_search_mcp.server import (
    _title_similarity,
    _pdf_matches_expected,
    _download_from_url,
    _try_repository_fallback,
)


class TestDownloadWithFallback(unittest.TestCase):
    def test_repository_fallback_before_scihub(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=("/tmp/repo.pdf", ""))), \
             patch("paper_search_mcp.server.SciHubFetcher.download_pdf", side_effect=AssertionError("Sci-Hub should not be called")):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=True,
                )
            )
            self.assertEqual(result, "/tmp/repo.pdf")

    def test_unpaywall_fallback_after_repositories(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(None, "repo failed"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value="https://example.org/oa.pdf"), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/unpaywall.pdf")):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=True,
                )
            )
            self.assertEqual(result, "/tmp/unpaywall.pdf")

    def test_no_scihub_returns_oa_chain_error(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(None, "repo failed"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=False,
                )
            )
            self.assertIn("OA fallback chain", result)

    def test_download_with_fallback_propagates_title_to_repository_fallback(self):
        """Regression: download_with_fallback must forward the title to
        _try_repository_fallback so the latter can apply title-match filtering."""
        captured = {}

        async def fake_repo_fallback(doi, title, save_path, expected_title=""):
            captured["expected_title"] = expected_title
            captured["title"] = title
            return None, "intentional fail"

        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=fake_repo_fallback), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None):
            asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="Myodural bridge and chronic headache",
                    use_scihub=False,
                )
            )
        self.assertEqual(captured.get("expected_title"), "Myodural bridge and chronic headache")
        self.assertEqual(captured.get("title"), "Myodural bridge and chronic headache")


class TestRepositoryFallbackNumericPaperId(unittest.TestCase):
    """Regression test for issue #57: _try_repository_fallback crashed when a
    repository connector returned a Paper whose paper_id was a non-string
    (int) value, because the code called .strip() on it directly."""

    def test_numeric_paper_id_does_not_crash(self):
        class FakePaper:
            pdf_url = "https://example.org/oa.pdf"
            paper_id = 12345  # int, not str — caused 'int' object has no attribute 'strip'
            title = "some title that matches the expected title"

        fake_searcher = type(
            "S", (), {"search": staticmethod(lambda q, max_results=3: [FakePaper()])}
        )

        # Patch one of the repository searchers to return our FakePaper.
        with patch.object(server, "openaire_searcher", fake_searcher), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/ok.pdf")):
            result, err = asyncio.run(
                server._try_repository_fallback(
                    doi="10.1000/test",
                    title="some title that matches the expected title",
                    save_path="/tmp",
                    expected_title="some title that matches the expected title",
                )
            )
            self.assertEqual(result, "/tmp/ok.pdf")
            self.assertEqual(err, "")


class TestRepositoryFallbackTitleMatching(unittest.TestCase):
    """Fix 3: when a repository returns a candidate paper whose title is too
    dissimilar from the expected one, we must skip it instead of blindly
    downloading — this is the cause of the 'phantom PDF' bug where a search
    for 'myodural bridge' returned an unrelated chemistry paper's PDF."""

    def _make_fake_paper(self, title, pdf_url="https://example.org/oa.pdf"):
        class FakePaper:
            def __init__(self, t, u):
                self.title = t
                self.pdf_url = u
                self.paper_id = "fake-id"
        return FakePaper(title, pdf_url)

    def test_dissimilar_title_is_skipped(self):
        """A fallback paper titled 'Solar cells chemistry' must NOT be accepted
        when we asked for 'Myodural bridge and chronic headache'."""
        fake_searcher = type(
            "S", (),
            {"search": staticmethod(lambda q, max_results=3: [
                self._make_fake_paper("Solar cells chemistry and lead-free perovskites")
            ])},
        )

        download_calls = []

        async def fake_download(pdf_url, save_path, filename_hint, expected_title="", expected_doi=""):
            download_calls.append((pdf_url, expected_title))
            return "/tmp/should-not-happen.pdf"

        with patch.object(server, "openaire_searcher", fake_searcher), \
             patch("paper_search_mcp.server._download_from_url", new=fake_download):
            result, err = asyncio.run(
                server._try_repository_fallback(
                    doi="10.1000/test",
                    title="Myodural bridge and chronic headache",
                    save_path="/tmp",
                    expected_title="Myodural bridge and chronic headache",
                )
            )
        self.assertIsNone(result, "dissimilar title must be skipped, not downloaded")
        self.assertEqual(download_calls, [], "_download_from_url must not be called for dissimilar title")

    def test_similar_title_is_downloaded(self):
        """A fallback paper with a close title (e.g. same paper, slightly
        different formatting) must still be downloaded."""
        fake_searcher = type(
            "S", (),
            {"search": staticmethod(lambda q, max_results=3: [
                self._make_fake_paper("The Myodural Bridge and Chronic Headache: An Experimental Study")
            ])},
        )

        with patch.object(server, "openaire_searcher", fake_searcher), \
             patch("paper_search_mcp.server._download_from_url",
                   new=AsyncMock(return_value="/tmp/ok.pdf")) as mock_dl:
            result, err = asyncio.run(
                server._try_repository_fallback(
                    doi="10.1000/test",
                    title="Myodural bridge and chronic headache",
                    save_path="/tmp",
                    expected_title="Myodural bridge and chronic headache",
                )
            )
        self.assertEqual(result, "/tmp/ok.pdf")
        mock_dl.assert_awaited_once()

    def test_no_expected_title_skips_filter(self):
        """Backward-compat: when expected_title is empty, no filtering is applied."""
        fake_searcher = type(
            "S", (),
            {"search": staticmethod(lambda q, max_results=3: [
                self._make_fake_paper("Anything at all")
            ])},
        )

        with patch.object(server, "openaire_searcher", fake_searcher), \
             patch("paper_search_mcp.server._download_from_url",
                   new=AsyncMock(return_value="/tmp/ok.pdf")):
            result, err = asyncio.run(
                server._try_repository_fallback(
                    doi="10.1000/test",
                    title="some query",
                    save_path="/tmp",
                    expected_title="",
                )
            )
        self.assertEqual(result, "/tmp/ok.pdf")


class TestTitleSimilarity(unittest.TestCase):
    def test_identical_titles_score_one(self):
        self.assertAlmostEqual(_title_similarity("Myodural bridge", "Myodural bridge"), 1.0)

    def test_case_insensitive(self):
        self.assertAlmostEqual(
            _title_similarity("Myodural Bridge", "myodural bridge"), 1.0
        )

    def test_whitespace_normalized(self):
        self.assertAlmostEqual(
            _title_similarity("Myodural  bridge", "Myodural bridge"), 1.0
        )

    def test_clear_mismatch_scores_low(self):
        sim = _title_similarity(
            "Myodural bridge and chronic headache",
            "Solar cells and lead-free perovskite chemistry",
        )
        self.assertLess(sim, 0.6, "topically unrelated titles must score < 0.6")

    def test_close_variant_scores_high(self):
        sim = _title_similarity(
            "Myodural bridge and chronic headache",
            "The Myodural Bridge and Chronic Headache: An Experimental Study",
        )
        self.assertGreaterEqual(sim, 0.6, "close title variants must score >= 0.6")

    def test_empty_strings(self):
        self.assertEqual(_title_similarity("", "anything"), 0.0)
        self.assertEqual(_title_similarity("anything", ""), 0.0)
        self.assertEqual(_title_similarity("", ""), 0.0)


class TestPdfMatchesExpected(unittest.TestCase):
    """Fix 1: verify a downloaded PDF's content matches the expected paper title
    before returning it from a fallback chain.

    Uses real PDFs from the downloads/ directory as fixtures when available
    (these reproduce the actual phantom-PDF bug observed in production:
    a search for a myodural-bridge paper returned a chemistry paper PDF).
    """

    DOWNLOADS_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "downloads",
    )
    # Real PDFs from a prior search session. If absent, content-level tests
    # fall back to a synthesized minimal PDF via pypdf, or skip if neither is
    # possible.
    REAL_REVIEW_PDF = os.path.join(DOWNLOADS_DIR, "europepmc_PMID_42078436.pdf")  # myodural review
    REAL_WRONG_PDF = os.path.join(DOWNLOADS_DIR, "europepmc_PMID_39227656.pdf")  # chemistry (phantom)
    EXPECTED_REVIEW_TITLE = "The myodural bridge complex a comprehensive review"
    EXPECTED_HEADACHE_TITLE = "Evidence for chronic headaches induced by pathological changes of myodural bridge complex"

    def _write_synthetic_pdf(self, text_content: str) -> str:
        """Synthesize a minimal one-page PDF whose extracted text contains
        the given string. Used when real-fixture PDFs aren't available."""
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest("reportlab not installed and no real PDF fixtures available")

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        c = canvas.Canvas(path)
        for i, line in enumerate(text_content.split("\n")[:30]):
            c.drawString(80, 750 - i * 12, line)
        c.save()
        return path

    def test_matching_real_pdf_accepted(self):
        """Sanity: the real review PDF must match its own expected title."""
        if not os.path.exists(self.REAL_REVIEW_PDF):
            self.skipTest(f"fixture {self.REAL_REVIEW_PDF} not present")
        self.assertTrue(_pdf_matches_expected(self.REAL_REVIEW_PDF, self.EXPECTED_REVIEW_TITLE))

    def test_mismatched_real_pdf_rejected(self):
        """Regression for the bug observed in this session: a PDF about
        solar cell chemistry (PMID 39227656) was returned when the expected
        paper was about myodural bridge and headache (PMID 39227656). The
        content check must reject this mismatch."""
        if not os.path.exists(self.REAL_WRONG_PDF):
            self.skipTest(f"fixture {self.REAL_WRONG_PDF} not present")
        self.assertFalse(
            _pdf_matches_expected(self.REAL_WRONG_PDF, self.EXPECTED_HEADACHE_TITLE),
            "chemistry PDF must NOT match expected myodural-bridge title"
        )

    def test_empty_expected_title_accepts(self):
        # When no title claim is made, accept anything
        if os.path.exists(self.REAL_REVIEW_PDF):
            self.assertTrue(_pdf_matches_expected(self.REAL_REVIEW_PDF, ""))
        else:
            pdf = self._write_synthetic_pdf("anything at all")
            try:
                self.assertTrue(_pdf_matches_expected(pdf, ""))
            finally:
                os.remove(pdf)

    def test_doi_in_text_accepts(self):
        """When the expected DOI appears verbatim in the PDF text, accept
        regardless of title overlap — strong signal that this is the right PDF.

        Uses a mock PdfReader so we can control extracted text precisely,
        without depending on whether a real fixture PDF embeds its DOI
        in a pypdf-extractable form (many do not in the first 3 pages)."""
        from unittest.mock import patch as _patch, MagicMock as _MagicMock

        fake_page = _MagicMock()
        fake_page.extract_text.return_value = (
            "Journal of Something\n"
            "Some title here\n"
            "DOI: 10.1038/s41598-024-55069-7\n"
            "more content"
        )
        fake_reader = _MagicMock()
        fake_reader.pages = [fake_page]

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            with _patch("pypdf.PdfReader", return_value=fake_reader):
                self.assertTrue(
                    _pdf_matches_expected(
                        path,
                        "Completely Different Title That Should Not Match",
                        expected_doi="10.1038/s41598-024-55069-7",
                    ),
                    "DOI match in PDF text must accept regardless of title overlap"
                )
        finally:
            os.remove(path)

    def test_unreadable_pdf_accepted(self):
        """If pypdf can't read the file, don't penalize — could be a legit scanned PDF."""
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"not a real pdf")
        os.close(fd)
        try:
            self.assertTrue(_pdf_matches_expected(path, "any title"))
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
