import asyncio
import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from paper_search_mcp import server
from paper_search_mcp.academic_platforms.iacr import IACRSearcher
from paper_search_mcp.academic_platforms.pubmed import PubMedSearcher


def test_search_papers_returns_partial_results_when_one_source_times_out():
    async def slow_search(*args, **kwargs):
        await asyncio.sleep(0.05)
        return [{"title": "slow", "paper_id": "slow-1"}]

    fast_result = [{"title": "fast", "paper_id": "fast-1"}]
    with (
        patch.object(server, "SEARCH_PAPERS_SOURCE_TIMEOUT_SECONDS", 0.01),
        patch.object(server, "search_arxiv", AsyncMock(side_effect=slow_search)),
        patch.object(server, "search_pubmed", AsyncMock(return_value=fast_result)),
    ):
        result = asyncio.run(
            server.search_papers(
                "test query",
                max_results_per_source=2,
                sources="arxiv,pubmed",
            )
        )

    assert result["source_results"] == {"arxiv": 0, "pubmed": 1}
    assert "timed out" in result["errors"]["arxiv"]
    assert result["total"] == 1
    assert result["papers"][0]["paper_id"] == "fast-1"


def test_search_papers_keeps_normal_source_results():
    result_row = [{"title": "paper a", "paper_id": "a"}]
    with patch.object(server, "search_arxiv", AsyncMock(return_value=result_row)):
        result = asyncio.run(
            server.search_papers(
                "test query",
                max_results_per_source=1,
                sources="arxiv",
            )
        )

    assert result["source_results"] == {"arxiv": 1}
    assert result["errors"] == {}
    assert result["papers"] == result_row


def test_google_scholar_tool_returns_results_before_timeout():
    expected = [{"paper_id": "1", "title": "paper"}]
    with patch.object(
        server, "async_search", AsyncMock(return_value=expected)
    ) as search:
        result = asyncio.run(
            server.search_google_scholar("machine learning", max_results=5)
        )

    assert result == expected
    search.assert_awaited_once_with(
        server.google_scholar_searcher,
        "machine learning",
        5,
        timeout_seconds=server.GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS - 1.0,
    )


def test_google_scholar_tool_returns_empty_list_on_timeout():
    async def slow_search(*args, **kwargs):
        await asyncio.sleep(0.05)
        return [{"paper_id": "1", "title": "paper"}]

    with (
        patch.object(server, "GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS", 0.01),
        patch.object(server, "async_search", AsyncMock(side_effect=slow_search)),
    ):
        result = asyncio.run(
            server.search_google_scholar("machine learning", max_results=5)
        )

    assert result == []


def test_timed_out_blocking_search_keeps_capacity_until_worker_exits():
    started = Event()
    release = Event()
    finished = Event()
    executor = server._BoundedSearchExecutor(max_workers=1)

    class BlockingSearcher:
        def search(self, query, max_results):
            started.set()
            release.wait(timeout=2)
            finished.set()
            return [SimpleNamespace(to_dict=lambda: {"paper_id": "slow"})]

    class FastSearcher:
        @staticmethod
        def search(query, max_results):
            return [SimpleNamespace(to_dict=lambda: {"paper_id": "fast"})]

    try:
        with patch.object(server, "_SEARCH_EXECUTOR", executor):
            with pytest.raises(TimeoutError, match="timed out"):
                asyncio.run(
                    server._run_search_with_timeout(
                        "blocking",
                        server.async_search(BlockingSearcher(), "test", 1),
                        timeout_seconds=0.01,
                    )
                )

            assert started.wait(timeout=1)
            with pytest.raises(server.SearchExecutorSaturatedError):
                asyncio.run(server.async_search(FastSearcher(), "test", 1))

            release.set()
            assert finished.wait(timeout=1)

            deadline = time.monotonic() + 1
            while True:
                try:
                    result = asyncio.run(server.async_search(FastSearcher(), "test", 1))
                    break
                except server.SearchExecutorSaturatedError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)

            assert result == [{"paper_id": "fast"}]
    finally:
        release.set()
        executor.shutdown()


def test_pubmed_search_uses_bounded_request_timeout():
    response = Mock(content=b"<eSearchResult><IdList /></eSearchResult>")
    with patch(
        "paper_search_mcp.academic_platforms.pubmed.requests.get",
        return_value=response,
    ) as request:
        assert PubMedSearcher().search("test") == []

    assert request.call_args.kwargs["timeout"] == 30


def test_iacr_search_uses_bounded_request_timeout():
    searcher = IACRSearcher()
    response = Mock(status_code=503)
    searcher.session.get = Mock(return_value=response)

    assert searcher.search("test", fetch_details=False) == []
    assert searcher.session.get.call_args.kwargs["timeout"] == 30
