import asyncio
import json
import os
import subprocess
import sys
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


def test_all_tools_declare_open_world_annotations():
    tools = asyncio.run(server.mcp.list_tools())

    missing = [
        tool.name
        for tool in tools
        if tool.annotations is None or tool.annotations.openWorldHint is not True
    ]

    assert missing == []


def test_search_and_lookup_tools_are_annotated_read_only():
    tools = asyncio.run(server.mcp.list_tools())
    read_only_tools = [
        tool
        for tool in tools
        if tool.name.startswith(("search_", "get_"))
    ]

    assert read_only_tools
    assert [
        tool.name
        for tool in read_only_tools
        if tool.annotations is None or tool.annotations.readOnlyHint is not True
    ] == []


def test_file_writing_tools_disclose_destructive_local_updates():
    tools = asyncio.run(server.mcp.list_tools())
    side_effecting_read_tools = {
        "read_arxiv_paper",
        "read_biorxiv_paper",
        "read_citeseerx_paper",
        "read_doaj_paper",
        "read_hal_paper",
        "read_iacr_paper",
        "read_medrxiv_paper",
        "read_semantic_paper",
        "read_ssrn_paper",
        "read_zenodo_paper",
    }
    tool_names = {tool.name for tool in tools}
    file_tools = [
        tool
        for tool in tools
        if tool.name.startswith("download_")
        or tool.name in side_effecting_read_tools
    ]

    assert side_effecting_read_tools <= tool_names
    assert file_tools
    assert [
        tool.name
        for tool in file_tools
        if tool.annotations is None
        or tool.annotations.readOnlyHint is not False
        or tool.annotations.destructiveHint is not True
    ] == []


def test_non_writing_read_tools_are_annotated_read_only():
    tools = asyncio.run(server.mcp.list_tools())
    read_only_read_tools = [
        tool
        for tool in tools
        if tool.name.startswith("read_")
        and tool.annotations is not None
        and tool.annotations.readOnlyHint is True
    ]

    assert {tool.name for tool in read_only_read_tools} == {
        "read_base_paper",
        "read_crossref_paper",
        "read_dblp_paper",
        "read_openalex_paper",
        "read_openaire_paper",
        "read_pubmed_paper",
    }


def test_optional_ieee_and_acm_tools_publish_annotations():
    env = os.environ.copy()
    env.update(
        {
            "PAPER_SEARCH_MCP_IEEE_API_KEY": "test-key",
            "PAPER_SEARCH_MCP_ACM_API_KEY": "test-key",
        }
    )
    script = """
import asyncio
import json
from paper_search_mcp import server

names = {
    "search_ieee", "download_ieee", "read_ieee_paper",
    "search_acm", "download_acm", "read_acm_paper",
}
tools = asyncio.run(server.mcp.list_tools())
print(json.dumps({
    tool.name: tool.annotations.model_dump()
    for tool in tools
    if tool.name in names
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=True,
        env=env,
        text=True,
    )
    annotations = json.loads(completed.stdout.strip().splitlines()[-1])

    assert set(annotations) == {
        "search_ieee",
        "download_ieee",
        "read_ieee_paper",
        "search_acm",
        "download_acm",
        "read_acm_paper",
    }
    for name in ("search_ieee", "read_ieee_paper", "search_acm", "read_acm_paper"):
        assert annotations[name]["readOnlyHint"] is True
        assert annotations[name]["openWorldHint"] is True
    for name in ("download_ieee", "download_acm"):
        assert annotations[name]["readOnlyHint"] is False
        assert annotations[name]["destructiveHint"] is True
        assert annotations[name]["openWorldHint"] is True


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
