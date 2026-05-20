import os
from pathlib import Path
import tempfile
import unittest
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
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=True) as tmp:
            tmp.write("PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=test@example.com\n")
            tmp.flush()

            with patch.dict(
                os.environ,
                {
                    "PAPER_SEARCH_MCP_ENV_FILE": tmp.name,
                },
                clear=True,
            ):
                config.load_env_file(force=True)
                self.assertEqual(config.get_env("UNPAYWALL_EMAIL", ""), "test@example.com")

    def test_loads_user_config_env_before_cwd_env(self):
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as cwd:
            config_dir = Path(home_dir) / ".config" / "paper-search-mcp"
            config_dir.mkdir(parents=True)
            (config_dir / ".env").write_text(
                "PAPER_SEARCH_MCP_CORE_API_KEY=user-config-value\n",
                encoding="utf-8",
            )
            Path(cwd, ".env").write_text(
                "PAPER_SEARCH_MCP_CORE_API_KEY=cwd-value\n",
                encoding="utf-8",
            )

            try:
                os.chdir(cwd)
                with patch.dict(os.environ, {"HOME": home_dir}, clear=True):
                    config.load_env_file(force=True)
                    self.assertEqual(config.get_env("CORE_API_KEY", ""), "user-config-value")
            finally:
                os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
