# tests/test_arxiv.py
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep
from unittest.mock import Mock, patch

import requests

from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher


EMPTY_ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""


def response_with(content: bytes, status_code: int = 200):
    response = Mock(spec=requests.Response)
    response.content = content
    response.status_code = status_code
    return response


class TestArxivRateLimiting(unittest.TestCase):
    def setUp(self):
        ArxivSearcher._last_request_at = 0.0

    def tearDown(self):
        ArxivSearcher._last_request_at = 0.0

    def test_soft_rate_limit_retries_then_succeeds(self):
        searcher = ArxivSearcher()
        searcher.session.get = Mock(side_effect=[
            response_with(b"  Rate exceeded.\n"),
            response_with(EMPTY_ATOM_FEED),
        ])

        with (
            patch.object(ArxivSearcher, "_pace_locked") as pace,
            patch("paper_search_mcp.academic_platforms.arxiv.time.sleep") as wait,
        ):
            self.assertEqual(searcher.search("test"), [])

        self.assertEqual(searcher.session.get.call_count, 2)
        self.assertEqual(pace.call_count, 2)
        wait.assert_called_once_with(5.0)

    def test_persistent_soft_rate_limit_raises_without_final_sleep(self):
        searcher = ArxivSearcher()
        searcher.session.get = Mock(
            side_effect=[response_with(b"Rate exceeded.")] * 3
        )

        with (
            patch.object(ArxivSearcher, "_pace_locked"),
            patch("paper_search_mcp.academic_platforms.arxiv.time.sleep") as wait,
            self.assertRaisesRegex(requests.RequestException, "Rate exceeded"),
        ):
            searcher.search("test")

        self.assertEqual(
            [call.args for call in wait.call_args_list],
            [(5.0,), (10.0,)],
        )

    def test_http_429_retry_exhaustion_raises(self):
        searcher = ArxivSearcher()
        searcher.session.get = Mock(
            side_effect=[response_with(b"", status_code=429)] * 3
        )

        with (
            patch.object(ArxivSearcher, "_pace_locked"),
            patch("paper_search_mcp.academic_platforms.arxiv.time.sleep") as wait,
            self.assertRaisesRegex(requests.RequestException, "HTTP 429"),
        ):
            searcher.search("test")

        self.assertEqual(
            [call.args for call in wait.call_args_list],
            [(1.5,), (3.0,)],
        )

    def test_network_failure_keeps_existing_empty_result_behavior(self):
        searcher = ArxivSearcher()
        searcher.session.get = Mock(
            side_effect=requests.ConnectionError("offline")
        )

        with (
            patch.object(ArxivSearcher, "_pace_locked"),
            patch("paper_search_mcp.academic_platforms.arxiv.time.sleep") as wait,
        ):
            self.assertEqual(searcher.search("test"), [])

        self.assertEqual(searcher.session.get.call_count, 3)
        self.assertEqual(
            [call.args for call in wait.call_args_list],
            [(1.5,), (3.0,)],
        )

    def test_soft_limit_detection_does_not_decode_response_text(self):
        class ContentOnlyResponse:
            content = b"Rate exceeded. Please try again later."

            @property
            def text(self):
                raise AssertionError("response.text must not be accessed")

        self.assertTrue(ArxivSearcher._is_soft_rate_limit(ContentOnlyResponse()))

    def test_pacing_uses_shared_timestamp(self):
        ArxivSearcher._last_request_at = 10.0

        with (
            patch(
                "paper_search_mcp.academic_platforms.arxiv.time.monotonic",
                side_effect=[11.0, 13.0],
            ),
            patch("paper_search_mcp.academic_platforms.arxiv.time.sleep") as wait,
        ):
            ArxivSearcher._pace_locked()

        wait.assert_called_once_with(2.0)
        self.assertEqual(ArxivSearcher._last_request_at, 13.0)

    def test_requests_from_different_instances_are_serialized(self):
        state_lock = Lock()
        active_requests = 0
        maximum_active_requests = 0

        def get_response(*args, **kwargs):
            nonlocal active_requests, maximum_active_requests
            with state_lock:
                active_requests += 1
                maximum_active_requests = max(
                    maximum_active_requests,
                    active_requests,
                )
            sleep(0.05)
            with state_lock:
                active_requests -= 1
            return response_with(EMPTY_ATOM_FEED)

        first = ArxivSearcher()
        second = ArxivSearcher()
        first.session.get = Mock(side_effect=get_response)
        second.session.get = Mock(side_effect=get_response)

        with patch.object(ArxivSearcher, "_pace_locked"):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda item: item.search("test"),
                        [first, second],
                    )
                )

        self.assertEqual(results, [[], []])
        self.assertEqual(maximum_active_requests, 1)


class TestArxivSearcher(unittest.TestCase):
    def test_search(self):
        searcher = ArxivSearcher()
        papers = searcher.search("machine learning", max_results=10)
        print(f"Found {len(papers)} papers for query 'machine learning':")
        for i, paper in enumerate(papers, 1):
            print(f"{i}. {paper.title} (ID: {paper.paper_id})")
        if not papers:
            self.skipTest("arXiv API is unavailable or rate-limited")
        self.assertEqual(len(papers), 10)
        self.assertTrue(papers[0].title)

if __name__ == '__main__':
    unittest.main()
