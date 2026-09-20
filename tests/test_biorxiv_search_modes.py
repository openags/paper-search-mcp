from unittest.mock import Mock

import pytest

from paper_search_mcp.academic_platforms.biorxiv import BioRxivSearcher


def _mock_response(collection):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"collection": collection}
    return response


def _paper_item(doi="10.1101/2023.12.30.573731"):
    return {
        "doi": doi,
        "title": "Test paper",
        "authors": "A One; B Two",
        "abstract": "Abstract",
        "date": "2024-01-01",
        "version": "1",
        "category": "bioinformatics",
    }


@pytest.fixture
def searcher():
    instance = BioRxivSearcher()
    instance.session.get = Mock(return_value=_mock_response([_paper_item()]))
    return instance


def test_category_search_uses_interval_json_endpoint(searcher):
    papers = searcher.search("Cell Biology", max_results=1, days=30)

    assert len(papers) == 1
    called_url = searcher.session.get.call_args.args[0]
    assert "/0/json?category=cell_biology" in called_url


def test_doi_lookup_uses_documented_doi_endpoint(searcher):
    doi = "10.1101/2023.12.30.573731"

    papers = searcher.search(f"https://doi.org/{doi}", max_results=1)

    assert papers[0].doi == doi
    assert searcher.session.get.call_args.args[0] == (
        f"https://api.biorxiv.org/details/biorxiv/{doi}/na/json"
    )


@pytest.mark.parametrize("separator", ["/", ":", "..", " to "])
def test_date_range_query_uses_interval_endpoint(searcher, separator):
    papers = searcher.search(f"2024-01-01{separator}2024-01-31", max_results=1)

    assert len(papers) == 1
    assert searcher.session.get.call_args.args[0] == (
        "https://api.biorxiv.org/details/biorxiv/2024-01-01/2024-01-31/0/json"
    )


def test_blank_query_returns_recent_papers_without_category(searcher):
    papers = searcher.search("", max_results=1, days=7)

    assert len(papers) == 1
    called_url = searcher.session.get.call_args.args[0]
    assert called_url.endswith("/0/json")
    assert "?category=" not in called_url


def test_interval_search_advances_cursor_for_full_page(searcher):
    first_page = [_paper_item() for _ in range(100)]
    second_page = [_paper_item("10.1101/2024.01.01.999999")]
    searcher.session.get.side_effect = [
        _mock_response(first_page),
        _mock_response(second_page),
    ]

    papers = searcher.search("bioinformatics", max_results=101)

    assert len(papers) == 101
    assert (
        "/100/json?category=bioinformatics"
        in (searcher.session.get.call_args_list[1].args[0])
    )


def test_zero_results_does_not_call_api(searcher):
    assert searcher.search("bioinformatics", max_results=0) == []
    searcher.session.get.assert_not_called()


def test_reversed_date_range_is_rejected(searcher):
    with pytest.raises(ValueError, match="start must not be after"):
        searcher.search("2024-02-01/2024-01-01")
