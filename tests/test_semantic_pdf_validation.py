"""Offline regressions for Semantic Scholar PDF downloads and cache safety."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from pypdf import PdfReader, PdfWriter

from paper_search_mcp.academic_platforms import semantic


@pytest.fixture
def pdf_bytes():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(stream)
    content = stream.getvalue()
    assert len(PdfReader(BytesIO(content)).pages) == 1
    return content


@pytest.fixture
def searcher(monkeypatch):
    instance = semantic.SemanticSearcher()
    paper = SimpleNamespace(
        pdf_url="https://example.org/paper.pdf",
        title="Test paper",
        authors=["Test Author"],
        published_date=None,
        url="https://example.org/paper",
    )
    monkeypatch.setattr(instance, "get_paper_details", Mock(return_value=paper))
    # Every download must opt into a response below, never the real network.
    monkeypatch.setattr(
        semantic.requests, "get", Mock(side_effect=AssertionError("Unexpected request"))
    )
    return instance


def mock_response(monkeypatch, content, content_type="application/pdf", status=200):
    response = requests.Response()
    response.status_code = status
    response.url = "https://example.org/paper.pdf"
    response._content = content
    if content_type is not None:
        response.headers["Content-Type"] = content_type
    get = Mock(return_value=response)
    monkeypatch.setattr(semantic.requests, "get", get)
    return get


@pytest.mark.parametrize("method", ["download_pdf", "read_paper"])
@pytest.mark.parametrize(
    "content,content_type",
    [
        (b"", "application/pdf"),
        (b" \r\n\t", "application/pdf"),
        (b"<html>Sign in to download</html>", "text/html"),
        (b"<html>Sign in to download</html>", "application/pdf"),
        (b"<!DOCTYPE html><title>Login</title>", None),
        (b"<html><code>%PDF-1.7</code></html>", "application/pdf"),
        (b'{"error": "access denied"}', "application/pdf"),
        (b"%PDF-", "application/pdf"),
        (b"%PDF-not-a-version\n", "application/pdf"),
        (b"%PDF-1.7HTML", "application/pdf"),
        (b" " * 1024 + b"%PDF-1.7\n", "application/pdf"),
    ],
)
def test_invalid_response_is_not_saved(
    searcher, monkeypatch, tmp_path, method, content, content_type
):
    get = mock_response(monkeypatch, content, content_type)
    reader = Mock(side_effect=AssertionError("Invalid bytes must not reach PdfReader"))
    monkeypatch.setattr(semantic, "PdfReader", reader)

    result = getattr(searcher, method)("paper/123", str(tmp_path))

    assert result.startswith("Error downloading PDF:")
    assert "PDF" in result
    assert list(tmp_path.iterdir()) == []
    get.assert_called_once_with("https://example.org/paper.pdf", timeout=30)
    reader.assert_not_called()


@pytest.mark.parametrize("content_type", ["application/pdf", "text/html", None])
@pytest.mark.parametrize("prefix", [b"", b"\r\n \t", b"\xef\xbb\xbf", b"\xef\xbb\xbf\r\n"])
@pytest.mark.parametrize("version", [b"1.4", b"1.7", b"2.0"])
def test_pdf_bytes_are_saved_regardless_of_url_or_mime(
    searcher, monkeypatch, tmp_path, pdf_bytes, content_type, prefix, version
):
    # A header signature, not a .pdf suffix or MIME assertion, establishes type.
    searcher.get_paper_details.return_value.pdf_url = "https://example.org/download?id=123"
    content = prefix + pdf_bytes.replace(b"%PDF-1.3", b"%PDF-" + version, 1)
    get = mock_response(monkeypatch, content, content_type)
    save_path = tmp_path / "new" / "downloads"

    result = searcher.download_pdf("paper/123", str(save_path))

    expected = save_path / "semantic_paper_123.pdf"
    assert result == str(expected)
    assert expected.read_bytes() == content
    assert list(save_path.iterdir()) == [expected]
    get.assert_called_once_with("https://example.org/download?id=123", timeout=30)


def test_read_paper_downloads_valid_pdf_and_keeps_existing_result_format(
    searcher, monkeypatch, tmp_path, pdf_bytes
):
    mock_response(monkeypatch, pdf_bytes)

    result = searcher.read_paper("paper/123", str(tmp_path))

    expected = tmp_path / "semantic_paper_123.pdf"
    assert expected.read_bytes() == pdf_bytes
    assert result == f"PDF downloaded to {expected}, but unable to extract readable text"


def test_read_paper_reuses_cached_file_and_metadata_format(
    searcher, monkeypatch, tmp_path, pdf_bytes
):
    cached = tmp_path / "semantic_paper_123.pdf"
    cached.write_bytes(pdf_bytes)
    reader = Mock(return_value=SimpleNamespace(pages=[Mock(extract_text=lambda: "Paper text")]))
    monkeypatch.setattr(semantic, "PdfReader", reader)

    result = searcher.read_paper("paper/123", str(tmp_path))

    semantic.requests.get.assert_not_called()
    searcher.get_paper_details.assert_called_once_with("paper/123")
    reader.assert_called_once_with(str(cached))
    assert cached.read_bytes() == pdf_bytes
    assert result.startswith("Title: Test paper\nAuthors: Test Author\nPublished Date: None\n")
    assert f"PDF downloaded to: {cached}\n" in result
    assert result.endswith("--- Page 1 ---\nPaper text")


@pytest.mark.parametrize("method", ["download_pdf", "read_paper"])
@pytest.mark.parametrize("paper", [None, SimpleNamespace(pdf_url="")])
def test_missing_pdf_url_keeps_error_and_makes_no_request(searcher, tmp_path, method, paper):
    searcher.get_paper_details.return_value = paper

    result = getattr(searcher, method)("paper/123", str(tmp_path))

    assert result == "Error: Could not find PDF URL for paper paper/123"
    semantic.requests.get.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("method", ["download_pdf", "read_paper"])
@pytest.mark.parametrize("failure", ["http", "timeout"])
def test_request_errors_keep_download_error_prefix(
    searcher, monkeypatch, tmp_path, pdf_bytes, method, failure
):
    get = mock_response(monkeypatch, pdf_bytes, status=403 if failure == "http" else 200)
    if failure == "timeout":
        get.side_effect = requests.Timeout("Timed out")

    result = getattr(searcher, method)("paper/123", str(tmp_path))

    assert result.startswith("Error downloading PDF:")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("content,status", [(b"", 200), (b"<html>Login</html>", 200), (b"", 503)])
def test_failed_download_preserves_cached_file(searcher, monkeypatch, tmp_path, pdf_bytes, content, status):
    cached = tmp_path / "semantic_paper_123.pdf"
    cached.write_bytes(pdf_bytes)
    mock_response(monkeypatch, content, status=status)

    result = searcher.download_pdf("paper/123", str(tmp_path))

    assert result.startswith("Error downloading PDF:")
    assert cached.read_bytes() == pdf_bytes
    assert list(tmp_path.iterdir()) == [cached]


@pytest.mark.parametrize("existing_file", [False, True])
@pytest.mark.parametrize("failure", ["write", "replace"])
def test_write_failures_clean_temporary_file_and_preserve_cache(
    searcher, monkeypatch, tmp_path, pdf_bytes, existing_file, failure
):
    cached = tmp_path / "semantic_paper_123.pdf"
    if existing_file:
        cached.write_bytes(pdf_bytes)
    mock_response(monkeypatch, pdf_bytes)

    if failure == "replace":
        def fail_replace(source, destination):
            assert Path(source).read_bytes() == pdf_bytes
            assert Path(source).parent == tmp_path
            assert Path(destination) == cached
            assert source != destination
            raise OSError("Replacement failed")

        monkeypatch.setattr(semantic.os, "replace", fail_replace)
    else:
        import tempfile

        original = tempfile.NamedTemporaryFile

        def fail_write(*args, **kwargs):
            temporary = original(*args, **kwargs)
            write = temporary.write

            def partial_write(content):
                write(content[:20])
                raise OSError("Disk full")

            temporary.write = partial_write
            return temporary

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail_write)

    result = searcher.download_pdf("paper/123", str(tmp_path))

    assert result.startswith("Error downloading PDF:")
    if existing_file:
        assert cached.read_bytes() == pdf_bytes
    assert list(tmp_path.iterdir()) == ([cached] if existing_file else [])


def test_successful_download_replaces_cache_only_after_complete_write(
    searcher, monkeypatch, tmp_path, pdf_bytes
):
    cached = tmp_path / "semantic_paper_123.pdf"
    old_content = pdf_bytes + b"\n% previous download\n"
    cached.write_bytes(old_content)
    mock_response(monkeypatch, pdf_bytes)
    replace = semantic.os.replace

    def verify_replace(source, destination):
        assert cached.read_bytes() == old_content
        assert Path(source).read_bytes() == pdf_bytes
        assert Path(source).parent == tmp_path
        replace(source, destination)

    replacement = Mock(side_effect=verify_replace)
    monkeypatch.setattr(semantic.os, "replace", replacement)

    assert searcher.download_pdf("paper/123", str(tmp_path)) == str(cached)
    replacement.assert_called_once()
    assert cached.read_bytes() == pdf_bytes
    assert list(tmp_path.iterdir()) == [cached]


@pytest.mark.parametrize("existing_file", [False, True])
@pytest.mark.parametrize("body", [b"%PDF-1.7\n", b"%PDF-1.7\n<html>Login</html>", None])
def test_malformed_pdf_body_does_not_replace_cache(
    searcher, monkeypatch, tmp_path, pdf_bytes, existing_file, body
):
    cached = tmp_path / "semantic_paper_123.pdf"
    if existing_file:
        cached.write_bytes(pdf_bytes)
    # Truncate a real PDF before its cross-reference table/trailer as well.
    content = body if body is not None else pdf_bytes.split(b"xref", 1)[0]
    mock_response(monkeypatch, content)

    result = searcher.download_pdf("paper/123", str(tmp_path))

    assert result == "Error downloading PDF: Downloaded PDF could not be parsed"
    if existing_file:
        assert cached.read_bytes() == pdf_bytes
    assert list(tmp_path.iterdir()) == ([cached] if existing_file else [])


def test_read_paper_does_not_cache_truncated_pdf(searcher, monkeypatch, tmp_path):
    mock_response(monkeypatch, b"%PDF-1.7\n")

    result = searcher.read_paper("paper/123", str(tmp_path))

    assert result == "Error downloading PDF: Downloaded PDF could not be parsed"
    assert list(tmp_path.iterdir()) == []


def test_valid_encrypted_pdf_can_still_be_downloaded(searcher, monkeypatch, tmp_path):
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("test-password")
    writer.write(stream)
    content = stream.getvalue()
    assert PdfReader(BytesIO(content)).is_encrypted
    mock_response(monkeypatch, content)

    result = searcher.download_pdf("paper/123", str(tmp_path))

    assert Path(result).read_bytes() == content


def test_long_valid_paper_id_does_not_exceed_temporary_filename_limit(
    searcher, monkeypatch, tmp_path, pdf_bytes
):
    paper_id = "URL:https://example.org/" + "x" * 208
    mock_response(monkeypatch, pdf_bytes)

    result = searcher.download_pdf(paper_id, str(tmp_path))

    expected = tmp_path / f"semantic_{paper_id.replace('/', '_')}.pdf"
    assert result == str(expected)
    assert expected.read_bytes() == pdf_bytes
    assert list(tmp_path.iterdir()) == [expected]
