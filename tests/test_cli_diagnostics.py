"""Library diagnostics must leave CLI stdout available for the result payload."""

import asyncio
import json
import sys
import subprocess
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from paper_search_mcp import cli, config
from paper_search_mcp.academic_platforms import arxiv, biorxiv, medrxiv


def run_command(command, *args):
    namespace = cli.build_parser().parse_args([command, *args])
    return asyncio.run(getattr(cli, f"cmd_{command}")(namespace))


def test_listing_sources_avoids_constructor_warnings(monkeypatch, capsys, caplog):
    monkeypatch.setattr(cli, "SEARCHERS", {})
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    monkeypatch.delenv("PAPER_SEARCH_MCP_CORE_API_KEY", raising=False)
    monkeypatch.setattr(config, "_ENV_LOADED", True)

    assert run_command("sources") == 0

    assert "core" in json.loads(capsys.readouterr().out)["sources"]
    assert "No CORE API key provided" not in caplog.text
    assert cli.SEARCHERS == {}


def test_concurrent_search_parse_errors_leave_stdout_untouched(
    monkeypatch, capsys, caplog
):
    arxiv_searcher = arxiv.ArxivSearcher()
    medrxiv_searcher = medrxiv.MedRxivSearcher()
    monkeypatch.setattr(
        cli, "SEARCHERS", {"arxiv": arxiv_searcher, "medrxiv": medrxiv_searcher}
    )
    # Both actual search methods must overlap. Checking stdout inside both
    # workers catches process-global redirection around asyncio.to_thread.
    rendezvous = Barrier(2, timeout=5)
    original_stdout = sys.stdout

    def response_for(*args, **kwargs):
        assert sys.stdout is original_stdout
        rendezvous.wait()
        assert sys.stdout is original_stdout
        response = Mock(status_code=200, content=b"mock feed")
        response.json.return_value = {"collection": [{}]}
        return response

    monkeypatch.setattr(arxiv_searcher, "_request_with_retries", response_for)
    monkeypatch.setattr(medrxiv_searcher.session, "get", response_for)
    monkeypatch.setattr(
        arxiv.feedparser, "parse", lambda _: SimpleNamespace(entries=[SimpleNamespace()])
    )

    assert run_command("search", "biology", "-s", "arxiv,medrxiv") == 0

    result = json.loads(capsys.readouterr().out)
    assert result["source_results"] == {"arxiv": 0, "medrxiv": 0}
    assert result["errors"] == {}
    assert "Error parsing arXiv entry" in caplog.text
    assert "Error parsing medRxiv entry" in caplog.text
    assert sys.stdout is original_stdout


def test_search_retry_and_terminal_error_leave_json_clean(monkeypatch, capsys, caplog):
    searcher = medrxiv.MedRxivSearcher()
    monkeypatch.setattr(cli, "SEARCHERS", {"medrxiv": searcher})
    monkeypatch.setattr(
        searcher.session, "get", Mock(side_effect=requests.ConnectionError("offline"))
    )

    assert run_command("search", "biology", "-s", "medrxiv") == 0

    assert json.loads(capsys.readouterr().out)["total"] == 0
    assert "Attempt 1 failed, retrying..." in caplog.text
    assert "Failed to connect to medRxiv API after 3 attempts: offline" in caplog.text


@pytest.mark.parametrize("module", [biorxiv, medrxiv])
@pytest.mark.parametrize("succeeds", [True, False])
def test_download_retry_leaves_one_json_result(
    module, succeeds, monkeypatch, tmp_path, capsys, caplog
):
    source = module.__name__.rsplit(".", 1)[-1]
    searcher = (
        module.BioRxivSearcher() if source == "biorxiv" else module.MedRxivSearcher()
    )
    response = Mock(content=b"%PDF-mock")
    failure = requests.ConnectionError("offline")
    attempts = [failure, response] if succeeds else [failure] * searcher.max_retries
    monkeypatch.setattr(searcher.session, "get", Mock(side_effect=attempts))
    monkeypatch.setattr(cli, "SEARCHERS", {source: searcher})

    assert run_command("download", source, "10.1101/example", "-o", str(tmp_path)) == (
        0 if succeeds else 1
    )

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("ok" if succeeds else "error")
    assert "Attempt 1 failed, retrying..." in caplog.text
    if succeeds:
        assert (tmp_path / "10.1101_example.pdf").read_bytes() == b"%PDF-mock"
    else:
        assert "Failed to download PDF after 3 attempts" in result["message"]


@pytest.mark.parametrize(
    ("module", "searcher_class"),
    [
        (arxiv, arxiv.ArxivSearcher),
        (biorxiv, biorxiv.BioRxivSearcher),
        (medrxiv, medrxiv.MedRxivSearcher),
    ],
)
def test_read_failure_diagnostic_does_not_become_paper_text(
    module, searcher_class, monkeypatch, tmp_path, capsys, caplog
):
    source = module.__name__.rsplit(".", 1)[-1]
    (tmp_path / "example.pdf").write_bytes(b"invalid PDF")
    monkeypatch.setattr(cli, "SEARCHERS", {source: searcher_class()})
    monkeypatch.setattr(module, "PdfReader", Mock(side_effect=ValueError("bad PDF")))

    assert run_command("read", source, "example", "-o", str(tmp_path)) == 0

    # Preserve the existing empty-string result on a PDF parsing failure.
    assert capsys.readouterr().out == "\n"
    assert "Error reading PDF for paper example: bad PDF" in caplog.text


@pytest.mark.parametrize("command", ["search", "download"])
def test_default_logging_goes_to_stderr_in_cli_process(command, tmp_path):
    # A fresh interpreter verifies default logging without pytest log handlers.
    script = """
import sys
from unittest.mock import Mock
import requests
from paper_search_mcp import cli
from paper_search_mcp.academic_platforms.medrxiv import MedRxivSearcher

searcher = MedRxivSearcher()
searcher.session.get = Mock(side_effect=requests.ConnectionError("offline"))
cli.SEARCHERS = {"medrxiv": searcher}
cli.main()
"""
    args = (
        ["search", "biology", "-s", "medrxiv"]
        if command == "search"
        else ["download", "medrxiv", "example", "-o", str(tmp_path)]
    )
    result = subprocess.run(
        [sys.executable, "-c", script, *args],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == (0 if command == "search" else 1)
    assert "Attempt 1 failed, retrying..." in result.stderr
    if command == "search":
        assert payload["total"] == 0
        assert "Failed to connect to medRxiv API" in result.stderr
    else:
        assert payload["status"] == "error"
