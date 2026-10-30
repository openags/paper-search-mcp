"""Tests for the keyless ACM Digital Library connector (Crossref prefix 10.1145)."""
import asyncio
import threading
import unittest
import unittest.mock

from paper_search_mcp.academic_platforms.acm import ACMSearcher

CROSSREF_ITEM = {
    "DOI": "10.1145/3654522.3654560",
    "title": ["DATransformer: Deep Attention Transformer"],
    "author": [{"given": "Ada", "family": "Lovelace"}],
    "type": "proceedings-article",
    "published": {"date-parts": [[2024, 2, 23]]},
    "URL": "https://doi.org/10.1145/3654522.3654560",
}


def _crossref_response(items):
    response = unittest.mock.Mock(status_code=200)
    response.json.return_value = {"message": {"items": items}}
    response.raise_for_status.return_value = None
    return response


class TestACMSearch(unittest.TestCase):
    def test_search_restricts_to_acm_prefix_and_relabels(self):
        searcher = ACMSearcher()
        with unittest.mock.patch.object(
            searcher.session, "get", return_value=_crossref_response([CROSSREF_ITEM])
        ) as get:
            papers = searcher.search("transformer", max_results=1)

        self.assertEqual(get.call_args.kwargs["params"]["filter"], "prefix:10.1145")
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper.source, "acm")
        self.assertEqual(paper.url, "https://dl.acm.org/doi/10.1145/3654522.3654560")
        self.assertEqual(paper.pdf_url, "https://dl.acm.org/doi/pdf/10.1145/3654522.3654560")

    def test_lookup_rejects_non_acm_doi_before_request(self):
        searcher = ACMSearcher()
        with unittest.mock.patch.object(searcher.session, "get") as get:
            for doi in ("10.1109/5.771073", "https://doi.org/10.1000/test", "10.1145/"):
                with self.subTest(doi=doi), self.assertRaises(ValueError):
                    searcher.get_paper_by_doi(doi)
            get.assert_not_called()

    def test_lookup_normalizes_acm_doi(self):
        searcher = ACMSearcher()
        response = _crossref_response([])
        response.json.return_value = {"message": CROSSREF_ITEM}
        with unittest.mock.patch.object(searcher.session, "get", return_value=response) as get:
            paper = searcher.get_paper_by_doi("https://doi.org/10.1145/3654522.3654560")
        self.assertEqual(paper.source, "acm")
        self.assertTrue(get.call_args.args[0].endswith("/10.1145/3654522.3654560"))

    def test_search_merges_extra_filter(self):
        searcher = ACMSearcher()
        with unittest.mock.patch.object(
            searcher.session, "get", return_value=_crossref_response([])
        ) as get:
            searcher.search("x", filter="from-pub-date:2020")
        self.assertEqual(
            get.call_args.kwargs["params"]["filter"], "prefix:10.1145,from-pub-date:2020"
        )


class TestACMDownload(unittest.TestCase):
    def test_rejects_non_acm_doi(self):
        with self.assertRaises(ValueError):
            ACMSearcher().download_pdf("10.1109/5.771073")

    def test_blocked_download_points_to_browser_and_fallback(self):
        blocked = unittest.mock.Mock(status_code=403, content=b"<!DOCTYPE html>")
        with unittest.mock.patch(
            "paper_search_mcp.academic_platforms.acm.requests.get", return_value=blocked
        ):
            with self.assertRaises(IOError) as ctx:
                ACMSearcher().download_pdf("10.1145/3292500.3330701")
        message = str(ctx.exception)
        self.assertIn("https://dl.acm.org/doi/pdf/10.1145/3292500.3330701", message)
        self.assertIn("download_with_fallback", message)

    def test_successful_download_writes_pdf(self):
        import tempfile, os
        ok = unittest.mock.Mock(status_code=200, content=b"%PDF-1.7 test")
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch(
            "paper_search_mcp.academic_platforms.acm.requests.get", return_value=ok
        ):
            path = ACMSearcher().download_pdf("10.1145/3292500.3330701", tmp)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"%PDF-1.7 test")
            self.assertEqual(os.path.basename(path), "acm_10.1145_3292500.3330701.pdf")


class TestACMReadTool(unittest.TestCase):
    def test_read_runs_off_event_loop_thread(self):
        from paper_search_mcp import server
        caller_thread = threading.get_ident()
        def read(paper_id, save_path):
            self.assertNotEqual(threading.get_ident(), caller_thread)
            self.assertEqual((paper_id, save_path), ("10.1145/example", "/tmp/acm-test"))
            return "paper text"
        with unittest.mock.patch.object(server.acm_searcher, "read_paper", side_effect=read):
            result = asyncio.run(server.read_acm_paper("10.1145/example", "/tmp/acm-test"))
        self.assertEqual(result, "paper text")


class TestACMAlwaysEnabled(unittest.TestCase):
    def test_in_all_sources_without_key(self):
        import importlib
        import paper_search_mcp.server as srv_module
        importlib.reload(srv_module)
        self.assertIn("acm", srv_module.ALL_SOURCES)


if __name__ == "__main__":
    unittest.main()
