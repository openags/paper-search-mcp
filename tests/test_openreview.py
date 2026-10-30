"""Offline public-only OpenReview and validated PDF coverage."""
import asyncio
import io
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from paper_search_mcp.academic_platforms.openreview import OpenReviewSearcher, OpenReviewError
from paper_search_mcp.academic_platforms.public_pdf import save_verified_pdf


def response(data=None, status=200, body=b""):
    result = Mock(status_code=status)
    result.json.return_value = data
    result.iter_content.return_value = iter([body[:37], body[37:]])
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def note(note_id="Note123", **overrides):
    result = {"id": note_id, "forum": note_id, "readers": ["everyone"], "cdate": 1700000000000,
              "content": {"title": {"value": "Verified public research"},
                          "authors": {"value": ["Alice", "Bob"]},
                          "abstract": {"value": "An abstract"}, "keywords": {"value": ["science"]},
                          "pdf": {"value": f"/pdf?id={note_id}"}}}
    result.update(overrides)
    return result


def pdf_bytes(title="Verified public research"):
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 50 700 Td ({title}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_public_search_parses_v2_and_is_anonymous():
    searcher = OpenReviewSearcher()
    searcher.session.get = Mock(return_value=response({"notes": [note()]}))
    paper, = searcher.search("research", 1)
    assert paper.paper_id == "Note123"
    assert paper.authors == ["Alice", "Bob"]
    assert paper.published_date.year == 2023
    assert paper.keywords == ["science"]
    assert paper.pdf_url == "https://openreview.net/pdf?id=Note123"
    assert paper.url == "https://openreview.net/forum?id=Note123"
    assert "Authorization" not in searcher.session.headers
    params = searcher.session.get.call_args.kwargs["params"]
    assert params["source"] == "forum"
    assert params["offset"] == 0
    assert searcher.session.get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("overrides", [
    {"replyto": "Parent"}, {"forum": "Parent"}, {"readers": ["private/group"]},
    {"ddate": 123}, {"id": "../bad"}, {"content": {"title": {"value": "Announcement"}}},
])
def test_non_public_non_paper_notes_are_excluded(overrides):
    assert OpenReviewSearcher()._parse_note(note(**overrides)) is None


def test_external_pdf_is_not_claimed_as_openreview_download():
    data = note()
    data["content"]["pdf"]["value"] = "https://arxiv.org/pdf/1234.5678"
    assert OpenReviewSearcher()._parse_note(data).pdf_url == ""


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500])
def test_search_http_failures_are_not_empty_results(status):
    searcher = OpenReviewSearcher()
    searcher.session.get = Mock(return_value=response(status=status))
    with pytest.raises(OpenReviewError, match="denied|HTTP"):
        searcher.search("research")
    assert searcher.session.get.call_count == 1


@pytest.mark.parametrize("data", [{}, [], {"notes": None}, {"notes": [None]}, {"notes": [{}]}])
def test_malformed_search_is_an_error(data):
    searcher = OpenReviewSearcher()
    searcher.session.get = Mock(return_value=response(data))
    with pytest.raises(OpenReviewError):
        searcher.search("research")


def test_invalid_json_and_network_are_errors():
    searcher = OpenReviewSearcher()
    bad = response()
    bad.json.side_effect = ValueError("bad JSON")
    for mocked in [Mock(return_value=bad), Mock(side_effect=requests.Timeout("timeout"))]:
        searcher.session.get = mocked
        with pytest.raises(OpenReviewError):
            searcher.search("research")


def test_zero_and_successful_empty():
    searcher = OpenReviewSearcher()
    searcher.session.get = Mock(return_value=response({"notes": []}))
    assert searcher.search("research", 0) == []
    searcher.session.get.assert_not_called()
    assert searcher.search("research", 10) == []


@pytest.mark.parametrize("limit", [-1, 1001, 1.5, True])
def test_limits_are_explicit(limit):
    with pytest.raises(ValueError):
        OpenReviewSearcher().search("research", limit)


def test_offset_pagination_and_deduplication():
    searcher = OpenReviewSearcher()
    searcher.PAGE_SIZE = 2
    searcher.session.get = Mock(side_effect=[response({"notes": [note("one"), note("two")]}),
                                             response({"notes": [note("two"), note("three")]}),
                                             response({"notes": [note("four")]})])
    assert [p.paper_id for p in searcher.search("research", 4)] == ["one", "two", "three", "four"]
    assert [c.kwargs["params"]["offset"] for c in searcher.session.get.call_args_list] == [0, 2, 4]


def test_duplicate_pages_raise_instead_of_looping():
    searcher = OpenReviewSearcher()
    searcher.PAGE_SIZE = 1
    searcher.session.get = Mock(return_value=response({"notes": [note()]}))
    with pytest.raises(OpenReviewError, match="did not advance"):
        searcher.search("research", 3)


def test_scan_limit_is_not_silently_partial():
    searcher = OpenReviewSearcher()
    searcher.MAX_RESULTS = 2
    searcher.session.get = Mock(return_value=response({"notes": [note(replyto="Parent"), note(replyto="Parent")]}))
    with pytest.raises(OpenReviewError, match="scan limit"):
        searcher.search("research", 2)


@pytest.mark.parametrize("value", ["Note_1-x", "openreview:Note_1-x", "https://openreview.net/forum?id=Note_1-x", "https://openreview.net/pdf?id=Note_1-x"])
def test_valid_note_ids(value):
    assert OpenReviewSearcher._note_id(value) == "Note_1-x"


@pytest.mark.parametrize("value", ["../escape", "", "https://evil.test/forum?id=Note", "https://openreview.net.evil.test/forum?id=Note",
                                    "https://u@openreview.net/forum?id=Note", "https://openreview.net:443/forum?id=Note",
                                    "https://openreview.net/forum?id=Note&id=Other", "https://openreview.net/forum?id=../bad"])
def test_invalid_note_ids(value):
    with pytest.raises(ValueError):
        OpenReviewSearcher._note_id(value)


def test_download_verifies_identity_and_content(tmp_path):
    searcher = OpenReviewSearcher()
    searcher.session.get = Mock(side_effect=[response({"notes": [note()]}), response(body=pdf_bytes())])
    saved = searcher.download_pdf("https://openreview.net/forum?id=Note123", str(tmp_path))
    assert Path(saved).read_bytes().startswith(b"%PDF-")
    assert Path(saved).name == "openreview_Note123.pdf"
    assert searcher.session.get.call_args.kwargs["params"] == {"id": "Note123"}


@pytest.mark.parametrize("returned", [[note("Other")], [], [note(), note()], [note(readers=["private"])], [note(replyto="Other")]])
def test_download_rejects_identity_mismatch_before_requesting_pdf(returned, tmp_path):
    searcher = OpenReviewSearcher()
    searcher.session.get = Mock(return_value=response({"notes": returned}))
    with pytest.raises(OpenReviewError):
        searcher.download_pdf("Note123", str(tmp_path))
    assert searcher.session.get.call_count == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("body", [b"<html>login</html>", b"%PDF-fake %%EOF", pdf_bytes("Wrong title"), pdf_bytes()[:-20]])
def test_bad_pdf_never_replaces_good_file(body, tmp_path):
    output = tmp_path / "known.pdf"
    output.write_bytes(b"existing")
    with pytest.raises(ValueError):
        save_verified_pdf(response(body=body), output, "Verified public research")
    assert output.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [output]


def test_pdf_size_bound(tmp_path, monkeypatch):
    monkeypatch.setattr("paper_search_mcp.academic_platforms.public_pdf.MAX_PDF_BYTES", 20)
    with pytest.raises(ValueError, match="limit"):
        save_verified_pdf(response(body=pdf_bytes()), tmp_path / "large.pdf", "Verified public research")
    assert not list(tmp_path.iterdir())


def test_registration_and_unified_error(monkeypatch):
    from paper_search_mcp import cli, server
    assert "openreview" in cli.ALL_SOURCES
    assert isinstance(cli._get_searcher("openreview"), OpenReviewSearcher)
    assert "openreview" in server.ALL_SOURCES
    monkeypatch.setattr(server.openreview_searcher, "search", Mock(side_effect=OpenReviewError("HTTP 429")))
    result = asyncio.run(server.search_papers("research", sources="openreview"))
    assert result["errors"] == {"openreview": "HTTP 429"}
    assert result["papers"] == []


def test_anonymous_auth_does_not_read_netrc_or_disable_proxy_configuration(monkeypatch):
    searcher = OpenReviewSearcher()
    lookup = Mock(side_effect=AssertionError("Must not read .netrc"))
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lookup)
    prepared = searcher.session.prepare_request(requests.Request("GET", searcher.BASE_URL + "/notes"))
    assert "Authorization" not in prepared.headers
    assert searcher.session.trust_env is True
    lookup.assert_not_called()


def test_read_extracts_validated_pdf_text(tmp_path):
    searcher = OpenReviewSearcher()
    searcher.session.get = Mock(side_effect=[response({"notes": [note()]}), response(body=pdf_bytes())])
    assert "Verified public research" in searcher.read_paper("Note123", str(tmp_path))


def test_partial_network_body_never_writes_file(tmp_path):
    result = response(body=b"")
    def broken():
        yield b"%PDF-partial"
        raise requests.ConnectionError("broken stream")
    result.iter_content.return_value = broken()
    with pytest.raises(requests.ConnectionError):
        save_verified_pdf(result, tmp_path / "paper.pdf", "Title")
    assert not list(tmp_path.iterdir())
