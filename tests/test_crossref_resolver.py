import unittest
from unittest.mock import patch

from paper_search_mcp.crossref_resolver import CROSSREF_WORKS_URL, resolve_title


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TestCrossrefResolver(unittest.TestCase):
    def test_resolve_title_returns_crossref_shape(self):
        payload = {
            "message": {
                "items": [
                    {
                        "title": ["Attention Is All You Need"],
                        "DOI": "10.1201/9781003561460-19",
                        "score": 34.5,
                        "issued": {"date-parts": [[2025]]},
                    }
                ]
            }
        }

        with patch("paper_search_mcp.crossref_resolver.requests.get", return_value=FakeResponse(payload)) as mock_get:
            result = resolve_title("Attention Is All You Need")

        self.assertEqual(
            result,
            {
                "title": "Attention Is All You Need",
                "doi": "10.1201/9781003561460-19",
                "score": 34.5,
                "year": 2025,
            },
        )
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["query.title"], "Attention Is All You Need")
        self.assertEqual(kwargs["params"]["rows"], 40)
        self.assertEqual(mock_get.call_args.args[0], CROSSREF_WORKS_URL)

    def test_resolve_title_not_found(self):
        with patch(
            "paper_search_mcp.crossref_resolver.requests.get",
            return_value=FakeResponse({"message": {"items": []}}),
        ):
            self.assertEqual(resolve_title("missing"), {"error": "not found"})


if __name__ == "__main__":
    unittest.main()
