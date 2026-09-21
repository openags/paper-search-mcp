import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from paper_search_mcp import server


class _FakeAsyncClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url):
        return self.response


def _empty_searcher():
    return SimpleNamespace(search=lambda query, max_results=3: [])


class TestDownloadWithFallback(unittest.TestCase):
    def test_scihub_is_disabled_by_default(self):
        with (
            patch.object(
                server.arxiv_searcher,
                "download_pdf",
                side_effect=Exception("primary failed"),
            ),
            patch(
                "paper_search_mcp.server._try_repository_fallback",
                new=AsyncMock(return_value=(None, "repo failed")),
            ),
            patch.object(
                server.unpaywall_resolver,
                "resolve_best_pdf_url",
                return_value=None,
            ),
            patch(
                "paper_search_mcp.server.SciHubFetcher.download_pdf",
                side_effect=AssertionError("Sci-Hub should not be called"),
            ),
        ):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                )
            )

        self.assertIn("OA fallback chain", result)

    def test_repository_fallback_before_scihub(self):
        with (
            patch.object(
                server.arxiv_searcher,
                "download_pdf",
                side_effect=Exception("primary failed"),
            ),
            patch(
                "paper_search_mcp.server._try_repository_fallback",
                new=AsyncMock(return_value=("/tmp/repo.pdf", "")),
            ),
            patch(
                "paper_search_mcp.server.SciHubFetcher.download_pdf",
                side_effect=AssertionError("Sci-Hub should not be called"),
            ),
        ):
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
        with (
            patch.object(
                server.arxiv_searcher,
                "download_pdf",
                side_effect=Exception("primary failed"),
            ),
            patch(
                "paper_search_mcp.server._try_repository_fallback",
                new=AsyncMock(return_value=(None, "repo failed")),
            ),
            patch.object(
                server.unpaywall_resolver,
                "resolve_best_pdf_url",
                return_value="https://example.org/oa.pdf",
            ),
            patch(
                "paper_search_mcp.server._download_from_url",
                new=AsyncMock(return_value="/tmp/unpaywall.pdf"),
            ) as download,
        ):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test title",
                    use_scihub=True,
                )
            )

        self.assertEqual(result, "/tmp/unpaywall.pdf")
        self.assertEqual(download.await_args.kwargs["expected_title"], "test title")
        self.assertEqual(download.await_args.kwargs["expected_doi"], "10.1000/test")

    def test_no_scihub_returns_oa_chain_error(self):
        with (
            patch.object(
                server.arxiv_searcher,
                "download_pdf",
                side_effect=Exception("primary failed"),
            ),
            patch(
                "paper_search_mcp.server._try_repository_fallback",
                new=AsyncMock(return_value=(None, "repo failed")),
            ),
            patch.object(
                server.unpaywall_resolver,
                "resolve_best_pdf_url",
                return_value=None,
            ),
        ):
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

    def test_title_is_propagated_to_repository_validation(self):
        captured = {}

        async def fake_repository(doi, title, save_path, expected_title=None):
            captured.update(title=title, expected_title=expected_title)
            return None, "intentional failure"

        with (
            patch.object(
                server.arxiv_searcher,
                "download_pdf",
                side_effect=Exception("primary failed"),
            ),
            patch(
                "paper_search_mcp.server._try_repository_fallback",
                new=fake_repository,
            ),
            patch.object(
                server.unpaywall_resolver,
                "resolve_best_pdf_url",
                return_value=None,
            ),
        ):
            asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="Myodural bridge and chronic headache",
                )
            )

        self.assertEqual(captured["title"], "Myodural bridge and chronic headache")
        self.assertEqual(
            captured["expected_title"],
            "Myodural bridge and chronic headache",
        )

    def test_scihub_fallback_rejects_and_removes_mismatched_pdf(self):
        with tempfile.TemporaryDirectory() as save_path:
            downloaded_path = os.path.join(save_path, "wrong.pdf")
            with open(downloaded_path, "wb") as downloaded:
                downloaded.write(b"%PDF-1.7\nwrong paper")

            with (
                patch.object(
                    server.arxiv_searcher,
                    "download_pdf",
                    side_effect=Exception("primary failed"),
                ),
                patch(
                    "paper_search_mcp.server._try_repository_fallback",
                    new=AsyncMock(return_value=(None, "repo failed")),
                ),
                patch.object(
                    server.unpaywall_resolver,
                    "resolve_best_pdf_url",
                    return_value=None,
                ),
                patch(
                    "paper_search_mcp.server.SciHubFetcher.download_pdf",
                    return_value=downloaded_path,
                ),
                patch.object(
                    server,
                    "_pdf_matches_expected",
                    return_value=False,
                ) as verifier,
            ):
                result = asyncio.run(
                    server.download_with_fallback(
                        source="arxiv",
                        paper_id="1234.5678",
                        doi="10.1000/test",
                        title="Expected paper title",
                        save_path=save_path,
                        use_scihub=True,
                    )
                )

            self.assertIn("did not match", result)
            self.assertFalse(os.path.exists(downloaded_path))
            verifier.assert_called_once_with(
                downloaded_path,
                "Expected paper title",
                "10.1000/test",
            )

    def test_scihub_fallback_returns_verified_pdf(self):
        with tempfile.TemporaryDirectory() as save_path:
            downloaded_path = os.path.join(save_path, "right.pdf")
            with open(downloaded_path, "wb") as downloaded:
                downloaded.write(b"%PDF-1.7\nright paper")

            with (
                patch.object(
                    server.arxiv_searcher,
                    "download_pdf",
                    side_effect=Exception("primary failed"),
                ),
                patch(
                    "paper_search_mcp.server._try_repository_fallback",
                    new=AsyncMock(return_value=(None, "repo failed")),
                ),
                patch.object(
                    server.unpaywall_resolver,
                    "resolve_best_pdf_url",
                    return_value=None,
                ),
                patch(
                    "paper_search_mcp.server.SciHubFetcher.download_pdf",
                    return_value=downloaded_path,
                ),
                patch.object(
                    server,
                    "_pdf_matches_expected",
                    return_value=True,
                ),
            ):
                result = asyncio.run(
                    server.download_with_fallback(
                        source="arxiv",
                        paper_id="1234.5678",
                        doi="10.1000/test",
                        title="Expected paper title",
                        save_path=save_path,
                        use_scihub=True,
                    )
                )

            self.assertEqual(result, downloaded_path)
            self.assertTrue(os.path.exists(downloaded_path))


class TestRepositoryFallback(unittest.TestCase):
    def _run_with_searcher(self, searcher, *, title, expected_title=None):
        with (
            patch.object(server, "openaire_searcher", searcher),
            patch.object(server, "core_searcher", _empty_searcher()),
            patch.object(server, "europepmc_searcher", _empty_searcher()),
            patch.object(server, "pmc_searcher", _empty_searcher()),
            patch.object(
                server,
                "_download_from_url",
                new=AsyncMock(return_value="/tmp/ok.pdf"),
            ) as download,
        ):
            result = asyncio.run(
                server._try_repository_fallback(
                    doi="10.1000/test",
                    title=title,
                    save_path="/tmp",
                    expected_title=expected_title,
                )
            )
        return result, download

    def test_numeric_paper_id_does_not_crash(self):
        paper = SimpleNamespace(
            pdf_url="https://example.org/oa.pdf",
            paper_id=12345,
            title="The requested title",
            doi="10.1000/test",
        )
        searcher = SimpleNamespace(search=lambda query, max_results=3: [paper])

        (result, error), download = self._run_with_searcher(
            searcher,
            title="The requested title",
        )

        self.assertEqual(result, "/tmp/ok.pdf")
        self.assertEqual(error, "")
        self.assertIn("12345", download.await_args.args[2])

    def test_dissimilar_title_is_skipped_without_network_fallthrough(self):
        paper = SimpleNamespace(
            pdf_url="https://example.org/wrong.pdf",
            paper_id="wrong",
            title="Solar cells and lead-free perovskite chemistry",
            doi="10.9999/wrong",
        )
        searcher = SimpleNamespace(search=lambda query, max_results=3: [paper])

        (result, error), download = self._run_with_searcher(
            searcher,
            title="Myodural bridge and chronic headache",
        )

        self.assertIsNone(result)
        self.assertIn("did not match", error)
        download.assert_not_awaited()

    def test_similar_title_is_downloaded(self):
        paper = SimpleNamespace(
            pdf_url="https://example.org/right.pdf",
            paper_id="right",
            title="The Myodural Bridge and Chronic Headache: Experimental Study",
            doi="",
        )
        searcher = SimpleNamespace(search=lambda query, max_results=3: [paper])

        (result, error), download = self._run_with_searcher(
            searcher,
            title="Myodural bridge and chronic headache",
        )

        self.assertEqual(result, "/tmp/ok.pdf")
        self.assertEqual(error, "")
        download.assert_awaited_once()

    def test_exact_candidate_doi_bypasses_title_prefilter(self):
        paper = SimpleNamespace(
            pdf_url="https://example.org/right.pdf",
            paper_id="right",
            title="Publisher supplied abbreviated title",
            doi="https://doi.org/10.1000/TEST",
        )
        searcher = SimpleNamespace(search=lambda query, max_results=3: [paper])

        (result, error), download = self._run_with_searcher(
            searcher,
            title="A much longer requested title with different wording",
        )

        self.assertEqual(result, "/tmp/ok.pdf")
        self.assertEqual(error, "")
        download.assert_awaited_once()

    def test_duplicate_pdf_url_is_attempted_once(self):
        paper = SimpleNamespace(
            pdf_url="https://example.org/repeated.pdf",
            paper_id="same",
            title="Matching requested paper title",
            doi="10.1000/test",
        )
        searcher = SimpleNamespace(search=lambda query, max_results=3: [paper])

        with (
            patch.object(server, "openaire_searcher", searcher),
            patch.object(server, "core_searcher", _empty_searcher()),
            patch.object(server, "europepmc_searcher", _empty_searcher()),
            patch.object(server, "pmc_searcher", _empty_searcher()),
            patch.object(
                server,
                "_download_from_url",
                new=AsyncMock(return_value=None),
            ) as download,
        ):
            result, error = asyncio.run(
                server._try_repository_fallback(
                    doi="10.1000/test",
                    title="Matching requested paper title",
                    save_path="/tmp",
                )
            )

        self.assertIsNone(result)
        self.assertIn("did not match", error)
        download.assert_awaited_once()


class TestTitleSimilarity(unittest.TestCase):
    def test_identical_titles_score_one(self):
        self.assertEqual(
            server._title_similarity("Myodural bridge", "Myodural bridge"),
            1.0,
        )

    def test_word_order_and_case_do_not_matter(self):
        self.assertEqual(
            server._title_similarity("Bridge Myodural", "myodural bridge"),
            1.0,
        )

    def test_subtitle_variant_scores_high(self):
        score = server._title_similarity(
            "Myodural bridge and chronic headache",
            "The Myodural Bridge and Chronic Headache: An Experimental Study",
        )
        self.assertGreaterEqual(score, 0.6)

    def test_unrelated_titles_score_low(self):
        score = server._title_similarity(
            "Myodural bridge and chronic headache",
            "Solar cells and lead-free perovskite chemistry",
        )
        self.assertLess(score, 0.6)

    def test_empty_title_scores_zero(self):
        self.assertEqual(server._title_similarity("", "anything"), 0.0)


class TestPdfMatchesExpected(unittest.TestCase):
    @staticmethod
    def _reader_with_text(text):
        page = MagicMock()
        page.extract_text.return_value = text
        reader = MagicMock()
        reader.pages = [page]
        return reader

    def test_matching_title_is_accepted(self):
        reader = self._reader_with_text(
            "Evidence for chronic headaches induced by pathological changes "
            "of myodural bridge complex"
        )
        with patch("pypdf.PdfReader", return_value=reader):
            matched = server._pdf_matches_expected(
                b"%PDF-1.7",
                "Evidence for chronic headaches induced by pathological changes "
                "of myodural bridge complex",
            )
        self.assertTrue(matched)

    def test_unrelated_text_is_rejected(self):
        reader = self._reader_with_text(
            "Single crystal solar cells and lead-free perovskite chemistry"
        )
        with patch("pypdf.PdfReader", return_value=reader):
            matched = server._pdf_matches_expected(
                b"%PDF-1.7",
                "Evidence for chronic headaches induced by pathological changes "
                "of myodural bridge complex",
            )
        self.assertFalse(matched)

    def test_doi_match_accepts_line_wrapped_doi(self):
        reader = self._reader_with_text(
            "DOI: https://doi.org/10.1038/s41598-024-\n55069-7"
        )
        with patch("pypdf.PdfReader", return_value=reader):
            matched = server._pdf_matches_expected(
                b"%PDF-1.7",
                "Completely different title",
                "https://doi.org/10.1038/s41598-024-55069-7",
            )
        self.assertTrue(matched)

    def test_doi_only_identity_is_checked(self):
        reader = self._reader_with_text("doi: 10.1000/example")
        with patch("pypdf.PdfReader", return_value=reader):
            matched = server._pdf_matches_expected(
                b"%PDF-1.7",
                "",
                "10.1000/example",
            )
        self.assertTrue(matched)

    def test_unreadable_pdf_is_rejected_when_identity_is_claimed(self):
        with patch("pypdf.PdfReader", side_effect=ValueError("broken PDF")):
            self.assertFalse(
                server._pdf_matches_expected(b"broken", "Expected paper title")
            )

    def test_image_only_pdf_is_rejected_when_identity_is_claimed(self):
        reader = self._reader_with_text("")
        with patch("pypdf.PdfReader", return_value=reader):
            self.assertFalse(
                server._pdf_matches_expected(b"%PDF-1.7", "Expected paper title")
            )

    def test_no_identity_hint_preserves_backward_compatibility(self):
        self.assertTrue(server._pdf_matches_expected(b"anything", "", ""))


class TestDownloadFromUrl(unittest.TestCase):
    @staticmethod
    def _response(content, content_type="application/pdf", status_code=200):
        return SimpleNamespace(
            content=content,
            headers={"content-type": content_type},
            status_code=status_code,
        )

    def _patch_client(self, response):
        return patch.object(
            server.httpx,
            "AsyncClient",
            return_value=_FakeAsyncClient(response),
        )

    def test_html_at_pdf_url_is_rejected(self):
        response = self._response(b"<html>login page</html>", "text/html")
        with tempfile.TemporaryDirectory() as parent:
            save_path = os.path.join(parent, "downloads")
            with self._patch_client(response):
                result = asyncio.run(
                    server._download_from_url(
                        "https://example.org/not-really.pdf",
                        save_path,
                    )
                )
            self.assertIsNone(result)
            self.assertFalse(os.path.exists(save_path))

    def test_identity_mismatch_is_not_written(self):
        response = self._response(b"%PDF-1.7\nplaceholder")
        with tempfile.TemporaryDirectory() as parent:
            save_path = os.path.join(parent, "downloads")
            with (
                self._patch_client(response),
                patch.object(server, "_pdf_matches_expected", return_value=False),
            ):
                result = asyncio.run(
                    server._download_from_url(
                        "https://example.org/paper.pdf",
                        save_path,
                        expected_title="Expected paper",
                    )
                )
            self.assertIsNone(result)
            self.assertFalse(os.path.exists(save_path))

    def test_verified_pdf_is_atomically_written(self):
        content = b"%PDF-1.7\nverified content"
        response = self._response(content)
        with tempfile.TemporaryDirectory() as save_path:
            with (
                self._patch_client(response),
                patch.object(server, "_pdf_matches_expected", return_value=True),
            ):
                result = asyncio.run(
                    server._download_from_url(
                        "https://example.org/paper.pdf",
                        save_path,
                        filename_hint="verified",
                        expected_title="Expected paper",
                    )
                )

            self.assertEqual(result, os.path.join(save_path, "verified.pdf"))
            with open(result, "rb") as downloaded:
                self.assertEqual(downloaded.read(), content)
            self.assertEqual(
                [name for name in os.listdir(save_path) if name.endswith(".part")],
                [],
            )

    def test_doi_only_triggers_content_validation(self):
        content = b"%PDF-1.7\nverified content"
        response = self._response(content)
        with tempfile.TemporaryDirectory() as save_path:
            verifier = MagicMock(return_value=True)
            with self._patch_client(response), patch.object(
                server,
                "_pdf_matches_expected",
                verifier,
            ):
                result = asyncio.run(
                    server._download_from_url(
                        "https://example.org/paper.pdf",
                        save_path,
                        expected_doi="10.1000/example",
                    )
                )

        self.assertIsNotNone(result)
        verifier.assert_called_once_with(content, "", "10.1000/example")


if __name__ == "__main__":
    unittest.main()
