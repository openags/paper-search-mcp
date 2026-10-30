import contextlib
import io
import os
import unittest
from unittest.mock import patch

from paper_search_mcp import server


class TestServerTransport(unittest.TestCase):
    ENV_KEYS = (
        "PAPER_SEARCH_MCP_TRANSPORT",
        "PAPER_SEARCH_MCP_HOST",
        "PAPER_SEARCH_MCP_PORT",
        "PAPER_SEARCH_MCP_PATH",
        "PAPER_SEARCH_TRANSPORT",
        "PAPER_SEARCH_HOST",
        "PAPER_SEARCH_PORT",
        "PAPER_SEARCH_PATH",
    )

    def setUp(self):
        self.saved_env = {key: os.environ.get(key) for key in self.ENV_KEYS}
        for key in self.ENV_KEYS:
            os.environ.pop(key, None)
        self.saved_settings = (
            server.mcp.settings.host,
            server.mcp.settings.port,
            server.mcp.settings.streamable_http_path,
        )

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        (
            server.mcp.settings.host,
            server.mcp.settings.port,
            server.mcp.settings.streamable_http_path,
        ) = self.saved_settings

    def test_stdio_is_default_and_starts_orphan_watchdog(self):
        with (
            patch.object(server.mcp, "run") as run,
            patch.object(server.threading, "Thread") as thread,
        ):
            server.main([])

        run.assert_called_once_with(transport="stdio")
        thread.assert_called_once_with(
            target=server._exit_when_orphaned,
            name="orphan-watchdog",
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()

    def test_cli_configures_streamable_http_and_skips_watchdog(self):
        with (
            patch.object(server.mcp, "run") as run,
            patch.object(server.threading, "Thread") as thread,
        ):
            server.main(
                [
                    "--transport",
                    "streamable-http",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9010",
                    "--path",
                    "/papers",
                ]
            )

        run.assert_called_once_with(transport="streamable-http")
        thread.assert_not_called()
        self.assertEqual(server.mcp.settings.host, "0.0.0.0")
        self.assertEqual(server.mcp.settings.port, 9010)
        self.assertEqual(server.mcp.settings.streamable_http_path, "/papers")

    def test_prefixed_environment_configures_network_transport(self):
        os.environ.update(
            {
                "PAPER_SEARCH_MCP_TRANSPORT": "sse",
                "PAPER_SEARCH_MCP_HOST": "127.0.0.2",
                "PAPER_SEARCH_MCP_PORT": "9011",
            }
        )
        with patch.object(server.mcp, "run") as run:
            server.main([])

        run.assert_called_once_with(transport="sse")
        self.assertEqual(server.mcp.settings.host, "127.0.0.2")
        self.assertEqual(server.mcp.settings.port, 9011)

    def test_cli_overrides_legacy_environment_aliases(self):
        os.environ.update(
            {
                "PAPER_SEARCH_TRANSPORT": "sse",
                "PAPER_SEARCH_HOST": "127.0.0.2",
                "PAPER_SEARCH_PORT": "9011",
            }
        )
        with patch.object(server.mcp, "run") as run:
            server.main(
                [
                    "--transport",
                    "streamable-http",
                    "--host",
                    "127.0.0.3",
                    "--port",
                    "9012",
                ]
            )

        run.assert_called_once_with(transport="streamable-http")
        self.assertEqual(server.mcp.settings.host, "127.0.0.3")
        self.assertEqual(server.mcp.settings.port, 9012)

    def test_invalid_network_options_fail_before_server_start(self):
        invalid_options = (
            ["--transport", "websocket"],
            ["--port", "0"],
            ["--port", "65536"],
            ["--port", "not-a-number"],
            ["--path", "missing-leading-slash"],
        )
        for options in invalid_options:
            with self.subTest(options=options):
                with (
                    patch.object(server.mcp, "run") as run,
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit),
                ):
                    server.main(options)
                run.assert_not_called()

    def test_orphan_watchdog_exits_after_parent_changes(self):
        with (
            patch.object(server.os, "name", "posix"),
            patch.object(server.os, "getppid", side_effect=[41, 41, 99]),
            patch.object(server.time, "sleep") as sleep,
            patch.object(server.os, "_exit") as exit_process,
        ):
            server._exit_when_orphaned(poll_seconds=0.25)

        self.assertEqual(sleep.call_count, 2)
        exit_process.assert_called_once_with(0)

    def test_windows_watchdog_waits_for_original_parent_handle(self):
        with (
            patch.object(server.os, "name", "nt"),
            patch.object(server.os, "getppid", return_value=41),
            patch.object(
                server, "_wait_for_windows_process_exit", return_value=True
            ) as wait_for_parent,
            patch.object(server.os, "_exit") as exit_process,
        ):
            server._exit_when_orphaned()

        wait_for_parent.assert_called_once_with(41)
        exit_process.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
