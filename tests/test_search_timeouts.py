import asyncio
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from paper_search_mcp import cli, cli_search, cli_search_worker, server
from paper_search_mcp.academic_platforms.iacr import IACRSearcher
from paper_search_mcp.academic_platforms.pubmed import PubMedSearcher


def test_search_papers_returns_partial_results_when_one_source_times_out():
    async def slow_search(*args, **kwargs):
        await asyncio.sleep(0.05)
        return [{"title": "slow", "paper_id": "slow-1"}]

    fast_result = [{"title": "fast", "paper_id": "fast-1"}]
    with (
        patch.object(server, "SEARCH_PAPERS_SOURCE_TIMEOUT_SECONDS", 0.01),
        patch.object(server, "search_arxiv", AsyncMock(side_effect=slow_search)),
        patch.object(server, "search_pubmed", AsyncMock(return_value=fast_result)),
    ):
        result = asyncio.run(
            server.search_papers(
                "test query",
                max_results_per_source=2,
                sources="arxiv,pubmed",
            )
        )

    assert result["source_results"] == {"arxiv": 0, "pubmed": 1}
    assert "timed out" in result["errors"]["arxiv"]
    assert result["total"] == 1
    assert result["papers"][0]["paper_id"] == "fast-1"


def test_search_papers_keeps_normal_source_results():
    result_row = [{"title": "paper a", "paper_id": "a"}]
    with patch.object(server, "search_arxiv", AsyncMock(return_value=result_row)):
        result = asyncio.run(
            server.search_papers(
                "test query",
                max_results_per_source=1,
                sources="arxiv",
            )
        )

    assert result["source_results"] == {"arxiv": 1}
    assert result["errors"] == {}
    assert result["papers"] == result_row


def test_google_scholar_tool_returns_results_before_timeout():
    expected = [{"paper_id": "1", "title": "paper"}]
    with patch.object(
        server, "async_search", AsyncMock(return_value=expected)
    ) as search:
        result = asyncio.run(
            server.search_google_scholar("machine learning", max_results=5)
        )

    assert result == expected
    search.assert_awaited_once_with(
        server.google_scholar_searcher,
        "machine learning",
        5,
        timeout_seconds=server.GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS - 1.0,
    )


def test_google_scholar_tool_reports_timeout_instead_of_empty_success():
    async def slow_search(*args, **kwargs):
        await asyncio.sleep(0.05)
        return [{"paper_id": "1", "title": "paper"}]

    with (
        patch.object(server, "GOOGLE_SCHOLAR_TOOL_TIMEOUT_SECONDS", 0.01),
        patch.object(server, "async_search", AsyncMock(side_effect=slow_search)),
    ):
        with pytest.raises(server.GoogleScholarSearchError, match="timed out after"):
            asyncio.run(server.search_google_scholar("machine learning", max_results=5))


def test_timed_out_blocking_search_keeps_capacity_until_worker_exits():
    started = Event()
    release = Event()
    finished = Event()
    executor = server._BoundedSearchExecutor(max_workers=1)

    class BlockingSearcher:
        def search(self, query, max_results):
            started.set()
            release.wait(timeout=2)
            finished.set()
            return [SimpleNamespace(to_dict=lambda: {"paper_id": "slow"})]

    class FastSearcher:
        @staticmethod
        def search(query, max_results):
            return [SimpleNamespace(to_dict=lambda: {"paper_id": "fast"})]

    try:
        with patch.object(server, "_SEARCH_EXECUTOR", executor):
            with pytest.raises(TimeoutError, match="timed out"):
                asyncio.run(
                    server._run_search_with_timeout(
                        "blocking",
                        server.async_search(BlockingSearcher(), "test", 1),
                        timeout_seconds=0.01,
                    )
                )

            assert started.wait(timeout=1)
            with pytest.raises(server.SearchExecutorSaturatedError):
                asyncio.run(server.async_search(FastSearcher(), "test", 1))

            release.set()
            assert finished.wait(timeout=1)

            deadline = time.monotonic() + 1
            while True:
                try:
                    result = asyncio.run(server.async_search(FastSearcher(), "test", 1))
                    break
                except server.SearchExecutorSaturatedError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)

            assert result == [{"paper_id": "fast"}]
    finally:
        release.set()
        executor.shutdown()


def test_pubmed_search_uses_bounded_request_timeout():
    response = Mock(content=b"<eSearchResult><IdList /></eSearchResult>")
    with patch(
        "paper_search_mcp.academic_platforms.pubmed.requests.get",
        return_value=response,
    ) as request:
        assert PubMedSearcher().search("test") == []

    assert request.call_args.kwargs["timeout"] == 30


def test_iacr_search_uses_bounded_request_timeout():
    searcher = IACRSearcher()
    response = Mock(status_code=503)
    searcher.session.get = Mock(return_value=response)

    assert searcher.search("test", fetch_details=False) == []
    assert searcher.session.get.call_args.kwargs["timeout"] == 30


# Offline CLI process deadlines must bound interpreter exit, not just coroutine return.
# Deliberately blocking workers below make no HTTP calls.
_CLI_WORKER = r'''
import json, os, signal, sys, time
from pathlib import Path
job = json.load(sys.stdin)
source = job["source"]
log = Path(sys.argv[1])
(log / (source + ".start")).write_text(json.dumps({"pid": os.getpid(), "time": time.monotonic(), "job": job}))
if source == "arxiv":
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
elif source == "semantic":
    print(json.dumps({"error": "Provider returned HTTP 429"}))
    sys.exit(1)
elif source == "core":
    print("not JSON")
    sys.exit(7)
elif source == "pubmed":
    # Much more than a pipe buffer: the parent must drain output concurrently.
    result = [{"paper_id": "pubmed-1", "title": "large", "abstract": "x" * 300000}]
elif source == "flood":
    print("x" * 300000, flush=True)
    time.sleep(30)
else:
    time.sleep(0.12)
    result = [{"paper_id": source + "-1", "title": source, "source": "provider-label", "citations": 2}]
(log / (source + ".end")).write_text(str(time.monotonic()))
print(json.dumps(result))
'''


def _source_job(source):
    return {"source": source, "query": "a unicode query: 科学", "max_results": 2, "kwargs": {}}


@pytest.fixture
def workers(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_search, "_worker_command", lambda: [sys.executable, "-c", _CLI_WORKER, str(tmp_path)])
    return tmp_path


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "1e309", "nope"])
def test_source_timeout_requires_positive_finite_seconds(value):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["search", "test", "--source-timeout=" + value])
    assert exc.value.code == 2


def test_source_timeout_is_opt_in():
    assert cli.build_parser().parse_args(["search", "test"]).source_timeout is None
    assert cli.build_parser().parse_args(["search", "test", "--source-timeout", "0.5"]).source_timeout == 0.5


def test_timeout_keeps_results_labels_errors_and_cli_exit(workers):
    # main() includes asyncio.run shutdown and interpreter teardown. A coroutine
    # timing test alone misses ThreadPoolExecutor's wait-at-exit behavior.
    script = (
        "import sys\nfrom paper_search_mcp import cli, cli_search\n"
        f"cli_search._worker_command = lambda: [sys.executable, '-c', {_CLI_WORKER!r}, {str(workers)!r}]\n"
        "cli.main()\n"
    )
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script, "search", "a unicode query: 科学",
         "-s", "arxiv,openalex,semantic,core,pubmed", "-n", "2", "--source-timeout", "1.5"],
        capture_output=True, text=True, timeout=10,
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert elapsed < 8  # The blocking provider would still be sleeping for 30s.
    output = json.loads(result.stdout)
    assert set(output) == {"query", "sources_used", "source_results", "errors", "total", "papers"}
    assert output["sources_used"] == ["arxiv", "openalex", "semantic", "core", "pubmed"]
    assert output["source_results"] == {"arxiv": 0, "openalex": 1, "semantic": 0, "core": 0, "pubmed": 1}
    assert output["errors"]["arxiv"] == "search for source 'arxiv' timed out after 1.5 seconds"
    assert output["errors"]["semantic"] == "Provider returned HTTP 429"
    assert "exited with code 7" in output["errors"]["core"]
    assert output["total"] == 2
    assert output["papers"][0]["source"] == "provider-label"
    assert output["papers"][1]["source"] == "pubmed"
    assert output["papers"][1]["abstract"] == "x" * 300000
    assert not (workers / "arxiv.end").exists()


def test_concurrency_is_bounded_and_queue_wait_gets_its_own_budget(workers, monkeypatch):
    monkeypatch.setattr(cli_search, "MAX_SEARCH_WORKERS", 2)
    sources = ["openalex", "crossref", "biorxiv", "medrxiv", "hal", "dblp"]
    results = asyncio.run(cli_search.search_sources([_source_job(source) for source in sources], 3))
    assert [result[0]["paper_id"] for result in results] == [source + "-1" for source in sources]
    events = []
    for source in sources:
        record = json.loads((workers / (source + ".start")).read_text())
        assert record["job"] == _source_job(source)
        events += [(record["time"], 1), (float((workers / (source + ".end")).read_text()), -1)]
    active = peak = 0
    for _, delta in sorted(events):
        active += delta
        peak = max(peak, active)
        assert 0 <= active <= 2
    assert peak == 2
    assert active == 0


def test_timeout_reaps_worker_before_return_and_reuses_capacity(workers, monkeypatch):
    monkeypatch.setattr(cli_search, "MAX_SEARCH_WORKERS", 1)
    processes = []
    original = asyncio.create_subprocess_exec

    async def track(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", track)
    results = asyncio.run(cli_search.search_sources([_source_job("arxiv"), _source_job("openalex")], 1.5))
    assert isinstance(results[0], TimeoutError)
    assert results[1][0]["paper_id"] == "openalex-1"
    assert len(processes) == 2
    assert all(process.returncode is not None for process in processes)
    assert processes[0].returncode != 0
    assert processes[1].returncode == 0


def test_cancellation_during_creation_reaps_worker(workers, monkeypatch):
    processes = []
    original = asyncio.create_subprocess_exec

    async def exercise():
        created = asyncio.Event()
        release = asyncio.Event()

        async def delayed(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            created.set()
            await release.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
        task = asyncio.create_task(cli_search.search_sources([_source_job("arxiv")], 10))
        await created.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert len(processes) == 1
    assert processes[0].returncode is not None


def test_cancelled_running_search_reaps_workers_and_does_not_start_queued(workers, monkeypatch):
    monkeypatch.setattr(cli_search, "MAX_SEARCH_WORKERS", 1)
    processes = []
    original = asyncio.create_subprocess_exec

    async def track(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", track)

    async def exercise():
        task = asyncio.create_task(cli_search.search_sources([_source_job("arxiv"), _source_job("openalex")], 10))
        deadline = time.monotonic() + 5
        while not (workers / "arxiv.start").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert len(processes) == 1
    assert processes[0].returncode is not None
    assert not (workers / "openalex.start").exists()


def test_cleanup_drains_full_pipe_after_reader_is_cancelled(workers):
    async def exercise():
        process = await asyncio.create_subprocess_exec(
            *cli_search._worker_command(), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        communication = asyncio.create_task(process.communicate(json.dumps(_source_job("flood")).encode()))
        deadline = time.monotonic() + 5
        while not (workers / "flood.start").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        communication.cancel()
        await asyncio.gather(communication, return_exceptions=True)
        # Let output fill the transport after its reader has been cancelled.
        await asyncio.sleep(0.1)
        await asyncio.wait_for(cli_search._kill_and_reap(process, communication), 3)
        assert process.returncode is not None

    asyncio.run(exercise())


def test_worker_preserves_kwargs_and_redirects_diagnostics(monkeypatch, capsys):
    request = _source_job("scopus")
    request["kwargs"] = {"view": "COMPLETE", "sort": "coverDate", "field": "TITLE", "date": "2024"}

    def search(query, max_results, **kwargs):
        assert query == request["query"]
        assert max_results == 2
        assert kwargs == request["kwargs"]
        print("provider diagnostic")
        return [SimpleNamespace(to_dict=lambda: {"title": "paper", "source": "original"})]

    factory = Mock(return_value=SimpleNamespace(search=search))
    monkeypatch.setattr(cli, "_get_searcher", factory)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    with pytest.raises(SystemExit) as exc:
        cli_search_worker.main()
    assert exc.value.code == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == [{"title": "paper", "source": "original"}]
    assert out.err == "provider diagnostic\n"
    factory.assert_called_once_with("scopus")


def test_worker_construction_failure_is_a_source_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_get_searcher", Mock(side_effect=RuntimeError("missing configuration")))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_source_job("semantic"))))
    with pytest.raises(SystemExit) as exc:
        cli_search_worker.main()
    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out) == {"error": "missing configuration"}


def test_real_worker_module_can_run_from_another_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # Unknown source fails during construction, before making any network call.
    result = subprocess.run(
        cli_search._worker_command(), input=json.dumps(_source_job("unknown-source")),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": "'unknown-source'"}


def test_spawn_failure_is_reported_per_source(monkeypatch):
    async def cannot_spawn(*args, **kwargs):
        raise OSError("cannot start worker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", cannot_spawn)
    results = asyncio.run(cli_search.search_sources([_source_job("openalex"), _source_job("crossref")], 2))
    assert [str(result) for result in results] == ["cannot start worker", "cannot start worker"]


@pytest.mark.skipif(sys.platform == "win32", reason="Uses POSIX SIGINT delivery; cancellation is tested on all platforms")
def test_ctrl_c_reaps_worker_and_exits_even_with_large_output(workers):
    script = (
        "import asyncio, signal, sys\nfrom paper_search_mcp import cli_search\n"
        # A background test runner may inherit SIG_IGN from its shell. Emulate
        # the foreground CLI's normal handler before asyncio.run takes over.
        "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
        f"cli_search._worker_command = lambda: [sys.executable, '-c', {_CLI_WORKER!r}, {str(workers)!r}]\n"
        f"asyncio.run(cli_search.search_sources([{_source_job('flood')!r}], 20))\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    worker_pid = None
    try:
        deadline = time.monotonic() + 5
        while not (workers / "flood.start").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        worker_pid = json.loads((workers / "flood.start").read_text())["pid"]
        process.send_signal(signal.SIGINT)
        process.communicate(timeout=5)
        assert process.returncode != 0
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
        # Keep a failure in this regression test from leaving its own workers.
        if worker_pid is not None:
            try:
                os.kill(worker_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="Linux file-descriptor accounting")
def test_repeated_searches_close_pipe_descriptors(workers):
    before = len(list(Path("/proc/self/fd").iterdir()))
    for _ in range(4):
        results = asyncio.run(cli_search.search_sources([_source_job("pubmed")], 3))
        assert results[0][0]["paper_id"] == "pubmed-1"
    assert len(list(Path("/proc/self/fd").iterdir())) == before


def test_timeout_path_passes_source_options_without_constructing_parent_searchers(monkeypatch, capsys):
    async def search(jobs, timeout):
        assert timeout == 5
        assert jobs == [
            {"source": "semantic", "query": "test", "max_results": 2, "kwargs": {"year": "2020"}},
            {"source": "wos", "query": "test", "max_results": 2, "kwargs": {"db": "WOK"}},
            {"source": "scopus", "query": "test", "max_results": 2,
             "kwargs": {"view": "COMPLETE", "sort": "coverDate", "field": "TITLE", "date": "2024"}},
        ]
        return [[], [], []]

    monkeypatch.setattr(cli_search, "search_sources", search)
    monkeypatch.setattr(cli, "_get_searcher", Mock(side_effect=AssertionError("parent source constructed")))
    args = cli.build_parser().parse_args([
        "search", "test", "-s", "semantic,wos,scopus", "-n", "2", "--source-timeout", "5",
        "--year", "2020", "--wos-db", "WOK", "--scopus-view", "COMPLETE",
        "--scopus-sort", "coverDate", "--scopus-field", "TITLE", "--scopus-date", "2024",
    ])
    assert asyncio.run(cli.cmd_search(args)) == 0
    assert json.loads(capsys.readouterr().out)["errors"] == {}
