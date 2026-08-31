import unittest
from datetime import datetime

from paper_search_mcp.paper import Paper


def _make_paper(published_date, updated_date=None):
    return Paper(
        paper_id="1",
        title="t",
        authors=["a"],
        abstract="abs",
        doi="10.1/x",
        published_date=published_date,
        pdf_url="https://example.com/x.pdf",
        url="https://example.com/x",
        source="zenodo",
        updated_date=updated_date,
    )


class TestPaperToDict(unittest.TestCase):
    def test_to_dict_with_datetime_date(self):
        paper = _make_paper(datetime(2024, 5, 1))
        self.assertEqual(paper.to_dict()["published_date"], "2024-05-01T00:00:00")

    def test_to_dict_with_none_date(self):
        paper = _make_paper(None)
        self.assertEqual(paper.to_dict()["published_date"], "")

    def test_to_dict_with_string_date_does_not_crash(self):
        # Zenodo's parser builds published_date as an already-formatted
        # string (e.g. "2024-05-01") rather than a datetime. Before the fix,
        # to_dict() called .isoformat() unconditionally on any truthy value
        # and raised AttributeError: 'str' object has no attribute 'isoformat'.
        paper = _make_paper("2024-05-01", updated_date="2024-06-01")
        result = paper.to_dict()
        self.assertEqual(result["published_date"], "2024-05-01")
        self.assertEqual(result["updated_date"], "2024-06-01")


if __name__ == "__main__":
    unittest.main()
