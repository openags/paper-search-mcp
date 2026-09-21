import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from paper_search_mcp import server
from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher


class TestOpenAlexSearcher(unittest.TestCase):
    def setUp(self):
        self.searcher = OpenAlexSearcher()

    @staticmethod
    def _response(results=None, status_code=200):
        response = Mock(status_code=status_code)
        response.json.return_value = {"results": results or []}
        return response

    def test_search_passes_filter_and_uses_supported_page_limit(self):
        response = self._response()
        self.searcher.session.get = Mock(return_value=response)

        papers = self.searcher.search(
            "artificial intelligence",
            max_results=250,
            filter=" publication_year:2024,open_access.is_oa:true ",
        )

        self.assertEqual(papers, [])
        self.searcher.session.get.assert_called_once_with(
            self.searcher.BASE_URL,
            params={
                "search": "artificial intelligence",
                "per_page": 100,
                "filter": "publication_year:2024,open_access.is_oa:true",
            },
            timeout=30,
        )

    def test_search_omits_empty_filter(self):
        response = self._response()
        self.searcher.session.get = Mock(return_value=response)

        self.searcher.search("machine learning", max_results=5, filter="  ")

        request_params = self.searcher.session.get.call_args.kwargs["params"]
        self.assertEqual(
            request_params,
            {"search": "machine learning", "per_page": 5},
        )

    def test_search_parses_filtered_result(self):
        response = self._response(
            [
                {
                    "id": "https://openalex.org/W123",
                    "title": "A filtered paper",
                    "authorships": [
                        {"author": {"display_name": "Ada Lovelace"}}
                    ],
                    "abstract_inverted_index": {
                        "Filtered": [0],
                        "abstract": [1],
                    },
                    "doi": "https://doi.org/10.1000/example",
                    "primary_location": {
                        "landing_page_url": "https://example.org/paper",
                        "pdf_url": "https://example.org/paper.pdf",
                    },
                    "open_access": {"is_oa": True},
                    "publication_date": "2024-02-03",
                    "concepts": [{"display_name": "Computer science"}],
                    "cited_by_count": 7,
                }
            ]
        )
        self.searcher.session.get = Mock(return_value=response)

        papers = self.searcher.search(
            "filtered",
            filter="publication_year:2024",
        )

        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.paper_id, "W123")
        self.assertEqual(paper.title, "A filtered paper")
        self.assertEqual(paper.authors, ["Ada Lovelace"])
        self.assertEqual(paper.abstract, "Filtered abstract")
        self.assertEqual(paper.doi, "10.1000/example")
        self.assertEqual(paper.published_date.year, 2024)
        self.assertEqual(paper.citations, 7)

    def test_non_success_response_returns_no_results(self):
        self.searcher.session.get = Mock(return_value=self._response(status_code=429))

        self.assertEqual(
            self.searcher.search("machine learning", filter="publication_year:2024"),
            [],
        )

    def test_user_agent_header(self):
        user_agent = self.searcher.session.headers.get("User-Agent", "")
        self.assertIn("paper-search-mcp", user_agent)
        self.assertIn("mailto:", user_agent)


class TestSearchOpenAlexTool(unittest.TestCase):
    def test_tool_forwards_non_empty_filter(self):
        search = AsyncMock(return_value=[])
        with patch.object(server, "async_search", search):
            result = asyncio.run(
                server.search_openalex(
                    "machine learning",
                    max_results=3,
                    filter=" publication_year:2024 ",
                )
            )

        self.assertEqual(result, [])
        search.assert_awaited_once_with(
            server.openalex_searcher,
            "machine learning",
            3,
            filter="publication_year:2024",
        )

    def test_tool_omits_empty_filter(self):
        search = AsyncMock(return_value=[])
        with patch.object(server, "async_search", search):
            asyncio.run(server.search_openalex("machine learning", filter=""))

        search.assert_awaited_once_with(
            server.openalex_searcher,
            "machine learning",
            10,
        )


if __name__ == "__main__":
    unittest.main()
