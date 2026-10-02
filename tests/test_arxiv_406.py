"""Deterministic coverage of anomalous arXiv HTTP 406 responses (#121)."""
import asyncio
from unittest.mock import Mock, patch

import pytest
import requests

from paper_search_mcp import server
from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher
from tests.test_arxiv import EMPTY_ATOM_FEED, response_with


ARXIV_FEED = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <id>http://arxiv.org/api/test-query</id>
  <title>ArXiv Query</title>
  <updated>2026-09-24T00:00:00Z</updated>
  <opensearch:totalResults>1</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>Machine unlearning</title>
    <summary>A paper about machine unlearning.</summary>
    <published>2024-01-24T10:00:00Z</published>
    <updated>2024-01-24T10:00:00Z</updated>
    <author><name>Example Author</name></author>
    <link href="http://arxiv.org/abs/2401.12345v1" type="text/html"/>
    <link href="http://arxiv.org/pdf/2401.12345v1" type="application/pdf"/>
    <category term="cs.LG"/>
  </entry>
</feed>'''


@pytest.fixture
def paced_searcher():
    searcher = ArxivSearcher()
    with (
        patch.object(ArxivSearcher, "_pace_locked") as pace,
        patch("paper_search_mcp.academic_platforms.arxiv.time.sleep") as wait,
    ):
        yield searcher, pace, wait


def test_valid_406_arxiv_feed_returns_papers_without_retry(paced_searcher):
    searcher, pace, wait = paced_searcher
    searcher.session.get = Mock(return_value=response_with(ARXIV_FEED, 406))

    papers = searcher.search("machine unlearning")

    assert len(papers) == 1
    assert papers[0].paper_id == "2401.12345v1"
    assert papers[0].authors == ["Example Author"]
    assert papers[0].source == "arxiv"
    assert papers[0].abstract == "A paper about machine unlearning."
    pace.assert_called_once_with()
    wait.assert_not_called()
    assert searcher.session.get.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize("body", [
    b"",
    b"<html><body>Not Acceptable</body></html>",
    b"Rate exceeded.",
    EMPTY_ATOM_FEED,
    ARXIV_FEED.replace(b"<opensearch:totalResults>1", b"<opensearch:totalResults>0"),
    ARXIV_FEED.replace(b"</feed>", b""),
    ARXIV_FEED.replace(b"http://www.w3.org/2005/Atom", b"urn:other"),
    ARXIV_FEED.replace(b"http://arxiv.org/api/", b"http://example.org/api/"),
    ARXIV_FEED.replace(b"http://arxiv.org/abs/2401.12345v1", b"http://arxiv.org/api/errors#bad_query"),
    ARXIV_FEED.replace(b"http://arxiv.org/abs/", b"http://arxiv.org.evil.example/abs/"),
    ARXIV_FEED.replace(b"2024-01-24T10:00:00Z", b"bad-date"),
    ARXIV_FEED.replace(b"<author><name>Example Author</name></author>", b""),
], ids=["empty-body", "html", "soft-limit", "empty-feed", "zero-total", "truncated",
        "wrong-namespace", "unrelated-feed", "api-error", "wrong-host", "invalid-entry", "no-authors"])
def test_unusable_406_retries_then_raises(body, paced_searcher):
    searcher, pace, wait = paced_searcher
    searcher.session.get = Mock(return_value=response_with(body, 406))

    with pytest.raises(requests.RequestException, match="HTTP 406.*usable arXiv Atom"):
        searcher.search("test")

    assert searcher.session.get.call_count == ArxivSearcher.MAX_ATTEMPTS
    assert pace.call_count == ArxivSearcher.MAX_ATTEMPTS
    assert [call.args for call in wait.call_args_list] == [(1.5,), (3.0,)]


@pytest.mark.parametrize("body,status,expected", [
    (ARXIV_FEED, 200, 1),
    (ARXIV_FEED, 406, 1),
    (EMPTY_ATOM_FEED, 200, 0),
])
def test_unusable_406_can_recover(body, status, expected, paced_searcher):
    searcher, pace, wait = paced_searcher
    searcher.session.get = Mock(side_effect=[response_with(b"", 406), response_with(body, status)])

    assert len(searcher.search("test")) == expected
    assert pace.call_count == 2
    wait.assert_called_once_with(1.5)


@pytest.mark.parametrize("last_response", [requests.ConnectionError("offline"), response_with(b"", 503)])
def test_406_followed_by_failures_cannot_become_false_zero(last_response, paced_searcher):
    searcher, _, _ = paced_searcher
    searcher.session.get = Mock(side_effect=[response_with(b"", 406), last_response, last_response])
    with pytest.raises(requests.RequestException, match="HTTP 406"):
        searcher.search("test")
    assert searcher.session.get.call_count == 3


def test_normal_200_empty_feed_is_still_successful(paced_searcher):
    searcher, pace, wait = paced_searcher
    searcher.session.get = Mock(return_value=response_with(EMPTY_ATOM_FEED))
    assert searcher.search("test") == []
    pace.assert_called_once_with()
    wait.assert_not_called()


@pytest.mark.parametrize("status", [400, 403, 404, 500])
def test_unrelated_http_failure_contract_is_unchanged(status, paced_searcher):
    searcher, _, _ = paced_searcher
    # A valid body must not silently override any other non-200 status.
    searcher.session.get = Mock(return_value=response_with(ARXIV_FEED, status))
    assert searcher.search("test") == []


def test_unified_search_reports_406_as_source_error(paced_searcher):
    searcher, _, _ = paced_searcher
    searcher.session.get = Mock(return_value=response_with(b"", 406))
    with patch.object(server, "arxiv_searcher", searcher):
        result = asyncio.run(server.search_papers("test", sources="arxiv"))
    assert "HTTP 406" in result["errors"]["arxiv"]
    assert result["source_results"] == {"arxiv": 0}
    assert result["papers"] == []


# Artifact integrity regressions use the existing deterministic arXiv test entry.
import io
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import requests
from pypdf import PdfWriter

from paper_search_mcp.academic_platforms import arxiv


@pytest.fixture
def pdf_bytes():
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def searcher(monkeypatch):
    monkeypatch.setattr(arxiv.ArxivSearcher, '_pace_locked', Mock())
    return arxiv.ArxivSearcher()


def response(monkeypatch, chunks, status=200):
    result = MagicMock()
    result.__enter__.return_value = result
    result.status_code = status
    result.iter_content.return_value = iter(chunks)
    get = Mock(return_value=result)
    monkeypatch.setattr(arxiv.requests, 'get', get)
    return get


@pytest.mark.parametrize('paper_id', ['2406.17835', '2406.17835v2', 'hep-th/9901001v1'])
def test_success_is_paced_streamed_and_atomically_saved(searcher, monkeypatch, tmp_path, pdf_bytes, paper_id):
    get = response(monkeypatch, [pdf_bytes[:11], b'', pdf_bytes[11:]])
    path = searcher.download_pdf(paper_id, str(tmp_path / 'with spaces'))
    assert open(path, 'rb').read() == pdf_bytes
    assert get.call_args.kwargs == {'stream': True, 'timeout': (10, 30)}
    searcher._pace_locked.assert_called_once_with()
    assert not list(tmp_path.rglob('*.part'))


@pytest.mark.parametrize('status', [403, 404, 429, 500, 503])
def test_http_failure_preserves_old_file_without_retry(searcher, monkeypatch, tmp_path, status):
    target = tmp_path / '2406.17835.pdf'
    target.write_bytes(b'old preserved bytes')
    get = response(monkeypatch, [b'<html>denied</html>'], status)
    with pytest.raises(requests.RequestException, match=f'HTTP {status}'):
        searcher.download_pdf('2406.17835', str(tmp_path))
    assert target.read_bytes() == b'old preserved bytes'
    assert get.call_count == 1
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('body', [b'', b'<html>denied</html>', b'%PDF-1.7\ntruncated'])
def test_invalid_body_preserves_old_pdf(searcher, monkeypatch, tmp_path, body, pdf_bytes):
    target = tmp_path / '2406.17835.pdf'
    target.write_bytes(pdf_bytes)
    response(monkeypatch, [body])
    with pytest.raises(requests.RequestException, match='PDF'):
        searcher.download_pdf('2406.17835', str(tmp_path))
    assert target.read_bytes() == pdf_bytes
    assert not list(tmp_path.glob('*.part'))


def test_partial_stream_timeout_cleans_temp_and_preserves_old(searcher, monkeypatch, tmp_path, pdf_bytes):
    target = tmp_path / '2406.17835.pdf'
    target.write_bytes(pdf_bytes)
    def interrupted():
        yield b'%PDF-1.7\n'
        raise requests.Timeout('mock read timeout')
    response(monkeypatch, interrupted())
    with pytest.raises(requests.Timeout):
        searcher.download_pdf('2406.17835', str(tmp_path))
    assert target.read_bytes() == pdf_bytes
    assert not list(tmp_path.glob('*.part'))
    assert searcher._request_lock.acquire(blocking=False)
    searcher._request_lock.release()


def test_size_limit_cleans_partial_without_replacing_target(searcher, monkeypatch, tmp_path, pdf_bytes):
    monkeypatch.setattr(searcher, 'MAX_PDF_BYTES', 10)
    target = tmp_path / '2406.17835.pdf'
    target.write_bytes(pdf_bytes)
    response(monkeypatch, [b'%PDF-1.7\nmore'])
    with pytest.raises(requests.RequestException, match='limit'):
        searcher.download_pdf('2406.17835', str(tmp_path))
    assert target.read_bytes() == pdf_bytes
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('paper_id', ['../secret', '/etc/passwd', '2406.17835/../../x', 'x?y=z', '2406.17835.pdf', '2406.17835v0', None])
def test_invalid_id_rejected_before_network_or_directory(searcher, monkeypatch, tmp_path, paper_id):
    get = Mock()
    monkeypatch.setattr(arxiv.requests, 'get', get)
    for method in (searcher.download_pdf, searcher.read_paper):
        with pytest.raises(ValueError, match='arXiv paper ID'):
            method(paper_id, str(tmp_path / 'absent'))
    get.assert_not_called()
    assert not (tmp_path / 'absent').exists()


def test_replace_failure_keeps_previous_file_and_removes_partial(searcher, monkeypatch, tmp_path, pdf_bytes):
    target = tmp_path / '2406.17835.pdf'
    target.write_bytes(b'previous')
    response(monkeypatch, [pdf_bytes])
    monkeypatch.setattr(arxiv.os, 'replace', Mock(side_effect=PermissionError('locked')))
    with pytest.raises(PermissionError):
        searcher.download_pdf('2406.17835', str(tmp_path))
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('kind', ['html', 'empty', 'image_only'])
def test_failed_read_does_not_return_blank_success(searcher, tmp_path, pdf_bytes, kind):
    body = {'html': b'<html>denied</html>', 'empty': b'', 'image_only': pdf_bytes}[kind]
    target = tmp_path / '2406.17835.pdf'
    target.write_bytes(body)
    with pytest.raises(RuntimeError, match='could not be read as text'):
        searcher.read_paper('2406.17835', str(tmp_path))
    assert target.read_bytes() == body


def test_read_preserves_text_with_an_empty_page(searcher, monkeypatch, tmp_path, pdf_bytes):
    (tmp_path / '2406.17835.pdf').write_bytes(pdf_bytes)
    monkeypatch.setattr(arxiv, 'PdfReader', lambda path: SimpleNamespace(pages=[
        SimpleNamespace(extract_text=lambda: None), SimpleNamespace(extract_text=lambda: 'Paper text')]))
    assert searcher.read_paper('2406.17835', str(tmp_path)) == 'Paper text'
