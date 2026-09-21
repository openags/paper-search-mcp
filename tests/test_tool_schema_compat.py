import asyncio
from unittest.mock import AsyncMock, patch

from paper_search_mcp import server


def _contains_nullable_union(schema):
    if isinstance(schema, dict):
        any_of = schema.get("anyOf")
        if isinstance(any_of, list) and any(
            isinstance(entry, dict) and entry.get("type") == "null" for entry in any_of
        ):
            return True
        return any(_contains_nullable_union(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_contains_nullable_union(item) for item in schema)
    return False


def test_tool_input_schemas_do_not_expose_nullable_unions():
    tools = asyncio.run(server.mcp.list_tools())

    nullable_tools = [
        tool.name for tool in tools if _contains_nullable_union(tool.inputSchema)
    ]

    assert nullable_tools == []


def test_empty_string_preserves_optional_semantic_year_behavior():
    with patch.object(server, "async_search", new=AsyncMock(return_value=[])) as search:
        asyncio.run(server.search_semantic("test", year="", max_results=3))

    search.assert_awaited_once_with(server.semantic_searcher, "test", 3)


def test_empty_strings_preserve_optional_crossref_behavior():
    with patch.object(server, "async_search", new=AsyncMock(return_value=[])) as search:
        asyncio.run(server.search_crossref("test", max_results=3))

    search.assert_awaited_once_with(server.crossref_searcher, "test", 3)


def test_nonempty_crossref_options_are_forwarded():
    with patch.object(server, "async_search", new=AsyncMock(return_value=[])) as search:
        asyncio.run(
            server.search_crossref(
                "test",
                max_results=3,
                filter="from-pub-date:2020",
                sort="published",
                order="desc",
            )
        )

    search.assert_awaited_once_with(
        server.crossref_searcher,
        "test",
        3,
        filter="from-pub-date:2020",
        sort="published",
        order="desc",
    )
