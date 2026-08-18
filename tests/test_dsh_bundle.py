"""Offline structural tests for the DeepSeek Harness (dsh) bundle in dsh/.

These tests validate the integration contract without starting dsh or the MCP
server: the npm bundle manifest, the loader patch that mounts the MCP client
row, the version sync with the Python package, and the user-facing docs.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DSH_DIR = ROOT / "dsh"


def _read_pkg() -> dict:
    return json.loads((DSH_DIR / "package.json").read_text(encoding="utf-8"))


def _read_patch() -> list:
    return yaml.safe_load((DSH_DIR / "cordis.patch.yml").read_text(encoding="utf-8"))


def _mcp_row() -> dict:
    patch = _read_patch()
    rows = patch[0]["insert"]
    row = next(row for row in rows if row["id"] == "mcp-paper-search")
    return row


def test_bundle_manifest_declares_patch() -> None:
    pkg = _read_pkg()
    assert pkg["name"] == "paper-search-mcp-dsh"
    assert pkg["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert (DSH_DIR / pkg["dsh"]["bundle"]["patch"]).is_file()


def test_bundle_version_syncs_with_python_package() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert match, "pyproject.toml must declare a project version"
    assert _read_pkg()["version"] == match.group(1)


def test_patch_mounts_one_mcp_client_row() -> None:
    patch = _read_patch()
    assert len(patch) == 1
    insert = patch[0]["insert"]
    assert len(insert) == 1
    row = _mcp_row()
    assert row["name"] == "@deepseek-ai/dsh-mcp-client"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", row["id"])


def test_mcp_client_row_config() -> None:
    config = _mcp_row()["config"]
    assert config["serverName"] == "paper-search"
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", config["serverName"])
    assert config["transport"] == "stdio"
    assert config["command"] == "uvx"
    assert config["args"] == ["paper-search-mcp"]
    # No env block: the server auto-loads ~/.config/paper-search-mcp/.env, and
    # dsh scrubs ambient credential-shaped vars from spawned children. An env
    # block with empty-string defaults would also shadow the server's own
    # setdefault()-based .env loading.
    assert "env" not in config


def test_skill_frontmatter_is_dsh_compatible() -> None:
    skill = (DSH_DIR / "skills" / "paper-search" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---")
    body = skill.split("---", 2)[1]
    assert re.search(r"^name:\s+paper-search\s*$", body, re.MULTILINE)
    assert re.search(r"^description:\s*\S", body, re.MULTILINE)
    assert "mcp__paper-search__" in skill


def test_docs_document_the_install_path() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    bundle_readme = (DSH_DIR / "README.md").read_text(encoding="utf-8")
    assert "npx @deepseek-ai/dsh plugin --profile web add link:./dsh" in readme
    assert "npx @deepseek-ai/dsh plugin --profile web remove paper-search-mcp-dsh" in readme
    assert "mcp__paper-search__" in readme
    assert "git clone https://github.com/openags/paper-search-mcp.git" in bundle_readme
    assert "npx @deepseek-ai/dsh plugin --profile web add link:./dsh" in bundle_readme
    assert "npx @deepseek-ai/dsh" in bundle_readme
    assert "~/.config/paper-search-mcp/.env" in bundle_readme


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
