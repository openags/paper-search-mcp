"""Offline source-error regressions for Google Scholar issue #74."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from paper_search_mcp import cli, server
from paper_search_mcp.academic_platforms import google_scholar
from paper_search_mcp.academic_platforms.google_scholar import (
    GoogleScholarSearchError,
    GoogleScholarSearcher,
)


RESULT_HTML = """
<html><body><div class="gs_ri">
  <h3 class="gs_rt"><a href="https://example.test/paper">Test Paper</a></h3>
  <div class="gs_a">Ada Lovelace - Journal, 2024</div>
  <div class="gs_rs">Abstract</div>
</div></body></html>
"""
EMPTY_HTML = "<html><body>Your search did not match any articles.</body></html>"
CONSENT_HTML = "<html><body>Before you continue to Google Scholar</body></html>"


def response(status=200, text=EMPTY_HTML, headers=None):
    return SimpleNamespace(status_code=status, text=text, headers=headers or {})


@pytest.fixture
def searcher(monkeypatch):
    # Advance a simulated clock: all pacing/backoff/deadline tests stay offline
    # and do not rely on wall-clock sleeps or patch another thread's clock.
    clock = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(delay):
        clock.sleeps.append(delay)
        clock.now += delay

    monkeypatch.setattr(
        google_scholar, "time",
        SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep),
    )
    monkeypatch.setattr(google_scholar.random, "uniform", lambda low, high: low)
    searcher = GoogleScholarSearcher()
    searcher.session.get = Mock(return_value=response())
    searcher.test_clock = clock
    return searcher


@pytest.mark.parametrize("status", [403, 429, 503])
def test_retry_exhaustion_raises_source_error(searcher, status):
    searcher.session.get.return_value = response(status)

    with pytest.raises(GoogleScholarSearchError, match=f"HTTP {status}"):
        searcher.search("test")

    assert searcher.session.get.call_count == searcher.max_retries
    # Pacing before each attempt; backoff only when another retry remains.
    assert searcher.test_clock.sleeps == [1.0, 2.0, 1.0, 4.0, 1.0]


@pytest.mark.parametrize("status", [400, 404, 500, 502])
def test_nonretryable_http_error_fails_without_extra_requests(searcher, status):
    searcher.session.get.return_value = response(status)

    with pytest.raises(GoogleScholarSearchError, match=f"HTTP {status}"):
        searcher.search("test")

    assert searcher.session.get.call_count == 1


@pytest.mark.parametrize("text,expected", [(EMPTY_HTML, []), (RESULT_HTML, ["Test Paper"])])
def test_retry_can_recover_to_genuine_empty_or_nonempty_results(searcher, text, expected):
    searcher.session.get.side_effect = [response(429), response(text=text)]

    papers = searcher.search("test", max_results=1)

    assert [paper.title for paper in papers] == expected
    assert searcher.session.get.call_count == 2


def test_genuine_empty_result_is_still_successful(searcher):
    assert searcher.search("no such paper") == []
    assert searcher.session.get.call_count == 1


@pytest.mark.parametrize("text", [
    '<form id="gs_captcha_f"></form>',
    '<form><input name="captcha"></form>',
    "Please show you're not a robot",
    "Unusual traffic from your computer network",
])
def test_captcha_is_reported_without_retry_or_solving(searcher, text):
    searcher.session.get.return_value = response(text=text)

    with pytest.raises(GoogleScholarSearchError, match="bot-detection/captcha"):
        searcher.search("test")

    assert searcher.session.get.call_count == 1


def test_persistent_consent_is_reported_after_one_retry(searcher):
    searcher.session.get.return_value = response(text=CONSENT_HTML)

    with pytest.raises(GoogleScholarSearchError, match="consent page after retry"):
        searcher.search("test")

    assert searcher.session.get.call_count == 2


def test_consent_retry_can_recover(searcher):
    searcher.session.get.side_effect = [response(text=CONSENT_HTML), response(text=RESULT_HTML)]
    assert [p.title for p in searcher.search("test", max_results=1)] == ["Test Paper"]
    assert searcher.session.get.call_count == 2


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError, requests.exceptions.ProxyError])
def test_network_failure_is_observable_without_exposing_request_details(searcher, error):
    searcher.session.get.side_effect = error("https://user:secret@proxy.example")

    with pytest.raises(GoogleScholarSearchError, match="request failed") as caught:
        searcher.search("test")

    assert "secret" not in str(caught.value)
    assert searcher.session.get.call_count == 1


def test_network_failure_after_429_is_not_false_empty_success(searcher):
    searcher.session.get.side_effect = [response(429), requests.ConnectionError("offline")]

    with pytest.raises(GoogleScholarSearchError, match="request failed"):
        searcher.search("test")

    assert searcher.session.get.call_count == 2


def test_deadline_during_rate_limit_backoff_preserves_source_error(searcher):
    searcher.session.get.return_value = response(429)

    with pytest.raises(GoogleScholarSearchError, match="HTTP 429"):
        searcher.search("test", timeout_seconds=2.0)

    assert searcher.session.get.call_count == 1
    assert searcher.test_clock.now == 2.0
    assert searcher.session.get.call_args.kwargs["timeout"] == 1.0


def test_failure_on_later_page_is_not_returned_as_complete_search(searcher):
    searcher.session.get.side_effect = [response(text=RESULT_HTML), response(text="<input name='captcha'>")]

    with pytest.raises(GoogleScholarSearchError, match="captcha"):
        searcher.search("test", max_results=2)

    assert searcher.session.get.call_count == 2


def test_expired_deadline_keeps_existing_no_request_contract(searcher):
    assert searcher.search("test", timeout_seconds=0.0) == []
    searcher.session.get.assert_not_called()


def test_nonpositive_limit_does_not_request(searcher):
    assert searcher.search("test", max_results=0) == []
    searcher.session.get.assert_not_called()


def test_direct_mcp_scholar_tool_surfaces_source_error(searcher, monkeypatch):
    searcher.session.get.return_value = response(429)
    monkeypatch.setattr(server, "google_scholar_searcher", searcher)

    with pytest.raises(GoogleScholarSearchError, match="HTTP 429"):
        asyncio.run(server.search_google_scholar("test"))


def test_unified_mcp_keeps_other_sources_and_reports_scholar_error(searcher, monkeypatch):
    searcher.session.get.return_value = response(text="<input name='captcha'>")
    monkeypatch.setattr(server, "google_scholar_searcher", searcher)
    good_papers = [{"paper_id": "good", "title": "Good paper", "source": "arxiv"}]
    monkeypatch.setattr(server, "search_arxiv", AsyncMock(return_value=good_papers))

    result = asyncio.run(server.search_papers("test", sources="google_scholar,arxiv"))

    assert "captcha" in result["errors"]["google_scholar"]
    assert result["source_results"] == {"google_scholar": 0, "arxiv": 1}
    assert result["papers"] == good_papers
    assert result["total"] == 1


def test_cli_reports_scholar_error_without_corrupting_json(searcher, monkeypatch, capsys):
    searcher.session.get.return_value = response(429)
    good_paper = {"paper_id": "good", "title": "Good paper", "source": "arxiv"}
    good_searcher = Mock()
    good_searcher.search.return_value = [Mock(to_dict=Mock(return_value=good_paper))]
    monkeypatch.setattr(cli, "SEARCHERS", {"google_scholar": searcher, "arxiv": good_searcher})
    args = cli.build_parser().parse_args(["search", "test", "--sources", "google_scholar,arxiv"])

    assert asyncio.run(cli.cmd_search(args)) == 0
    result = json.loads(capsys.readouterr().out)

    assert "HTTP 429" in result["errors"]["google_scholar"]
    assert result["source_results"] == {"google_scholar": 0, "arxiv": 1}
    assert result["papers"] == [good_paper]
    assert result["total"] == 1


def test_live_smoke_test_skips_only_expected_upstream_failure():
    from tests import test_google_scholar as live_tests

    smoke = live_tests.TestGoogleScholarSearcher(methodName="test_search")
    smoke.scholar_accessible = True
    smoke.searcher = Mock()
    smoke.searcher.search.side_effect = GoogleScholarSearchError("HTTP 429")

    with pytest.raises(unittest.SkipTest, match="Google Scholar is unavailable"):
        smoke.test_search()

    smoke.searcher.search.side_effect = AssertionError("Unexpected regression")
    with pytest.raises(AssertionError, match="Unexpected regression"):
        smoke.test_search()


@pytest.mark.parametrize("status", [403, 429, 503])
def test_exhausted_backoff_blocks_following_queries_without_requests(searcher, status):
    searcher.session.get.return_value = response(status)
    with pytest.raises(GoogleScholarSearchError, match=f"HTTP {status}"):
        searcher.search("first")
    calls = searcher.session.get.call_count
    sleeps = list(searcher.test_clock.sleeps)
    with pytest.raises(GoogleScholarSearchError, match="cooling down.*Retry after 60 seconds"):
        searcher.search("different query")
    assert searcher.session.get.call_count == calls
    assert searcher.test_clock.sleeps == sleeps


@pytest.mark.parametrize("status", [200, 403, 429, 503])
def test_captcha_cooldown_never_retries_or_rotates_identity(searcher, status):
    original_ua = searcher.session.headers["User-Agent"]
    searcher.session.get.return_value = response(status, text="<input name='captcha'>")
    with pytest.raises(GoogleScholarSearchError, match="captcha"):
        searcher.search("first")
    with pytest.raises(GoogleScholarSearchError, match="cooling down"):
        searcher.search("second")
    assert searcher.session.get.call_count == 1
    assert searcher.session.headers["User-Agent"] == original_ua


def test_cooldown_expires_and_success_resets_exponential_streak(searcher):
    searcher.max_retries = 1
    searcher.session.get.return_value = response(429)
    for expected in (60, 120, 240, 480, 900, 900):
        with pytest.raises(GoogleScholarSearchError, match=f"Retry after {expected} seconds"):
            searcher.search("blocked")
        searcher.test_clock.now += expected
    searcher.session.get.return_value = response(text=RESULT_HTML)
    assert len(searcher.search("recovered", max_results=1)) == 1
    assert searcher._consecutive_blocks == 0
    searcher.session.get.return_value = response(429)
    with pytest.raises(GoogleScholarSearchError, match="Retry after 60 seconds"):
        searcher.search("blocked again")


def test_retry_after_seconds_paces_retry_without_rotating_identity(searcher):
    original_ua = searcher.session.headers["User-Agent"]
    searcher.session.get.side_effect = [response(429, headers={"Retry-After": "7"}), response()]
    assert searcher.search("retry") == []
    assert searcher.test_clock.sleeps == [1.0, 7.0, 1.0]
    assert searcher.session.headers["User-Agent"] == original_ua


def test_retry_after_http_date_is_honored(searcher, monkeypatch):
    from datetime import datetime, timezone
    from email.utils import format_datetime
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(google_scholar, "datetime", FixedDateTime)
    retry_at = datetime(2026, 10, 2, 0, 2, tzinfo=timezone.utc)
    searcher.max_retries = 1
    searcher.session.get.return_value = response(503, headers={"Retry-After": format_datetime(retry_at)})
    with pytest.raises(GoogleScholarSearchError, match="Retry after 120 seconds"):
        searcher.search("retry")


@pytest.mark.parametrize("header,expected", [
    ("", 0), ("garbage", 0), ("-1", 0), ("nan", 0), ("inf", 0),
    ("1.5", 0), ("99999999999999999", float("inf")), ("86401", float("inf")),
    ("Wed, 21 Oct 2015 07:28:00 GMT", 0), (None, 0), (" 7 ", 7),
])
def test_retry_after_untrusted_values_are_bounded(header, expected):
    assert GoogleScholarSearcher._retry_after(response(429, headers={"Retry-After": header})) == expected


def test_retry_after_beyond_deadline_carries_over_to_next_search(searcher):
    searcher.session.get.return_value = response(429, headers={"Retry-After": "120"})
    with pytest.raises(GoogleScholarSearchError, match="HTTP 429"):
        searcher.search("first", timeout_seconds=3)
    assert searcher.test_clock.now == 1  # A long Retry-After fails fast.
    with pytest.raises(GoogleScholarSearchError, match="cooling down"):
        searcher.search("second", timeout_seconds=3)
    assert searcher.session.get.call_count == 1


def test_cooldown_survives_network_error_during_retry(searcher):
    searcher.session.get.side_effect = [response(429), requests.ConnectionError("private")]
    with pytest.raises(GoogleScholarSearchError, match="request failed"):
        searcher.search("first")
    with pytest.raises(GoogleScholarSearchError, match="cooling down"):
        searcher.search("second")
    assert searcher.session.get.call_count == 2


def test_deadline_during_pacing_is_not_empty_success(searcher):
    with pytest.raises(GoogleScholarSearchError, match="timed out"):
        searcher.search("query", timeout_seconds=0.5)
    searcher.session.get.assert_not_called()


def test_deadline_after_first_page_is_not_partial_success(searcher):
    searcher.session.get.return_value = response(text=RESULT_HTML)
    with pytest.raises(GoogleScholarSearchError, match="timed out"):
        searcher.search("query", max_results=2, timeout_seconds=1.5)
    assert searcher.session.get.call_count == 1


def test_wait_for_busy_session_is_bounded_and_never_issues_request(searcher):
    searcher._search_lock.acquire()
    try:
        with pytest.raises(GoogleScholarSearchError, match="timed out waiting"):
            searcher.search("query", timeout_seconds=0.01)
    finally:
        searcher._search_lock.release()
    searcher.session.get.assert_not_called()


def test_concurrent_calls_share_session_and_cooldown(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered, release = Event(), Event()
    searcher = GoogleScholarSearcher(max_retries=1)
    monkeypatch.setattr(searcher, "_sleep_with_deadline", lambda delay, deadline: True)
    def blocked_request(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return response(429)
    searcher.session.get = Mock(side_effect=blocked_request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(searcher.search, "first", timeout_seconds=5)
        assert entered.wait(timeout=2)
        second = pool.submit(searcher.search, "second", timeout_seconds=5)
        release.set()
        with pytest.raises(GoogleScholarSearchError, match="HTTP 429"):
            first.result(timeout=3)
        with pytest.raises(GoogleScholarSearchError, match="cooling down"):
            second.result(timeout=3)
    assert searcher.session.get.call_count == 1


def test_unified_search_preserves_public_alternatives_during_cooldown(searcher, monkeypatch):
    searcher._begin_cooldown()
    monkeypatch.setattr(server, "google_scholar_searcher", searcher)
    papers = [{"paper_id": "W1", "title": "Alternative", "source": "openalex"}]
    monkeypatch.setattr(server, "search_openalex", AsyncMock(return_value=papers))
    result = asyncio.run(server.search_papers("query", sources="google_scholar,openalex"))
    assert "cooling down" in result["errors"]["google_scholar"]
    assert result["papers"] == papers
    assert result["source_results"] == {"google_scholar": 0, "openalex": 1}
    searcher.session.get.assert_not_called()


def test_long_valid_retry_after_never_retries_early_at_local_horizon(searcher):
    searcher.session.get.return_value = response(429, headers={"Retry-After": "172800"})
    with pytest.raises(GoogleScholarSearchError, match="automatic requests are paused"):
        searcher.search("first")
    assert searcher.test_clock.sleeps == [1.0]
    for elapsed in (86401, 172801):
        searcher.test_clock.now += elapsed
        with pytest.raises(GoogleScholarSearchError, match="automatic requests are paused"):
            searcher.search("next")
    assert searcher.session.get.call_count == 1


def test_long_supported_retry_after_extends_exponential_cooldown_without_sleep(searcher):
    searcher.session.get.return_value = response(429, headers={"Retry-After": "3600"})
    with pytest.raises(GoogleScholarSearchError, match="Retry after 3600 seconds"):
        searcher.search("first")
    assert searcher.test_clock.sleeps == [1.0]
    searcher.test_clock.now += 901
    with pytest.raises(GoogleScholarSearchError, match="cooling down"):
        searcher.search("next")
    assert searcher.session.get.call_count == 1


def test_captcha_returned_after_deadline_still_starts_cooldown(searcher):
    def late_response(*args, **kwargs):
        searcher.test_clock.now += 2
        return response(text="<input name='captcha'>")
    searcher.session.get.side_effect = late_response
    with pytest.raises(GoogleScholarSearchError, match="captcha"):
        searcher.search("first", timeout_seconds=2)
    with pytest.raises(GoogleScholarSearchError, match="cooling down"):
        searcher.search("second")
    assert searcher.session.get.call_count == 1
