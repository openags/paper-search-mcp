from datetime import datetime, timezone

from paper_search_mcp.paper import Paper


def _paper(**overrides):
    values = {
        "paper_id": "paper-1",
        "title": "Test Paper",
        "authors": ["Ada Lovelace"],
        "abstract": "",
        "doi": "",
        "published_date": None,
        "pdf_url": "",
        "url": "https://example.test/paper-1",
        "source": "test",
    }
    values.update(overrides)
    return Paper(**values)


def test_to_dict_serializes_datetime_values():
    result = _paper(
        published_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        updated_date=datetime(2024, 2, 16, 12, 30, tzinfo=timezone.utc),
    ).to_dict()

    assert result["published_date"] == "2024-01-15T00:00:00+00:00"
    assert result["updated_date"] == "2024-02-16T12:30:00+00:00"


def test_to_dict_tolerates_string_dates_and_authors():
    result = _paper(
        authors="Already Serialized",
        published_date="2024-01-15",
    ).to_dict()

    assert result["authors"] == "Already Serialized"
    assert result["published_date"] == "2024-01-15"
