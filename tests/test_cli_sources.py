"""Offline coverage for source presets and exact CLI selection (PR #71)."""

import asyncio
import json
from unittest.mock import Mock

import pytest

from paper_search_mcp import cli, config


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    monkeypatch.setattr(cli, "SEARCHERS", {})
    monkeypatch.setattr(config, "_ENV_LOADED", True)
    for name in ("SEMANTIC_SCHOLAR_API_KEY", "IEEE_API_KEY", "ACM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"PAPER_SEARCH_MCP_{name}", raising=False)


@pytest.mark.parametrize("arguments,expected", [
    ([], cli.ALL_SOURCES),
    (["--exhaustive"], cli.ALL_SOURCES),
    (["-s", "all"], cli.ALL_SOURCES),
    (["-s", "all", "--exhaustive"], cli.ALL_SOURCES),
    (["-s", "fast"], cli.FAST_SOURCES),
    (["--exhaustive", "-s", "fast"], cli.FAST_SOURCES),
    (["-s", "fastest"], cli.FASTEST_SOURCES),
    (["-s", "fastest", "--exhaustive"], cli.FASTEST_SOURCES),
    (["-s", " ARXIV, Crossref, arxiv "], ["arxiv", "crossref"]),
    (["-s", "arxiv", "--exhaustive"], ["arxiv"]),
    (["-s", "unpaywall"], ["unpaywall"]),
    (["-s", "acm"], ["acm"]),
])
@pytest.mark.parametrize("query", ["machine learning", "DOI:10.1038/s41593-020-0658-y"])
def test_command_respects_sources(arguments, expected, query, monkeypatch, capsys):
    searchers = {}

    def get_searcher(source):
        searchers[source] = Mock()
        searchers[source].search.return_value = []
        return searchers[source]

    monkeypatch.setattr(cli, "_get_searcher", get_searcher)
    args = cli.build_parser().parse_args(["search", query, *arguments])
    assert asyncio.run(cli.cmd_search(args)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sources_used"] == expected
    assert list(searchers) == expected
    assert output["source_results"] == dict.fromkeys(expected, 0)
    assert output["errors"] == {}
    assert set(output) == {"query", "sources_used", "source_results", "errors", "total", "papers"}
    for searcher in searchers.values():
        searcher.search.assert_called_once_with(query, max_results=5)


@pytest.mark.parametrize("source", ["unknown", "  ", ",,"])
def test_doi_cannot_rescue_invalid_sources(source, monkeypatch, capsys):
    get_searcher = Mock(side_effect=AssertionError("No source should be constructed"))
    monkeypatch.setattr(cli, "_get_searcher", get_searcher)
    args = cli.build_parser().parse_args(["search", "10.1038/test", "-s", source])
    assert asyncio.run(cli.cmd_search(args)) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "No valid sources selected"
    get_searcher.assert_not_called()


@pytest.mark.parametrize("name", ["SEMANTIC_SCHOLAR_API_KEY", "PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY"])
@pytest.mark.parametrize("key,include", [("test-key", True), (" test-key ", True), ("", False), (" \t", False)])
def test_fast_semantic_key_handling(name, key, include, monkeypatch):
    monkeypatch.setenv(name, key)
    assert ("semantic" in cli._parse_sources("fast")) is include
    assert "semantic" not in cli._parse_sources("fastest")
    assert cli.SEARCHERS == {}


def test_blank_prefixed_key_overrides_legacy_key(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "legacy-key")
    monkeypatch.setenv("PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY", " ")
    assert "semantic" not in cli._parse_sources("fast")


@pytest.mark.parametrize("preset", ["all", "fast", "fastest"])
def test_presets_never_add_optional_paid_or_restricted_sources(preset, monkeypatch):
    monkeypatch.setenv("PAPER_SEARCH_MCP_IEEE_API_KEY", "existing-key")
    assert "ieee" in cli._available_sources()
    assert {"ieee", "scihub", "sci_hub"}.isdisjoint(cli._parse_sources(preset))
    assert cli.SEARCHERS == {}


def test_get_searcher_only_initializes_requested_source_and_caches(monkeypatch):
    arxiv = Mock()
    unused = Mock(side_effect=AssertionError("Unselected source initialized"))
    monkeypatch.setattr(cli, "ArxivSearcher", arxiv)
    monkeypatch.setattr(cli, "CORESearcher", unused)
    monkeypatch.setattr(cli, "UnpaywallResolver", unused)
    assert cli._get_searcher("arxiv") is arxiv.return_value
    assert cli._get_searcher("arxiv") is arxiv.return_value
    arxiv.assert_called_once_with()
    unused.assert_not_called()
    assert list(cli.SEARCHERS) == ["arxiv"]


@pytest.mark.parametrize("command,method,result", [
    ("download", "download_pdf", "/tmp/example.pdf"),
    ("read", "read_paper", "Paper text"),
])
def test_single_source_commands_are_lazy(command, method, result, monkeypatch, capsys):
    searcher = Mock()
    getattr(searcher, method).return_value = result
    constructor = Mock(return_value=searcher)
    monkeypatch.setattr(cli, "ArxivSearcher", constructor)
    monkeypatch.setattr(cli, "CORESearcher", Mock(side_effect=AssertionError("Unused source")))
    args = cli.build_parser().parse_args([command, "arxiv", "1234.5678"])
    assert asyncio.run(getattr(cli, f"cmd_{command}")(args)) == 0
    assert list(cli.SEARCHERS) == ["arxiv"]
    getattr(searcher, method).assert_called_once_with("1234.5678", "./downloads")
    output = capsys.readouterr().out
    if command == "download":
        assert json.loads(output) == {"status": "ok", "path": result}
    else:
        assert output.strip() == result


def test_acm_remains_keyless_and_explicit():
    from paper_search_mcp.academic_platforms.acm import ACMSearcher

    assert "acm" in cli._available_sources()
    assert isinstance(cli._get_searcher("acm"), ACMSearcher)
    assert "acm" not in cli._parse_sources("all")


def test_sources_lists_without_constructing_searchers(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_get_searcher", Mock(side_effect=AssertionError("Unexpected construction")))
    assert asyncio.run(cli.cmd_sources(cli.build_parser().parse_args(["sources"]))) == 0
    assert json.loads(capsys.readouterr().out)["sources"] == sorted(cli._available_sources())
    assert cli.SEARCHERS == {}
