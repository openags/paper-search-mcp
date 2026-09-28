"""Tests for the keyless ACM Digital Library connector (Crossref prefix 10.1145)."""
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


class TestACMAlwaysEnabled(unittest.TestCase):
    def test_in_all_sources_without_key(self):
        import importlib
        import paper_search_mcp.server as srv_module
        importlib.reload(srv_module)
        self.assertIn("acm", srv_module.ALL_SOURCES)


if __name__ == "__main__":
    unittest.main()
