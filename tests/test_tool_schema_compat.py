import asyncio

from paper_search_mcp import server


def _contains_anyof_with_null(schema):
    if isinstance(schema, dict):
        any_of = schema.get("anyOf")
        if isinstance(any_of, list):
            for entry in any_of:
                if isinstance(entry, dict) and entry.get("type") == "null":
                    return True
        return any(_contains_anyof_with_null(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_contains_anyof_with_null(item) for item in schema)
    return False


def test_tool_input_schemas_avoid_nullable_anyof():
    tools = asyncio.run(server.mcp.list_tools())
    for tool in tools:
        assert not _contains_anyof_with_null(tool.inputSchema), tool.name
