"""Offline coverage for citation and fallback commands with lazy connectors."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from paper_search_mcp import cli, config


@pytest.fixture(autouse=True)
def isolated_searchers(monkeypatch):
    monkeypatch.setattr(cli, "SEARCHERS", {})
    monkeypatch.setattr(config, "_ENV_LOADED", True)


@pytest.mark.parametrize("format,expected", [("bibtex", "@article{"), ("ris", "TY  - JOUR")])
def test_cite_initializes_only_selected_source(format, expected, monkeypatch, capsys):
    searcher = Mock()
    searcher.search.return_value = [SimpleNamespace(to_dict=lambda: {
        "title": "A Useful Paper", "authors": "Ada Lovelace",
        "published_date": "2024", "doi": "10.1000/example",
    })]
    monkeypatch.setattr(cli, "CrossRefSearcher", Mock(return_value=searcher))
    monkeypatch.setattr(cli, "CORESearcher", Mock(side_effect=AssertionError("Unused connector")))
    args = cli.build_parser().parse_args(["cite", "example", "-s", "crossref", "-f", format])

    assert asyncio.run(cli.cmd_cite(args)) == 0
    assert expected in capsys.readouterr().out
    assert list(cli.SEARCHERS) == ["crossref"]
    searcher.search.assert_called_once_with("example", max_results=5)


def test_fallback_continues_after_constructor_failure_and_stops_at_pdf(monkeypatch, capsys, tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\nmock")
    searcher = Mock()
    searcher.download_pdf.return_value = str(pdf)
    get_searcher = Mock(side_effect=[RuntimeError("Missing configuration"), searcher])
    monkeypatch.setattr(cli, "_get_searcher", get_searcher)
    args = cli.build_parser().parse_args(["fallback", " 10.1000/example ", "-o", str(tmp_path)])

    assert asyncio.run(cli.cmd_fallback(args)) == 0
    assert [call.args[0] for call in get_searcher.call_args_list] == ["unpaywall", "core"]
    searcher.download_pdf.assert_called_once_with("10.1000/example", str(tmp_path))
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "source": "core", "path": str(pdf)}


def test_fallback_rejects_html_and_saves_abstract(monkeypatch, capsys, tmp_path):
    html = tmp_path / "error.pdf"
    html.write_text("<html>Access denied</html>")
    searcher = Mock()
    searcher.download_pdf.return_value = str(html)
    monkeypatch.setattr(cli, "FALLBACK_DOWNLOAD_ORDER", ["core"])
    monkeypatch.setattr(cli, "_get_searcher", Mock(return_value=searcher))
    monkeypatch.setattr(cli, "metadata_for_identifier", Mock(return_value={
        "title": "A Useful Paper", "authors": ["Ada Lovelace"],
        "published_date": "2024", "abstract": "Available abstract.",
        "doi": "10.1000/example",
    }))
    args = cli.build_parser().parse_args(["fallback", "10.1000/example", "-o", str(tmp_path)])

    assert asyncio.run(cli.cmd_fallback(args)) == 0
    metadata = tmp_path / "Lovelace_2024_A_Useful_Paper.txt"
    assert json.loads(capsys.readouterr().out) == {"status": "abstract_only", "path": str(metadata)}
    assert "Available abstract." in metadata.read_text()


@pytest.mark.parametrize("opt_in", [False, True])
def test_fallback_scihub_requires_opt_in(opt_in, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "FALLBACK_DOWNLOAD_ORDER", ["scihub"])
    get_searcher = Mock(side_effect=RuntimeError("Unavailable"))
    monkeypatch.setattr(cli, "_get_searcher", get_searcher)
    monkeypatch.setattr(cli, "metadata_for_identifier", Mock(return_value={}))
    arguments = ["fallback", "10.1000/example", "-o", str(tmp_path)]
    if opt_in:
        arguments.append("--use-scihub")

    assert asyncio.run(cli.cmd_fallback(cli.build_parser().parse_args(arguments))) == 0
    assert get_searcher.call_count == int(opt_in)
    assert json.loads(capsys.readouterr().out)["status"] == "abstract_only"


@pytest.mark.parametrize("command,arguments", [
    ("download", ["arxiv", "1234.5678"]),
    ("read", ["arxiv", "1234.5678"]),
    ("fallback", ["10.1000/example"]),
])
def test_download_commands_share_configured_output(command, arguments, monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_SEARCH_MCP_DEFAULT_OUTPUT", str(tmp_path))
    parser = cli.build_parser()
    assert parser.parse_args([command, *arguments]).save_path == str(tmp_path)
    assert parser.parse_args([command, *arguments, "-o", "explicit"]).save_path == "explicit"
