import asyncio
import io
import json
import unittest
from contextlib import redirect_stdout

from paper_search_mcp import api, cli


class TestToolCli(unittest.TestCase):
    def test_build_parser_accepts_tool_command(self):
        parser = cli.build_parser()
        args = parser.parse_args(["tool", "search_papers", "query", "--max-results-per-source", "3"])
        self.assertEqual(args.command, "tool")
        self.assertEqual(args.tool_name, "search_papers")
        self.assertEqual(args.query, "query")
        self.assertEqual(args.max_results_per_source, 3)

    def test_cmd_tool_runs_shared_api_function(self):
        parser = cli.build_parser()
        args = parser.parse_args(["tool", "sources"])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exit_code = asyncio.run(cli.cmd_tool(args))
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(buffer.getvalue()), {"sources": api.ALL_SOURCES})


if __name__ == "__main__":
    unittest.main()
