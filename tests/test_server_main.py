import sys
import unittest
from unittest.mock import patch

from paper_search_mcp import server


class TestServerMain(unittest.TestCase):
    def setUp(self):
        self.original_host = server.mcp.settings.host
        self.original_port = server.mcp.settings.port
        self.original_path = server.mcp.settings.streamable_http_path

    def tearDown(self):
        server.mcp.settings.host = self.original_host
        server.mcp.settings.port = self.original_port
        server.mcp.settings.streamable_http_path = self.original_path

    def test_main_defaults_to_stdio(self):
        with patch.object(server.mcp, "run") as mock_run:
            with patch.object(sys, "argv", ["paper-search-mcp"]):
                server.main()

        mock_run.assert_called_once_with(transport="stdio")
        self.assertEqual(server.mcp.settings.host, "127.0.0.1")
        self.assertEqual(server.mcp.settings.port, 8000)
        self.assertEqual(server.mcp.settings.streamable_http_path, "/mcp")

    def test_main_accepts_transport_and_network_options(self):
        with patch.object(server.mcp, "run") as mock_run:
            with patch.object(
                sys,
                "argv",
                [
                    "paper-search-mcp",
                    "--transport",
                    "streamable-http",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9000",
                    "--path",
                    "/gateway/mcp",
                ],
            ):
                server.main()

        mock_run.assert_called_once_with(transport="streamable-http")
        self.assertEqual(server.mcp.settings.host, "0.0.0.0")
        self.assertEqual(server.mcp.settings.port, 9000)
        self.assertEqual(server.mcp.settings.streamable_http_path, "/gateway/mcp")


if __name__ == "__main__":
    unittest.main()
