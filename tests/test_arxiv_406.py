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
