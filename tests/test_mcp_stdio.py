"""Exercise the installed MCP SDK over a real, network-free stdio session."""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from paper_search_mcp.mcp_compat import tool_input_schema
from tests.mcp_test_compat import annotation, is_error, server_name


def test_stdio_initializes_lists_tools_and_calls_without_credentials(tmp_path):
    async def exercise_session():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "paper_search_mcp.server", "--transport", "stdio"],
            cwd=tmp_path,
            # Do not read a developer's saved credentials or server settings.
            env={"PAPER_SEARCH_MCP_ENV_FILE": str(tmp_path / "absent.env")},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert server_name(initialized) == "paper_search_server"

                tools = (await session.list_tools()).tools
                names = {tool.name for tool in tools}
                assert {"search_papers", "search_arxiv", "download_with_fallback"} <= names
                assert all(tool_input_schema(tool).get("type") == "object" for tool in tools)
                assert all(
                    tool.annotations is not None
                    and annotation(tool, "openWorldHint") is True
                    for tool in tools
                )

                # An unknown source exercises tool dispatch and JSON serialization
                # without making a request to any academic provider.
                result = await session.call_tool(
                    "search_papers", {"query": "test", "sources": "not-a-source"}
                )
                assert not is_error(result)
                payload = json.loads(result.content[0].text)
                assert payload["sources_used"] == []
                assert payload["errors"] == {
                    "not-a-source": "Unknown or unavailable source.",
                    "sources": "No valid sources selected.",
                }

    asyncio.run(asyncio.wait_for(exercise_session(), timeout=30))
