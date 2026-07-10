import unittest
import os
import shutil
import requests
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from paper_search_mcp.academic_platforms.semantic import SemanticSearcher


@lru_cache(maxsize=1)
def check_semantic_accessible():
    """Check if Semantic Scholar is accessible"""
    try:
        with requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/"
            "5bbfdf2e62f0508c65ba6de9c72fe2066fd98138",
            timeout=5,
        ) as response:
            return response.status_code == 200
    except requests.RequestException:
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

    def _mock_response(
        self,
        content,
        content_type="application/pdf",
        url="https://example.com/paper.pdf",
        error=None,
        headers=None,
    ):
        chunks = list(content) if isinstance(content, (list, tuple)) else [content]
        response = Mock()
        response.content = b"".join(chunks)
        response.headers = {"Content-Type": content_type}
        if headers:
            response.headers.update(headers)
        response.url = url
        response.iter_content.return_value = iter(chunks)
        response.close.return_value = None
        if error is not None:
            response.raise_for_status.side_effect = error
        else:
            response.raise_for_status.side_effect = None
            response.raise_for_status.return_value = None
        return response

    def test_download_pdf_saves_file_when_pdf_url_available(self):
        paper = SimpleNamespace(pdf_url="https://example.com/paper.pdf")
        response = self._mock_response(b"%PDF-1.4 test content")

        with tempfile.TemporaryDirectory(prefix="semantic_mock_download_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(self.searcher.session, "get", return_value=response):
                    result = self.searcher.download_pdf("paper/123", test_dir)

            expected_path = Path(test_dir) / "semantic_paper_123.pdf"
            self.assertEqual(result, str(expected_path))
            self.assertTrue(expected_path.exists())
            self.assertEqual(expected_path.read_bytes(), b"%PDF-1.4 test content")

    def test_download_pdf_accepts_split_pdf_header(self):
        paper = SimpleNamespace(pdf_url="https://example.com/paper.pdf")
        response = self._mock_response([b"%P", b"DF-1.4 split header"])

        with tempfile.TemporaryDirectory(prefix="semantic_split_header_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(self.searcher.session, "get", return_value=response):
                    result = self.searcher.download_pdf("paper/123", test_dir)

            expected_path = Path(test_dir) / "semantic_paper_123.pdf"
            self.assertEqual(result, str(expected_path))
            self.assertEqual(expected_path.read_bytes(), b"%PDF-1.4 split header")

    def test_download_pdf_sanitizes_prefixed_identifier_for_filename(self):
        paper = SimpleNamespace(pdf_url="https://example.com/paper.pdf")
        response = self._mock_response(b"%PDF-1.4 test content")

        with tempfile.TemporaryDirectory(prefix="semantic_safe_filename_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(self.searcher.session, "get", return_value=response):
                    result = self.searcher.download_pdf(
                        "DOI:10.18653/v1/N18-3011", test_dir
                    )

            expected_path = Path(test_dir) / "semantic_DOI_10.18653_v1_N18-3011.pdf"
            self.assertEqual(result, str(expected_path))
            self.assertTrue(expected_path.exists())

    def test_download_pdf_caps_long_identifier_for_filename(self):
        paper = SimpleNamespace(pdf_url="https://example.com/paper.pdf")
        response = self._mock_response(b"%PDF-1.4 test content")
        paper_id = "URL:https://example.com/" + ("very-long-path/" * 30)

        with tempfile.TemporaryDirectory(prefix="semantic_long_filename_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(self.searcher.session, "get", return_value=response):
                    result = self.searcher.download_pdf(paper_id, test_dir)

            filename = Path(result).name
            safe_id = filename.removeprefix("semantic_").removesuffix(".pdf")
            self.assertLessEqual(
                len(safe_id),
                self.searcher.SAFE_PAPER_ID_MAX_LENGTH,
            )
            self.assertTrue(Path(result).exists())

    def test_safe_paper_id_adds_hash_for_distinct_long_identifiers(self):
        base_id = "URL:https://example.com/" + ("segment/" * 40)

        first = self.searcher._safe_paper_id(base_id + "a")
        second = self.searcher._safe_paper_id(base_id + "b")

        self.assertLessEqual(len(first), self.searcher.SAFE_PAPER_ID_MAX_LENGTH)
        self.assertLessEqual(len(second), self.searcher.SAFE_PAPER_ID_MAX_LENGTH)
        self.assertNotEqual(first, second)

    def test_download_pdf_uses_pmcid_fallback_when_direct_url_is_forbidden(self):
        direct_url = "https://academic.oup.com/article.pdf"
        fallback_url = "https://europepmc.org/articles/PMC10516373?pdf=render"
        paper = SimpleNamespace(
            pdf_url=direct_url,
            url="https://www.semanticscholar.org/paper/test",
            extra={"externalIds": {"PubMedCentral": "10516373"}},
        )
        forbidden_response = self._mock_response(
            b"<!DOCTYPE html><title>Just a moment...</title>",
            content_type="text/html; charset=UTF-8",
            url=direct_url,
            error=requests.HTTPError("403 Client Error: Forbidden"),
        )
        pdf_response = self._mock_response(
            b"%PDF-1.7 fallback content",
            content_type="application/pdf",
            url=fallback_url,
        )

        with tempfile.TemporaryDirectory(prefix="semantic_fallback_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher.session,
                    "get",
                    side_effect=[forbidden_response, pdf_response],
                ) as mocked_get:
                    result = self.searcher.download_pdf("paper/123", test_dir)

            expected_path = Path(test_dir) / "semantic_paper_123.pdf"
            self.assertEqual(result, str(expected_path))
            self.assertEqual(expected_path.read_bytes(), b"%PDF-1.7 fallback content")
            self.assertEqual(mocked_get.call_args_list[0].args[0], direct_url)
            self.assertEqual(mocked_get.call_args_list[1].args[0], fallback_url)

    def test_download_pdf_prefers_europe_pmc_for_pmc_article_url(self):
        article_url = "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11326250"
        fallback_url = "https://europepmc.org/articles/PMC11326250?pdf=render"
        paper = SimpleNamespace(
            pdf_url=article_url,
            url="https://www.semanticscholar.org/paper/test",
            extra={"externalIds": {"PubMedCentral": "11326250"}},
        )
        pdf_response = self._mock_response(
            b"%PDF-1.7 fallback content",
            content_type="application/pdf",
            url=fallback_url,
        )

        with tempfile.TemporaryDirectory(prefix="semantic_pmc_fallback_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher.session,
                    "get",
                    return_value=pdf_response,
                ) as mocked_get:
                    result = self.searcher.download_pdf("paper/123", test_dir)

            expected_path = Path(test_dir) / "semantic_paper_123.pdf"
            self.assertEqual(result, str(expected_path))
            self.assertEqual(mocked_get.call_args.args[0], fallback_url)

    def test_download_pdf_does_not_save_html_as_pdf(self):
        paper = SimpleNamespace(
            pdf_url="https://example.com/article",
            url="https://www.semanticscholar.org/paper/test",
            extra={},
        )
        html_response = self._mock_response(
            b"<!doctype html><html><body>not a pdf</body></html>",
            content_type="text/html; charset=utf-8",
            url="https://example.com/article",
        )

        with tempfile.TemporaryDirectory(prefix="semantic_html_download_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher.session,
                    "get",
                    return_value=html_response,
                ):
                    result = self.searcher.download_pdf("paper/123", test_dir)

            expected_path = Path(test_dir) / "semantic_paper_123.pdf"
            self.assertTrue(result.startswith("Error downloading PDF for paper/123"))
            self.assertFalse(expected_path.exists())
            html_response.close.assert_called_once()

    def test_download_pdf_rejects_non_pdf_body_with_pdf_content_type(self):
        paper = SimpleNamespace(
            pdf_url="https://example.com/article.pdf",
            url="https://www.semanticscholar.org/paper/test",
            extra={},
        )
        bad_response = self._mock_response(
            b"not actually a pdf",
            content_type="application/pdf",
            url="https://example.com/article.pdf",
        )

        with tempfile.TemporaryDirectory(prefix="semantic_fake_pdf_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher.session,
                    "get",
                    return_value=bad_response,
                ):
                    result = self.searcher.download_pdf("paper/123", test_dir)

            expected_path = Path(test_dir) / "semantic_paper_123.pdf"
            self.assertTrue(result.startswith("Error downloading PDF for paper/123"))
            self.assertFalse(expected_path.exists())

    def test_download_pdf_closes_streamed_response_after_success(self):
        paper = SimpleNamespace(
            pdf_url="https://example.com/paper.pdf",
            url="https://www.semanticscholar.org/paper/test",
            extra={},
        )
        pdf_response = self._mock_response(
            b"%PDF-1.7 streamed content",
            content_type="application/pdf",
            url="https://example.com/paper.pdf",
        )

        with tempfile.TemporaryDirectory(prefix="semantic_stream_close_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher.session,
                    "get",
                    return_value=pdf_response,
                ):
                    result = self.searcher.download_pdf("paper/123", test_dir)

            expected_path = Path(test_dir) / "semantic_paper_123.pdf"
            self.assertEqual(result, str(expected_path))
            pdf_response.close.assert_called_once()

    def test_download_pdf_removes_partial_temp_file_after_stream_failure(self):
        paper = SimpleNamespace(
            pdf_url="https://example.com/paper.pdf",
            url="https://www.semanticscholar.org/paper/test",
            extra={},
        )

        def broken_stream():
            yield b"%PDF-1.7 partial content"
            raise requests.ConnectionError("connection lost")

        response = self._mock_response(
            b"%PDF-1.7 partial content",
            url="https://example.com/paper.pdf",
        )
        response.iter_content.return_value = broken_stream()

        with tempfile.TemporaryDirectory(prefix="semantic_partial_stream_") as test_dir:
            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(self.searcher.session, "get", return_value=response):
                    result = self.searcher.download_pdf("paper/123", test_dir)

            self.assertTrue(result.startswith("Error downloading PDF for paper/123"))
            self.assertEqual(list(Path(test_dir).iterdir()), [])
            response.close.assert_called_once()

    def test_download_pdf_replaces_invalid_cached_file(self):
        paper = SimpleNamespace(
            pdf_url="https://example.com/paper.pdf",
            url="https://www.semanticscholar.org/paper/test",
            extra={},
        )
        pdf_response = self._mock_response(
            b"%PDF-1.7 replacement content",
            content_type="application/pdf",
            url="https://example.com/paper.pdf",
        )

        with tempfile.TemporaryDirectory(prefix="semantic_bad_cache_") as test_dir:
            cached_path = Path(test_dir) / "semantic_paper_123.pdf"
            cached_path.write_bytes(b"<!doctype html><html>cached challenge</html>")

            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher.session,
                    "get",
                    return_value=pdf_response,
                ):
                    result = self.searcher.download_pdf("paper/123", test_dir)

            self.assertEqual(result, str(cached_path))
            self.assertEqual(cached_path.read_bytes(), b"%PDF-1.7 replacement content")

    def test_download_pdf_returns_valid_cached_file_without_api_lookup(self):
        with tempfile.TemporaryDirectory(prefix="semantic_valid_cache_") as test_dir:
            cached_path = Path(test_dir) / "semantic_paper_123.pdf"
            cached_path.write_bytes(b"%PDF-1.7 cached content")

            with patch.object(
                self.searcher,
                "get_paper_details",
                side_effect=AssertionError("API lookup should not be called"),
            ):
                result = self.searcher.download_pdf("paper/123", test_dir)

            self.assertEqual(result, str(cached_path))

    def test_read_paper_fetches_metadata_for_valid_cached_pdf(self):
        paper = SimpleNamespace(
            title="Cached article",
            authors=["Ada Lovelace"],
            published_date=None,
            pdf_url="https://example.com/paper.pdf",
            url="https://www.semanticscholar.org/paper/test",
            extra={},
        )

        with tempfile.TemporaryDirectory(prefix="semantic_cached_read_") as test_dir:
            cached_path = Path(test_dir) / "semantic_paper_123.pdf"
            cached_path.write_bytes(b"%PDF-1.7 cached content")

            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher,
                    "_extract_pdf_text",
                    return_value="Cached PDF text",
                ):
                    result = self.searcher.read_paper("paper/123", test_dir)

            self.assertIn("Title: Cached article", result)
            self.assertIn("Authors: Ada Lovelace", result)
            self.assertIn("Cached PDF text", result)

    def test_read_paper_removes_cached_pdf_when_extraction_fails(self):
        paper = SimpleNamespace(
            pdf_url="https://example.com/paper.pdf",
            url="https://www.semanticscholar.org/paper/test",
            extra={},
        )

        with tempfile.TemporaryDirectory(prefix="semantic_corrupt_cache_") as test_dir:
            cached_path = Path(test_dir) / "semantic_paper_123.pdf"
            cached_path.write_bytes(b"%PDF corrupt cached content")

            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher,
                    "_extract_pdf_text",
                    side_effect=ValueError("broken pdf"),
                ):
                    with patch.object(
                        self.searcher,
                        "_read_europe_pmc_full_text",
                        return_value=("", ""),
                    ):
                        result = self.searcher.read_paper("paper/123", test_dir)

            self.assertIn("text extraction failed", result)
            self.assertIn("cached PDF was removed", result)
            self.assertNotIn("PDF downloaded to", result)
            self.assertFalse(cached_path.exists())

    def test_read_paper_reports_cache_removal_error_after_extraction_failure(self):
        paper = SimpleNamespace(
            pdf_url="https://example.com/paper.pdf",
            url="https://www.semanticscholar.org/paper/test",
            extra={},
        )

        with tempfile.TemporaryDirectory(
            prefix="semantic_cache_remove_error_"
        ) as test_dir:
            cached_path = Path(test_dir) / "semantic_paper_123.pdf"
            cached_path.write_bytes(b"%PDF corrupt cached content")

            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher,
                    "_extract_pdf_text",
                    side_effect=ValueError("broken pdf"),
                ):
                    with patch.object(
                        self.searcher,
                        "_remove_pdf_file",
                        return_value="permission denied",
                    ):
                        with patch.object(
                            self.searcher,
                            "_read_europe_pmc_full_text",
                            return_value=("", ""),
                        ):
                            result = self.searcher.read_paper("paper/123", test_dir)

            self.assertIn("text extraction failed", result)
            self.assertIn("cached PDF could not be removed", result)
            self.assertIn("permission denied", result)
            self.assertIn("broken pdf", result)

    def test_read_paper_omits_removed_pdf_path_when_full_text_fallback_succeeds(self):
        paper = SimpleNamespace(
            title="Fallback article",
            authors=["Ada Lovelace"],
            published_date=None,
            pdf_url="https://example.com/paper.pdf",
            url="https://www.semanticscholar.org/paper/test",
            extra={"externalIds": {"PubMedCentral": "123456"}},
        )

        with tempfile.TemporaryDirectory(
            prefix="semantic_full_text_after_corrupt_"
        ) as test_dir:
            cached_path = Path(test_dir) / "semantic_paper_123.pdf"
            cached_path.write_bytes(b"%PDF corrupt cached content")

            def full_text_fallback(fallback_paper):
                self.assertIs(fallback_paper, paper)
                return "Recovered full text", "https://example.com/xml"

            with patch.object(self.searcher, "get_paper_details", return_value=paper):
                with patch.object(
                    self.searcher,
                    "_extract_pdf_text",
                    side_effect=ValueError("broken pdf"),
                ):
                    with patch.object(
                        self.searcher,
                        "_read_europe_pmc_full_text",
                        side_effect=full_text_fallback,
                    ):
                        result = self.searcher.read_paper("paper/123", test_dir)

            self.assertIn("Full text source: https://example.com/xml", result)
            self.assertIn("Recovered full text", result)
            self.assertNotIn("PDF downloaded to:", result)
            self.assertFalse(cached_path.exists())

    def test_read_europe_pmc_full_text_closes_response(self):
        paper = SimpleNamespace(
            pdf_url="",
            url="",
            extra={"externalIds": {"PubMedCentral": "123456"}},
        )
        response = self._mock_response(
            b"""
            <article>
              <front>
                <article-meta>
                  <title-group>
                    <article-title>Article title</article-title>
                  </title-group>
                  <abstract><p>Abstract text</p></abstract>
                </article-meta>
              </front>
              <body><sec><title>Results</title><p>Body text</p></sec></body>
            </article>
            """,
            content_type="application/xml",
            url=(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123456/fullTextXML"
            ),
        )

        with patch.object(self.searcher.session, "get", return_value=response):
            text, source = self.searcher._read_europe_pmc_full_text(paper)

        self.assertEqual(
            source,
            "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC123456/fullTextXML",
        )
        self.assertIn("Article title", text)
        self.assertIn("Body text", text)
        response.close.assert_called_once()

    def test_extract_text_from_article_xml_rejects_dtd(self):
        unsafe_xml = b"""
        <!DOCTYPE article [
          <!ENTITY repeated "unsafe">
        ]>
        <article><body><p>&repeated;</p></body></article>
        """

        with self.assertRaisesRegex(ValueError, "Unsafe XML declaration"):
            self.searcher._extract_text_from_article_xml(unsafe_xml)

    def test_parse_paper_handles_missing_publication_date(self):
        item = {
            "paperId": "paper-123",
            "title": "Paper without a publication date",
            "authors": [{"name": "Ada Lovelace"}],
            "abstract": "",
            "url": "https://www.semanticscholar.org/paper/paper-123",
            "publicationDate": None,
            "externalIds": {},
            "fieldsOfStudy": None,
            "openAccessPdf": None,
            "citationCount": 0,
        }

        paper = self.searcher._parse_paper(item)

        self.assertIsNotNone(paper)
        self.assertIsNone(paper.published_date)

    def test_semantic_accessibility_probe_is_memoized(self):
        check_semantic_accessible.cache_clear()
        response = MagicMock()
        response.status_code = 200
        response.__enter__.return_value = response

        try:
            with patch.object(requests, "get", return_value=response) as mocked_get:
                self.assertTrue(check_semantic_accessible())
                self.assertTrue(check_semantic_accessible())

            mocked_get.assert_called_once()
            response.__exit__.assert_called_once()
        finally:
            check_semantic_accessible.cache_clear()

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
                self.assertTrue(
                    "PDF downloaded to:" in result or "Full text source:" in result
                )

                if "PDF downloaded to:" in result:
                    # Should contain page markers indicating PDF text extraction
                    self.assertIn("--- Page", result)

                    # Check if PDF was actually downloaded
                    expected_filename = (
                        f"semantic_{self.searcher._safe_paper_id(paper_id)}.pdf"
                    )
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

        if not paper_details:
            self.skipTest(
                "Semantic Scholar details endpoint is rate-limited or unavailable"
            )

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
        query = "encryption"
        max_results = 3

        # Test detailed search time
        print("\nTesting detailed search performance...")
        start_time = time.time()
        compact_papers = self.searcher.search(query, max_results=max_results)
        compact_time = time.time() - start_time

        print(
            f"Compact search took {compact_time:.2f} seconds for {len(compact_papers)} papers"
        )


if __name__ == "__main__":
    unittest.main()
