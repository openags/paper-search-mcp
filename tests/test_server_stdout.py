"""Tool diagnostics must stay off stdout, which carries JSON-RPC in stdio mode."""

import asyncio
import logging

import pytest

from paper_search_mcp import server


@pytest.mark.parametrize(
    ("tool", "searcher"),
    [
        ("read_arxiv_paper", "arxiv_searcher"),
        ("read_biorxiv_paper", "biorxiv_searcher"),
        ("read_medrxiv_paper", "medrxiv_searcher"),
        ("read_iacr_paper", "iacr_searcher"),
        ("read_semantic_paper", "semantic_searcher"),
    ],
)
def test_read_tool_failure_is_logged_not_printed(monkeypatch, capsys, caplog, tool, searcher):
    def fail(paper_id, save_path="./downloads"):
        raise RuntimeError("connector unavailable")

    monkeypatch.setattr(getattr(server, searcher), "read_paper", fail)

    with caplog.at_level(logging.WARNING, logger=server.logger.name):
        result = asyncio.run(getattr(server, tool)("1234.5678"))

    assert result == ""
    assert capsys.readouterr().out == ""
    assert "Error reading paper 1234.5678: connector unavailable" in caplog.text
