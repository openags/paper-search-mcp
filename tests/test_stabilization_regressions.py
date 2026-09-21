from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher
from paper_search_mcp.academic_platforms.google_scholar import GoogleScholarSearcher
from paper_search_mcp.academic_platforms.hal import HALSearcher
from paper_search_mcp.academic_platforms.sci_hub import SciHubFetcher
from paper_search_mcp.academic_platforms.semantic import (
    SemanticScholarRequestError,
    SemanticSearcher,
)
from paper_search_mcp.academic_platforms.zenodo import ZenodoSearcher


def test_mcp_dependency_stays_on_compatible_major_version():
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    project_config = pyproject.read_text(encoding="utf-8")

    assert '"mcp[cli]>=1.8.0,<2"' in project_config


def test_arxiv_uses_https_and_quotes_plain_multi_word_query():
    assert ArxivSearcher.BASE_URL == "https://export.arxiv.org/api/query"
    assert (
        ArxivSearcher._build_search_query("  machine   learning  ")
        == 'all:"machine learning"'
    )
    assert (
        ArxivSearcher._build_search_query('"machine learning"')
        == '"machine learning"'
    )
    assert ArxivSearcher._build_search_query("ti:agents") == "ti:agents"
    assert (
        ArxivSearcher._build_search_query("au:Hinton AND ti:dropout")
        == "au:Hinton AND ti:dropout"
    )


def test_zenodo_metadata_matches_paper_contract():
    hit = {
        "id": 12345,
        "doi": "10.5281/zenodo.12345",
        "metadata": {
            "title": "Zenodo Parser Test",
            "creators": [{"name": "Alice Example"}, {"name": "Bob Example"}],
            "description": "<p>Test abstract</p>",
            "publication_date": "2024-01-15",
        },
        "files": [],
        "links": {"html": "https://zenodo.org/record/12345"},
    }

    paper = ZenodoSearcher()._parse_record(hit)

    assert paper is not None
    assert paper.authors == ["Alice Example", "Bob Example"]
    assert paper.published_date is not None
    assert paper.published_date.isoformat() == "2024-01-15T00:00:00"
    assert paper.to_dict()["authors"] == "Alice Example; Bob Example"
    assert paper.to_dict()["published_date"] == "2024-01-15T00:00:00"

    hit["metadata"]["publication_date"] = "2024"
    year_only_paper = ZenodoSearcher()._parse_record(hit)
    assert year_only_paper is not None
    assert year_only_paper.published_date is not None
    assert year_only_paper.published_date.isoformat() == "2024-01-01T00:00:00"


def test_hal_metadata_matches_paper_contract():
    doc = {
        "halId_s": "hal-01234567",
        "title_s": ["HAL Parser Test"],
        "authFullName_s": ["Alice Example", "Bob Example"],
        "abstract_s": ["This is a test abstract"],
        "doiId_s": "10.1000/hal-test",
        "publicationDateY_i": 2023,
        "fileMain_s": "https://hal.science/hal-01234567/document",
        "uri_s": "https://hal.science/hal-01234567",
    }

    paper = HALSearcher()._parse_doc(doc)

    assert paper is not None
    assert paper.authors == ["Alice Example", "Bob Example"]
    assert paper.published_date is not None
    assert paper.published_date.isoformat() == "2023-01-01T00:00:00"
    assert paper.to_dict()["authors"] == "Alice Example; Bob Example"
    assert paper.to_dict()["published_date"] == "2023-01-01T00:00:00"

    doc.pop("publicationDateY_i")
    doc["submittedDate_s"] = "2022"
    year_only_paper = HALSearcher()._parse_doc(doc)
    assert year_only_paper is not None
    assert year_only_paper.published_date is not None
    assert year_only_paper.published_date.isoformat() == "2022-01-01T00:00:00"


def test_semantic_search_surfaces_api_error():
    searcher = SemanticSearcher()
    with patch.object(
        searcher,
        "request_api",
        return_value={
            "error": "rate_limited",
            "status_code": 429,
            "message": "Too many requests",
        },
    ), pytest.raises(
        SemanticScholarRequestError,
        match="HTTP 429.*Too many requests",
    ):
        searcher.search("cryptography", max_results=1)


def test_scihub_relative_pdf_uses_redirected_host(tmp_path):
    fetcher = SciHubFetcher(output_dir=str(tmp_path))
    response = SimpleNamespace(
        status_code=200,
        url="https://working-mirror.example/article/10.1000/test",
        content=b'<embed type="application/pdf" src="/downloads/paper.pdf">',
        text='<embed type="application/pdf" src="/downloads/paper.pdf">',
    )
    with patch.object(fetcher.session, "get", return_value=response):
        result = fetcher._get_direct_url("10.1000/test")

    assert result == "https://working-mirror.example/downloads/paper.pdf"


def test_google_scholar_retries_consent_page_once():
    searcher = GoogleScholarSearcher()
    assert (
        searcher.session.cookies.get("CONSENT", domain=".google.com")
        == searcher.CONSENT_COOKIE_VALUE
    )

    consent_response = Mock(
        status_code=200,
        text="<html><body>Before you continue to Google Scholar</body></html>",
    )
    result_response = Mock(
        status_code=200,
        text="""
            <html><body>
                <div class="gs_ri">
                    <h3 class="gs_rt"><a href="https://example.test/paper">Test Paper</a></h3>
                    <div class="gs_a">Ada Lovelace - Journal, 2024</div>
                    <div class="gs_rs">Abstract</div>
                </div>
            </body></html>
        """,
    )
    with (
        patch.object(
            searcher.session,
            "get",
            side_effect=[consent_response, result_response],
        ) as session_get,
        patch.object(searcher, "_rotate_user_agent"),
        patch(
            "paper_search_mcp.academic_platforms.google_scholar.time.sleep"
        ),
        patch(
            "paper_search_mcp.academic_platforms.google_scholar.random.uniform",
            return_value=0.0,
        ),
    ):
        papers = searcher.search("llm", max_results=1)

    assert [paper.title for paper in papers] == ["Test Paper"]
    assert session_get.call_count == 2
