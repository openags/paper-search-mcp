import unittest
import asyncio
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from paper_search_mcp import server
from paper_search_mcp import cli


class TestDownloadWithFallback(unittest.TestCase):
    def test_repository_fallback_before_scihub(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=("/tmp/repo.pdf", ""))), \
             patch("paper_search_mcp.server.SciHubFetcher.download_pdf", side_effect=AssertionError("Sci-Hub should not be called")):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=True,
                )
            )
            self.assertEqual(result, "/tmp/repo.pdf")

    def test_unpaywall_fallback_before_repositories(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(side_effect=AssertionError("repositories should not be called"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value="https://example.org/oa.pdf"), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/unpaywall.pdf")):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=True,
                )
            )
            self.assertEqual(result, "/tmp/unpaywall.pdf")

    def test_no_scihub_returns_oa_chain_error(self):
        with patch.object(server.arxiv_searcher, "download_pdf", side_effect=Exception("primary failed")), \
             patch("paper_search_mcp.server._try_repository_fallback", new=AsyncMock(return_value=(None, "repo failed"))), \
             patch.object(server.unpaywall_resolver, "resolve_best_pdf_url", return_value=None):
            result = asyncio.run(
                server.download_with_fallback(
                    source="arxiv",
                    paper_id="1234.5678",
                    doi="10.1000/test",
                    title="test",
                    use_scihub=False,
                )
            )
            self.assertIn("OA fallback chain", result)

    def test_repository_fallback_handles_numeric_paper_id(self):
        paper = SimpleNamespace(paper_id=12345, pdf_url="https://example.org/paper.pdf")
        searcher = SimpleNamespace(search=lambda query, max_results=3: [paper])

        with patch.object(server, "openaire_searcher", searcher), \
             patch.object(server, "core_searcher", SimpleNamespace(search=lambda *args, **kwargs: [])), \
             patch.object(server, "europepmc_searcher", SimpleNamespace(search=lambda *args, **kwargs: [])), \
             patch.object(server, "pmc_searcher", SimpleNamespace(search=lambda *args, **kwargs: [])), \
             patch("paper_search_mcp.server._download_from_url", new=AsyncMock(return_value="/tmp/repo.pdf")) as download:
            result, error = asyncio.run(server._try_repository_fallback("10.1000/test", "title", "/tmp"))

        self.assertEqual(result, "/tmp/repo.pdf")
        self.assertEqual(error, "")
        self.assertEqual(download.call_args.args[2], "openaire_12345")

    def test_search_source_timeout_is_reported(self):
        async def slow_result():
            await asyncio.sleep(1)
            return []

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(cli._with_timeout(slow_result(), 0.01))

    def test_parse_sources_defaults_to_fast_set(self):
        cli.SEARCHERS.clear()
        selected = cli._parse_sources("fast")
        self.assertEqual(selected, [s for s in cli._fast_sources() if s in cli._available_sources()])
        self.assertEqual(cli.SEARCHERS, {})

        fastest = cli._parse_sources("fastest")
        self.assertEqual(fastest, [s for s in cli.FASTEST_SOURCES if s in cli._available_sources()])
        self.assertLess(len(fastest), len(selected))

        selected_from_all = cli._parse_sources("all")
        self.assertEqual(selected_from_all, selected)

        exhaustive = cli._parse_sources("all", exhaustive=True)
        self.assertIn("google_scholar", exhaustive)
        self.assertGreater(len(exhaustive), len(selected))

    def test_fast_sources_include_semantic_when_key_is_configured(self):
        with patch.dict("os.environ", {"PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "test-key"}):
            selected = cli._parse_sources("fast")

        self.assertIn("semantic", selected)

    def test_download_doi_cli_uses_fallback(self):
        args = SimpleNamespace(
            doi="10.1000/test",
            source="crossref",
            title="",
            save_path="/tmp",
            no_scihub=True,
            scihub_base_url="https://sci-hub.se",
        )
        with patch("paper_search_mcp.server.download_with_fallback", new=AsyncMock(return_value="/tmp/paper.pdf")):
            result = asyncio.run(cli.cmd_download_doi(args))

        self.assertEqual(result, 0)

    def test_metadata_dois_merges_parallel_source_results(self):
        crossref_paper = SimpleNamespace(
            to_dict=lambda: {
                "doi": "10.1000/test",
                "title": "Crossref Title",
                "authors": "Ada Lovelace",
                "abstract": "",
                "published_date": "2024-01-01T00:00:00",
                "url": "https://doi.org/10.1000/test",
                "pdf_url": "",
                "citations": 0,
                "categories": "",
                "keywords": "",
                "source": "crossref",
            }
        )
        openalex_paper = SimpleNamespace(
            to_dict=lambda: {
                "doi": "10.1000/test",
                "title": "OpenAlex Title",
                "authors": "Ada Lovelace; Grace Hopper",
                "abstract": "Useful abstract",
                "published_date": "",
                "url": "https://openalex.org/W1",
                "pdf_url": "https://example.org/paper.pdf",
                "citations": 42,
                "categories": "Ecology",
                "keywords": "",
                "source": "openalex",
            }
        )

        crossref = SimpleNamespace(get_paper_by_doi=lambda doi: crossref_paper)
        openalex = SimpleNamespace(get_paper_by_doi=lambda doi: openalex_paper)
        unpaywall = SimpleNamespace(resolver=SimpleNamespace(get_paper_by_doi=lambda doi: None))

        def fake_get_searcher(source):
            return {"crossref": crossref, "openalex": openalex, "unpaywall": unpaywall}[source]

        args = SimpleNamespace(
            dois=["10.1000/test"],
            input=None,
            output=None,
            sources="metadata",
            include_semantic=False,
            source_timeout=2,
        )

        with patch.object(cli, "_get_searcher", side_effect=fake_get_searcher), \
             patch.object(cli, "_available_sources", return_value=["crossref", "openalex", "unpaywall"]):
            result = asyncio.run(cli.cmd_metadata_dois(args))

        self.assertEqual(result, 0)

    def test_metadata_dois_adds_rank_fields_from_available_metadata(self):
        review_record = {
            "doi": "10.1000/review",
            "title": "A systematic review of ecological forecasting",
            "authors": "Ada Lovelace; Grace Hopper",
            "abstract": "This review synthesizes ecological forecasting studies.",
            "published_date": "2025-02-03",
            "url": "https://doi.org/10.1000/review",
            "pdf_url": "https://example.org/review.pdf",
            "citations": 125,
            "categories": "Ecology",
            "keywords": "forecasting; systematic review",
            "source": "openalex",
        }
        crossref_record = dict(review_record, source="crossref", pdf_url="", citations=80)

        crossref = SimpleNamespace(get_paper_by_doi=lambda doi: SimpleNamespace(to_dict=lambda: crossref_record))
        openalex = SimpleNamespace(get_paper_by_doi=lambda doi: SimpleNamespace(to_dict=lambda: review_record))
        unpaywall = SimpleNamespace(resolver=SimpleNamespace(get_paper_by_doi=lambda doi: None))

        def fake_get_searcher(source):
            return {"crossref": crossref, "openalex": openalex, "unpaywall": unpaywall}[source]

        args = SimpleNamespace(
            dois=["10.1000/review"],
            input=None,
            output=None,
            sources="metadata",
            include_semantic=False,
            source_timeout=2,
        )

        stdout = io.StringIO()
        with patch.object(cli, "_get_searcher", side_effect=fake_get_searcher), \
             patch.object(cli, "_available_sources", return_value=["crossref", "openalex", "unpaywall"]), \
             redirect_stdout(stdout):
            result = asyncio.run(cli.cmd_metadata_dois(args))

        self.assertEqual(result, 0)
        metadata = json.loads(stdout.getvalue())["results"][0]["metadata"]
        self.assertGreaterEqual(metadata["rank_score"], 80)
        self.assertIn("rank_reasons", metadata)
        self.assertTrue(any("review" in reason.lower() for reason in metadata["rank_reasons"]))
        self.assertTrue(any("open access" in reason.lower() or "pdf" in reason.lower() for reason in metadata["rank_reasons"]))
        self.assertEqual(
            sorted(metadata["rank_components"]),
            ["availability", "citation_signal", "literature_fit", "metadata_confidence", "recency"],
        )
        for score in metadata["rank_components"].values():
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)

    def test_metadata_dois_scores_pdf_availability_from_unpaywall(self):
        base_record = {
            "doi": "10.1000/unpaywall-only",
            "title": "Ecological model evaluation",
            "authors": "Ada Lovelace",
            "abstract": "A paper about model evaluation.",
            "published_date": "2024-01-01",
            "url": "https://doi.org/10.1000/unpaywall-only",
            "pdf_url": "",
            "citations": 12,
            "categories": "Ecology",
            "keywords": "models",
        }
        crossref_record = dict(base_record, source="crossref")
        openalex_record = dict(base_record, source="openalex")
        unpaywall_record = dict(base_record, source="unpaywall", pdf_url="https://example.org/oa.pdf")

        crossref = SimpleNamespace(get_paper_by_doi=lambda doi: SimpleNamespace(to_dict=lambda: crossref_record))
        openalex = SimpleNamespace(get_paper_by_doi=lambda doi: SimpleNamespace(to_dict=lambda: openalex_record))
        unpaywall = SimpleNamespace(resolver=SimpleNamespace(get_paper_by_doi=lambda doi: SimpleNamespace(to_dict=lambda: unpaywall_record)))

        def fake_get_searcher(source):
            return {"crossref": crossref, "openalex": openalex, "unpaywall": unpaywall}[source]

        args = SimpleNamespace(
            dois=["10.1000/unpaywall-only"],
            input=None,
            output=None,
            sources="metadata",
            include_semantic=False,
            source_timeout=2,
        )

        stdout = io.StringIO()
        with patch.object(cli, "_get_searcher", side_effect=fake_get_searcher), \
             patch.object(cli, "_available_sources", return_value=["crossref", "openalex", "unpaywall"]), \
             redirect_stdout(stdout):
            result = asyncio.run(cli.cmd_metadata_dois(args))

        self.assertEqual(result, 0)
        metadata = json.loads(stdout.getvalue())["results"][0]["metadata"]
        self.assertEqual(metadata["rank_components"]["availability"], 100)
        self.assertEqual(metadata["oa_pdf_sources"], ["unpaywall"])
        self.assertTrue(any("unpaywall" in reason.lower() for reason in metadata["rank_reasons"]))


if __name__ == "__main__":
    unittest.main()
