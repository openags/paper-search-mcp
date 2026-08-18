"""MCP bootstrap for the shared paper search API."""

from mcp.server.fastmcp import FastMCP

from .api import TOOLS


mcp = FastMCP("paper_search_server")

for tool in TOOLS:
    mcp.tool()(tool)


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
