import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_search_mcp import config


class TestConfigEnv(unittest.TestCase):
    def test_prefixed_env_has_priority_over_legacy(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "PAPER_SEARCH_MCP_CORE_API_KEY": "prefixed-value",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", ""), "prefixed-value")

    def test_legacy_env_fallback_still_works(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", ""), "legacy-value")

    def test_empty_prefixed_value_blocks_legacy_fallback(self):
        with patch.dict(
            os.environ,
            {
                "PAPER_SEARCH_MCP_ENV_FILE": "/tmp/paper-search-mcp-missing.env",
                "PAPER_SEARCH_MCP_CORE_API_KEY": "",
                "CORE_API_KEY": "legacy-value",
            },
            clear=True,
        ):
            self.assertEqual(config.get_env("CORE_API_KEY", "default"), "")

    def test_loads_from_custom_env_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / "test.env"
            env_path.write_text(
                "PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=test@example.com\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": str(env_path),
                },
                clear=True,
            ):
                config.load_env_file(force=True)
                self.assertEqual(config.get_env("UNPAYWALL_EMAIL", ""), "test@example.com")


if __name__ == "__main__":
    unittest.main()
