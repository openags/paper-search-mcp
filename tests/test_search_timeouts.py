import asyncio
from unittest.mock import AsyncMock, patch

from paper_search_mcp import server


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
    with patch.object(server, "async_search", AsyncMock(return_value=expected)):
        result = asyncio.run(
            server.search_google_scholar("machine learning", max_results=5)
        )

    assert result == expected


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
