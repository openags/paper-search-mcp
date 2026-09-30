"""Offline coverage for CLI sorting and the existing search response contract."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from paper_search_mcp import cli


def paper(identifier, **fields):
    return {"paper_id": identifier, "title": identifier, "authors": "Author", **fields}


def ids(papers):
    return [item["paper_id"] for item in papers]


@pytest.mark.parametrize("order", ["relevance", "citations", "date"])
def test_parser_accepts_sort(order):
    args = cli.build_parser().parse_args(["search", "query", "--sort", order])
    assert args.sort == order
    assert args.sources == "all"
    assert args.max_results == 5


def test_parser_default_and_invalid_sort(capsys):
    assert cli.build_parser().parse_args(["search", "query"]).sort == "relevance"
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["search", "query", "--sort", "unknown"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_citation_sort_mixed_values_and_stable_ties():
    values = [None, "12", 2, "12", "bad", 0, "0", -1, float("nan"),
              "Infinity", {}, True, "9007199254740993", 9007199254740992]
    papers = [paper(str(i), citations=value) for i, value in enumerate(values)]
    papers.append(paper("missing"))
    result = cli._sort_papers(papers, "citations")
    assert ids(result) == ["12", "13", "1", "3", "2", "5", "6", "0", "4",
                           "7", "8", "9", "10", "11", "missing"]
    assert ids(papers) == [str(i) for i in range(len(values))] + ["missing"]


def test_date_sort_mixed_values_timezone_and_stable_ties():
    values = [None, "bad", "2024-01-01T01:00:00+02:00", date(2024, 1, 1),
              "2024-01-01T00:00:00Z", datetime(2024, 1, 2),
              datetime(2024, 1, 2, tzinfo=timezone(timedelta(hours=-2))),
              "2024-02-30", "", 2024, "2024-01-01", date.min]
    papers = [paper(str(i), published_date=value) for i, value in enumerate(values)]
    papers.append(paper("missing"))
    assert ids(cli._sort_papers(papers, "date")) == [
        "6", "5", "3", "4", "10", "2", "11", "0", "1", "7", "8", "9", "missing"]


def install_searchers(monkeypatch, source_papers):
    searchers = {}
    for source, results in source_papers.items():
        searcher = Mock()
        if isinstance(results, Exception):
            searcher.search.side_effect = results
        else:
            searcher.search.return_value = [Mock(to_dict=Mock(return_value=p)) for p in results]
        searchers[source] = searcher
    monkeypatch.setattr(cli, "SEARCHERS", searchers)
    monkeypatch.setattr(cli, "_init_searchers", lambda: None)
    return searchers


@pytest.mark.parametrize("order,expected", [
    (None, ["first", "second", "third"]),
    ("relevance", ["first", "second", "third"]),
    ("citations", ["second", "first", "third"]),
    ("date", ["third", "first", "second"]),
])
def test_cmd_search_sorts_after_deduplication(monkeypatch, capsys, order, expected):
    first = paper("first", doi="10.1/example", citations=5, published_date="2024-01-01")
    searchers = install_searchers(monkeypatch, {
        "arxiv": [first, paper("second", citations="10", published_date=None)],
        "semantic": [paper("duplicate", doi="10.1/EXAMPLE", citations=999,
                           published_date="2030-01-01"),
                     paper("third", citations=None, published_date="2025-01-01")],
    })
    argv = ["search", "query", "--sources", "arxiv,semantic", "--year", "2024", "-n", "3"]
    if order:
        argv.extend(["--sort", order])
    args = cli.build_parser().parse_args(argv)
    assert asyncio.run(cli.cmd_search(args)) == 0
    output = json.loads(capsys.readouterr().out)
    assert ids(output["papers"]) == expected
    assert output["query"] == "query"
    assert output["sources_used"] == ["arxiv", "semantic"]
    assert output["source_results"] == {"arxiv": 2, "semantic": 2}
    assert output["total"] == 3
    assert output["errors"] == {}
    assert all(p["source"] in searchers for p in output["papers"])
    searchers["arxiv"].search.assert_called_once_with("query", max_results=3)
    searchers["semantic"].search.assert_called_once_with("query", max_results=3, year="2024")


def test_cmd_search_partial_failure_retains_error_contract(monkeypatch, capsys):
    install_searchers(monkeypatch, {
        "arxiv": RuntimeError("offline"), "semantic": [paper("found", citations="4")]})
    args = cli.build_parser().parse_args(["search", "query", "--sort", "citations"])
    assert asyncio.run(cli.cmd_search(args)) == 0
    output = json.loads(capsys.readouterr().out)
    assert ids(output["papers"]) == ["found"]
    assert output["errors"] == {"arxiv": "offline"}
    assert output["source_results"] == {"arxiv": 0, "semantic": 1}


def test_cmd_search_empty_results(monkeypatch, capsys):
    install_searchers(monkeypatch, {"arxiv": []})
    args = cli.build_parser().parse_args(["search", "query", "--sort", "date"])
    assert asyncio.run(cli.cmd_search(args)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["papers"] == []
    assert output["total"] == 0


def test_cmd_search_invalid_source(monkeypatch, capsys):
    install_searchers(monkeypatch, {"arxiv": []})
    args = cli.build_parser().parse_args(["search", "query", "--sources", "unknown", "--sort", "date"])
    assert asyncio.run(cli.cmd_search(args)) == 1
    assert json.loads(capsys.readouterr().out) == {
        "error": "No valid sources selected", "available": ["arxiv"]}
