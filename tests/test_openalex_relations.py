import asyncio
from concurrent.futures import Future
from unittest.mock import Mock, patch

import pytest
import requests

from paper_search_mcp import server
from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher
from paper_search_mcp.academic_platforms.openalex_relations import (
    OpenAlexRelationError, _Budget, normalize_identifier,
)


def response(data=None, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = data
    return result


def work(n=2, **fields):
    return {"id": f"https://openalex.org/W{n}", "title": f"Paper {n}", **fields}


def page(items, count=None):
    return response({"results": items, "meta": {"count": len(items) if count is None else count}})


def searcher_with(*pages):
    searcher = OpenAlexSearcher(api_key="")
    searcher.session.get = Mock(side_effect=[response(work(1)), *pages])
    return searcher


@pytest.mark.parametrize("given,expected", [
    ("W123", "W123"), (" w123 ", "W123"),
    ("https://openalex.org/w123", "W123"),
    ("http://doi.org/10.1234/ABC", "doi:10.1234/abc"),
    ("https://dx.doi.org/10.1234/a%3Fb%23c", "doi:10.1234/a?b#c"),
    ("DOI: 10.1234/x", "doi:10.1234/x"),
    ("10.1234/x", "doi:10.1234/x"),
])
def test_identifier_normalization(given, expected):
    assert normalize_identifier(given) == expected


@pytest.mark.parametrize("bad", ["", None, "Paper title", "10.1/no", "W1|W2", "W1,other:2",
    "https://evil.example/W1", "https://openalex.org/A1", "https://openalex.org/W1?x=y",
    "https://openalex.org/10.1234/no", "10.1234/white space", "https://openalex.org/W1#x"])
def test_invalid_identifiers_never_send_requests(bad):
    searcher = searcher_with()
    with pytest.raises(ValueError):
        searcher.get_references(bad)
    searcher.session.get.assert_not_called()


def test_search_populates_references_without_an_extra_request():
    searcher = OpenAlexSearcher(api_key="")
    searcher.session.get = Mock(return_value=page([
        work(referenced_works=["https://openalex.org/W5", "W6"], authorships=None,
             concepts=None, primary_location=None, open_access=None, doi=None),
    ]))
    paper = searcher.search("example", filter="publication_year:2026")[0]
    assert paper.references == ["W5", "W6"]
    assert paper.to_dict()["references"] == "W5; W6"
    assert paper.to_dict()["authors"] == ""
    assert paper.doi == ""
    searcher.session.get.assert_called_once()


@pytest.mark.parametrize("method,edge,direction", [
    ("get_citations", "cites", "citing"), ("get_references", "cited_by", "referenced"),
])
def test_one_hop_ranked_filter_and_empty_success(method, edge, direction):
    searcher = searcher_with(page([]))
    result = getattr(searcher, method)("10.1234/a?b#c", filter=" publication_year:2026 ")
    assert result == {
        "work_id": "W1", "direction": direction, "papers": [], "total": 0,
        "total_available": 0, "truncated": False, "stop_reason": "complete",
        "pages_fetched": 1, "requests_made": 2, "skipped_results": 0,
    }
    seed, lookup = searcher.session.get.call_args_list
    assert seed.args[0].endswith("/doi:10.1234%2Fa%3Fb%23c")
    assert lookup.kwargs["params"] == {
        "filter": f"{edge}:W1,publication_year:2026", "sort": "cited_by_count:desc",
        "per_page": 10, "page": 1,
    }
    assert all(call.kwargs["allow_redirects"] is False for call in searcher.session.get.call_args_list)


def test_pagination_fixed_page_size_no_skipped_records_on_last_page():
    searcher = searcher_with(page([work(n) for n in range(2, 102)], 250),
                            page([work(n) for n in range(102, 202)], 250))
    result = searcher.get_citations("W1", 150)
    assert result["total"] == 150
    assert result["papers"][-1]["paper_id"] == "W151"
    assert result["truncated"] and result["stop_reason"] == "max_results"
    assert result["pages_fetched"] == 2 and result["requests_made"] == 3
    assert [call.kwargs["params"]["per_page"] for call in searcher.session.get.call_args_list[1:]] == [100, 100]


def test_max_results_is_reported_even_when_last_page_has_the_remaining_records():
    searcher = searcher_with(page([work(n) for n in range(2, 102)], 200),
                            page([work(n) for n in range(102, 202)], 200))
    result = searcher.get_citations("W1", 150)
    assert result["total"] == 150 and result["truncated"]


@pytest.mark.parametrize("options,reason", [({"max_pages": 1}, "page_budget"),
                                             ({"max_requests": 2}, "request_budget")])
def test_page_and_request_budgets_return_explicit_partial_result(options, reason):
    searcher = searcher_with(page([work(n) for n in range(2, 102)], 1000))
    result = searcher.get_citations("W1", 500, **options)
    assert result["total"] == 100 and result["truncated"]
    assert result["stop_reason"] == reason
    assert searcher.session.get.call_count == 2


def test_duplicate_and_missing_title_records_are_reported_not_looped_forever():
    searcher = searcher_with(page([work(2), work(2), {"id": "W3"}], 3))
    result = searcher.get_references("W1")
    assert result["total"] == 1 and result["skipped_results"] == 2
    assert not result["truncated"]


@pytest.mark.parametrize("options", [{"max_results": 0}, {"max_results": 501},
    {"max_results": True}, {"max_pages": 6}, {"max_pages": 0}, {"max_requests": 9},
    {"max_requests": 1}, {"timeout_seconds": 0}, {"timeout_seconds": 61},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
    {"filter": None}])
def test_invalid_limits_fail_before_network(options):
    searcher = searcher_with()
    with pytest.raises(ValueError):
        searcher.get_citations("W1", **options)
    searcher.session.get.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_http_failures_are_not_empty_success_or_retried(status):
    searcher = OpenAlexSearcher(api_key="test-secret")
    searcher.session.get = Mock(return_value=response({"error": "test-secret"}, status))
    with pytest.raises(OpenAlexRelationError, match=f"HTTP {status}") as err:
        searcher.get_references("W1")
    assert "test-secret" not in str(err.value)
    assert searcher.session.headers["Authorization"] == "Bearer test-secret"
    searcher.session.get.assert_called_once()


@pytest.mark.parametrize("error", [requests.Timeout("secret"), requests.ConnectionError("secret")])
def test_transport_errors_are_safe_and_explicit(error):
    searcher = OpenAlexSearcher(api_key="")
    searcher.session.get = Mock(side_effect=error)
    with pytest.raises(OpenAlexRelationError, match="network/timeout") as err:
        searcher.get_references("W1")
    assert "secret" not in str(err.value)


@pytest.mark.parametrize("bad", [None, [], {}, {"results": None, "meta": {"count": 0}},
    {"results": [], "meta": None}, {"results": [], "meta": {"count": "0"}}])
def test_malformed_pages_are_explicit_errors(bad):
    searcher = searcher_with(response(bad))
    with pytest.raises(OpenAlexRelationError):
        searcher.get_references("W1")


def test_invalid_json_and_seed_id_are_errors():
    searcher = OpenAlexSearcher(api_key="")
    bad_json = response()
    bad_json.json.side_effect = ValueError("no JSON")
    searcher.session.get = Mock(return_value=bad_json)
    with pytest.raises(OpenAlexRelationError, match="invalid JSON"):
        searcher.get_references("W1")
    searcher.session.get = Mock(return_value=response({"id": "not a work"}))
    with pytest.raises(OpenAlexRelationError, match="valid work ID"):
        searcher.get_references("W1")


def test_later_page_error_does_not_return_partial_success():
    searcher = searcher_with(page([work(n) for n in range(2, 102)], 500), response(status=429))
    with pytest.raises(OpenAlexRelationError, match="429"):
        searcher.get_citations("W1", 200)
    assert searcher.session.get.call_count == 3


def test_safe_merged_id_redirect_consumes_request_budget():
    searcher = OpenAlexSearcher(api_key="")
    searcher.session.get = Mock(side_effect=[response(status=301, headers={"Location": "/works/W2"}),
                                            response(work(2)), page([])])
    result = searcher.get_citations("W1")
    assert result["work_id"] == "W2" and result["requests_made"] == 3
    assert searcher.session.get.call_args_list[1].args[0] == "https://api.openalex.org/works/W2"


@pytest.mark.parametrize("location", ["https://evil.example/works/W1", "/works/W1?api_key=secret", ""])
def test_unsafe_or_empty_redirect_is_rejected(location):
    searcher = OpenAlexSearcher(api_key="")
    searcher.session.get = Mock(return_value=response(status=301, headers={"Location": location}))
    with pytest.raises(OpenAlexRelationError, match="redirect|budget"):
        searcher.get_references("W1")
    assert searcher.session.get.call_count <= 8


def test_redirect_loop_cannot_escape_request_budget():
    searcher = OpenAlexSearcher(api_key="")
    searcher.session.get = Mock(return_value=response(status=301, headers={"Location": "/works/W1"}))
    with pytest.raises(OpenAlexRelationError, match="request budget"):
        searcher.get_references("W1", max_requests=3)
    assert searcher.session.get.call_count == 3


def test_deadline_stops_after_late_response_without_next_request():
    searcher = searcher_with(page([]))
    with patch("paper_search_mcp.academic_platforms.openalex_relations.time.monotonic",
               side_effect=[0, 0, 2]):
        with pytest.raises(OpenAlexRelationError, match="time budget"):
            searcher.get_references("W1", timeout_seconds=1)
    searcher.session.get.assert_called_once()
    assert searcher.session.get.call_args.kwargs["timeout"] == 1


def test_budget_refuses_expired_requests():
    with patch("paper_search_mcp.academic_platforms.openalex_relations.time.monotonic", side_effect=[0, 2]):
        budget = _Budget(2, 1)
        with pytest.raises(OpenAlexRelationError, match="time budget"):
            budget.request_json(Mock(), "https://api.openalex.org/works/W1")
    assert budget.requests_made == 0


@pytest.mark.parametrize("tool,method", [("get_citing_papers", "get_citations"),
                                         ("get_referenced_papers", "get_references")])
def test_mcp_tools_use_bounded_executor_and_forward_options(tool, method):
    expected = {"papers": [], "truncated": False}
    with patch.object(server.openalex_searcher, method, return_value=expected) as lookup:
        assert asyncio.run(getattr(server, tool)("W1", 2, "publication_year:2026", 1, 2, 1)) == expected
    lookup.assert_called_once_with("W1", max_results=2, filter="publication_year:2026",
                                   max_pages=1, max_requests=2, timeout_seconds=1)


def test_mcp_wall_clock_timeout_does_not_wait_for_worker():
    future = Future()
    future.set_running_or_notify_cancel()
    with patch.object(server._SEARCH_EXECUTOR, "submit", return_value=future):
        with pytest.raises(TimeoutError, match="time budget"):
            asyncio.run(server.get_citing_papers("W1", timeout_seconds=0.01))
    future.set_result({"papers": []})
