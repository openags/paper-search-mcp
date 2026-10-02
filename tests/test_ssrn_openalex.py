"""Cursor pagination and strict identity coverage adapted from PR #60."""
import asyncio
from unittest.mock import Mock

import pytest
import requests

from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher
from paper_search_mcp.academic_platforms.ssrn import SSRNSearcher
from tests.test_openreview import response, pdf_bytes


def work(abstract_id="1234567", **overrides):
    item = {"id": f"https://openalex.org/W{abstract_id}", "title": "Verified public research",
            "authorships": [{"author": {"display_name": "Alice"}}],
            "abstract_inverted_index": {"Test": [0], "abstract": [1]},
            "doi": "https://doi.org/10.1234/example", "publication_date": "2024-01-15", "cited_by_count": 7,
            "primary_location": {"landing_page_url": f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={abstract_id}",
                                 "pdf_url": f"https://papers.ssrn.com/sol3/Delivery.cfm?abstract_id={abstract_id}"}}
    item.update(overrides)
    return item


def payload(items, cursor=None):
    return response({"results": items, "meta": {"next_cursor": cursor}})


def searcher(*responses):
    result = OpenAlexSearcher(api_key="", email="")
    result.session.get = Mock(side_effect=responses)
    return result


def test_ssrn_normalized_identity_and_metadata():
    search = searcher(payload([work()]))
    paper, = search.search_ssrn("law")
    assert paper.paper_id == "ssrn:1234567"
    assert SSRNSearcher._extract_abstract_id(paper.paper_id) == "1234567"
    assert paper.source == "ssrn"
    assert paper.authors == ["Alice"]
    assert paper.abstract == "Test abstract"
    assert paper.published_date.year == 2024
    assert paper.pdf_url == ""  # An OpenAlex OA locator is not a verified PDF.
    assert search.session.get.call_args.kwargs["params"]["filter"] == "locations.source.id:S4210172589"


def test_ssrn_location_can_be_secondary():
    item = work(primary_location={"landing_page_url": "https://doi.org/10.1000/published"},
                locations=[None, {"landing_page_url": "https://papers.ssrn.com/abstract=3456789"}])
    paper, = searcher(payload([item])).search_ssrn("law")
    assert paper.paper_id == "ssrn:3456789"
    assert paper.url.endswith("abstract_id=3456789")


@pytest.mark.parametrize("url", ["https://evil.test/?abstract_id=123", "https://papers.ssrn.com.evil.test/abstract=123",
                                 "https://evil.test/ssrn.com/abstract=123", "https://u@papers.ssrn.com/abstract=123",
                                 "https://papers.ssrn.com:443/abstract=123", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1&abstract_id=2",
                                 "file:///abstract=123", "https://papers.ssrn.com/unrelated?abstract_id=123"])
def test_spoofed_or_ambiguous_ssrn_locators_are_rejected(url):
    assert OpenAlexSearcher._extract_ssrn_abstract_id(url) == ""
    assert SSRNSearcher._extract_abstract_id(url) == ""


@pytest.mark.parametrize("locator", ["https://papers.ssrn.com/abstract=123", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=123",
                                     "https://doi.org/10.2139/ssrn.123", "https://dx.doi.org/10.2139/ssrn.123"])
def test_official_ssrn_locators(locator):
    assert OpenAlexSearcher._extract_ssrn_abstract_id(locator) == "123"


def test_ambiguous_and_unidentified_records_are_skipped():
    invalid = work(primary_location={"landing_page_url": "https://openalex.org/W1"})
    ambiguous = work(locations=[{"landing_page_url": "https://papers.ssrn.com/abstract=9876"}])
    assert len(searcher(payload([invalid, ambiguous, work()])).search_ssrn("law")) == 1


def test_cursor_pagination_over_100_and_no_extra_request():
    first = [work(str(1000000 + i)) for i in range(100)]
    second = [work(str(1000100 + i)) for i in range(100)]
    third = [work(str(1000200 + i)) for i in range(100)]
    search = searcher(payload(first, "two"), payload(second, "three"), payload(third))
    papers = search.search_ssrn("law", 250)
    assert len(papers) == 250
    assert papers[-1].paper_id == "ssrn:1000249"
    assert [c.kwargs["params"]["cursor"] for c in search.session.get.call_args_list] == ["*", "two", "three"]
    assert all(c.kwargs["params"]["per_page"] == 100 for c in search.session.get.call_args_list)


def test_null_cursor_stops_even_with_shortfall():
    search = searcher(payload([work(str(i + 1)) for i in range(80)]))
    assert len(search.search_ssrn("law", 150)) == 80
    assert search.session.get.call_count == 1


def test_duplicate_ids_and_cursor_cycle():
    search = searcher(payload([work()], "two"), payload([work(), work("234")]))
    assert len(search.search_ssrn("law")) == 2
    search = searcher(payload([work()], "*"))
    with pytest.raises(RuntimeError, match="repeated a cursor"):
        search.search_ssrn("law")


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_failures_are_not_zero_results(status):
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        searcher(response(status=status)).search_ssrn("law")


def test_later_page_failure_does_not_return_partial_as_success():
    search = searcher(payload([work()], "two"), response(status=503))
    with pytest.raises(RuntimeError, match="503"):
        search.search_ssrn("law")


@pytest.mark.parametrize("data", [{}, [], {"results": None, "meta": {}}, {"results": [], "meta": {}},
                                  {"results": [None], "meta": {"next_cursor": None}}])
def test_malformed_responses_raise(data):
    with pytest.raises(RuntimeError):
        searcher(response(data)).search_ssrn("law")


def test_json_and_network_errors_raise():
    bad = response()
    bad.json.side_effect = ValueError("json")
    for result in [bad, requests.Timeout("failed")]:
        with pytest.raises(RuntimeError):
            searcher(result).search_ssrn("law")


def test_page_scan_limit():
    search = searcher(payload([work()], "two"))
    search.SSRN_MAX_PAGES = 1
    with pytest.raises(RuntimeError, match="scan limit"):
        search.search_ssrn("law")


def test_success_empty_and_zero_limit():
    search = searcher(payload([]))
    assert search.search_ssrn("law", 0) == []
    search.session.get.assert_not_called()
    assert search.search_ssrn("law") == []


@pytest.mark.parametrize("limit", [-1, 1001, 1.5, True])
def test_limits_fail_before_network(limit):
    search = searcher()
    with pytest.raises(ValueError):
        search.search_ssrn("law", limit)
    search.session.get.assert_not_called()


def test_ssrn_connector_and_unified_search_route_via_openalex(monkeypatch):
    from paper_search_mcp import server
    mock = Mock(side_effect=RuntimeError("OpenAlex HTTP 429"))
    monkeypatch.setattr(OpenAlexSearcher, "search_ssrn", mock)
    with pytest.raises(RuntimeError):
        SSRNSearcher().search("law", 3)
    mock.assert_called_once_with("law", max_results=3)
    result = asyncio.run(server.search_papers("law", sources="ssrn"))
    assert result["errors"]["ssrn"] == "OpenAlex HTTP 429"


def paper_page(abstract_id="123", href="/sol3/Delivery.cfm?abstract_id=123"):
    return f'''<html><head><link rel="canonical" href="https://papers.ssrn.com/abstract={abstract_id}">
    <meta name="citation_title" content="Verified public research">
    <meta name="citation_pdf_url" content="{href}"></head></html>'''


def test_ssrn_download_checks_page_and_pdf_identity(tmp_path):
    search = SSRNSearcher()
    page = response(); page.text = paper_page()
    search.session.get = Mock(side_effect=[page, response(body=pdf_bytes())])
    path = search.download_pdf("ssrn:123", str(tmp_path))
    assert path.endswith("ssrn_123.pdf")
    assert all(c.kwargs["allow_redirects"] is False for c in search.session.get.call_args_list)


@pytest.mark.parametrize("html", [paper_page("other"), "<html>login</html>"])
def test_ssrn_wrong_or_missing_page_identity_fails(html):
    search = SSRNSearcher()
    page = response(); page.text = html
    search.session.get = Mock(return_value=page)
    with pytest.raises(RuntimeError, match="identity"):
        search.download_pdf("123")
    assert search.session.get.call_count == 1


@pytest.mark.parametrize("href", ["https://evil.test/other.pdf", "/sol3/Delivery.cfm?abstract_id=999"])
def test_ssrn_pdf_must_be_official_and_same_identity(href):
    search = SSRNSearcher()
    page = response(); page.text = paper_page(href=href)
    search.session.get = Mock(return_value=page)
    assert "No publicly accessible" in search.download_pdf("123")
    assert search.session.get.call_count == 1


def test_ssrn_public_download_never_uses_netrc(monkeypatch):
    search = SSRNSearcher()
    lookup = Mock(side_effect=AssertionError("Must not read .netrc"))
    monkeypatch.setattr(requests.sessions, "get_netrc_auth", lookup)
    prepared = search.session.prepare_request(requests.Request("GET", search.BASE_URL))
    assert "Authorization" not in prepared.headers
    lookup.assert_not_called()
