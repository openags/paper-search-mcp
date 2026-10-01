import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
import sqlite3
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from paper_search_mcp import server
from paper_search_mcp.search_cache import CacheSettings, SearchCache, search_key


@pytest.fixture
def cache(tmp_path):
    return SearchCache(CacheSettings(enabled=True, path=tmp_path / "cache" / "search.sqlite3"))


def write(cache, key="key", value=None):
    _, generation = cache.lookup(key)
    assert cache.put(key, value or [{"title": key}], generation)


class Provider:
    search_cache_public = True
    def __init__(self):
        self.calls = 0

    def search(self, query, max_results=10, **kwargs):
        self.calls += 1
        return [SimpleNamespace(to_dict=lambda: {"title": query, "authors": "Alice; Bob", "references": "W1; W2"})]


def test_disabled_default_never_creates_paths(tmp_path):
    path = tmp_path / "missing" / "cache.sqlite3"
    cache = SearchCache(CacheSettings(path=path))
    assert cache.lookup("private query") == (None, None)
    assert not cache.put("private query", [{"title": "private"}], 0)
    assert cache.status()["enabled"] is False
    assert cache.clear()["cleared"] == 0
    assert not path.parent.exists()


def test_default_environment_disabled_and_explicit_enable(tmp_path):
    with patch.dict(os.environ, {"PAPER_SEARCH_MCP_ENV_FILE": "/missing.env"}, clear=True):
        assert not CacheSettings.from_env().enabled
        os.environ["PAPER_SEARCH_MCP_SEARCH_CACHE_ENABLED"] = "true"
        os.environ["PAPER_SEARCH_MCP_SEARCH_CACHE_PATH"] = str(tmp_path / "cache.db")
        os.environ["PAPER_SEARCH_MCP_SEARCH_CACHE_TTL_SECONDS"] = "120"
        settings = CacheSettings.from_env()
        assert settings.enabled and settings.ttl_seconds == 120
        os.environ["PAPER_SEARCH_MCP_SEARCH_CACHE_ENABLED"] = "false"
        assert not CacheSettings.from_env().enabled


@pytest.mark.parametrize("values", [{"ttl_seconds": 0}, {"max_entries": 0}, {"max_bytes": 10},
    {"max_entries": 10001}, {"ttl_seconds": 2592001}, {"ttl_seconds": True}])
def test_limits_are_validated(values):
    with pytest.raises(ValueError):
        CacheSettings(**values)


def test_invalid_environment_disables_cache_without_breaking_startup():
    with patch.dict(os.environ, {"PAPER_SEARCH_MCP_SEARCH_CACHE_ENABLED": "maybe"}, clear=True):
        assert not SearchCache.from_env().settings.enabled


def test_canonical_keys_isolate_source_options_limit_case_and_delimiters():
    class OtherProvider(Provider):
        pass
    provider = Provider()
    baseline = search_key(provider, "Q", 10, {"filter": "year:2024", "sort": "date"})
    assert baseline == search_key(provider, "Q", 10, {"sort": "date", "filter": "year:2024"})
    assert len({baseline, search_key(provider, "q", 10, {}),
        search_key(provider, "Q", 11, {"filter": "year:2024", "sort": "date"}),
        search_key(OtherProvider(), "Q", 10, {"filter": "year:2024", "sort": "date"}),
        search_key(provider, "Q", 10, {"filter": "year:2025", "sort": "date"}),
        search_key(provider, "Q|a", 10, {"filter": "b"}),
        search_key(provider, "Q", 10, {"filter": "a|b"})}) == 7
    assert search_key(provider, "Q", 10, {"year": 2024}) != search_key(provider, "Q", 10, {"year": "2024"})


def test_authenticated_provider_is_bypassed_without_storing_credentials():
    provider = Provider()
    provider.api_key = "private-credential-one"
    assert search_key(provider, "Q", 10, {}) is None
    provider.api_key = ""
    provider.email = "researcher-one@example.org"
    first = search_key(provider, "Q", 10, {})
    provider.email = "researcher-two@example.org"
    second = search_key(provider, "Q", 10, {})
    assert first != second
    assert "researcher-" not in first + second


@pytest.mark.parametrize("credential", ["api_key", "wos_api_key", "scopus_api_key", "access_token", "token", "password", "inst_token"])
def test_credentialed_and_institutional_sources_bypass_cache(credential):
    provider = Provider()
    setattr(provider, credential, "private")
    assert search_key(provider, "Q", 10, {}) is None
    class Institutional:
        pass
    Institutional.__module__ = "paper_search_mcp.academic_platforms.scopus"
    assert search_key(Institutional(), "Q", 10, {}) is None


def test_session_auth_and_nonpublic_cookies_bypass_cache():
    import requests
    provider = Provider()
    provider.session = requests.Session()
    provider.session.headers["Authorization"] = "Bearer private"
    assert search_key(provider, "Q", 10, {}) is None
    del provider.session.headers["Authorization"]
    provider.session.auth = ("user", "password")
    assert search_key(provider, "Q", 10, {}) is None
    provider.session.auth = None
    provider.session.cookies.set("sessionid", "private")
    assert search_key(provider, "Q", 10, {}) is None



def test_query_keys_do_not_use_hash_as_sole_identity():
    digest = SimpleNamespace(hexdigest=lambda: "collision")
    with patch("paper_search_mcp.search_cache.hashlib.sha256", return_value=digest):
        assert search_key(Provider(), "query A", 10, {}) != search_key(Provider(), "query B", 10, {})


def test_unsupported_and_oversized_keys_bypass_cache():
    assert search_key(Provider(), "x" * 20000, 10, {}) is None
    assert search_key(Provider(), "x", 10, {"object": object()}) is None
    assert search_key(Provider(), "x", 10, {"float": float("nan")}) is None


def test_hit_is_independent_copy_and_survives_new_cache_instance(cache):
    write(cache, value=[{"title": "original", "authors": "Alice; Bob", "references": "W1; W2"}])
    hit, _ = cache.lookup("key")
    hit[0]["title"] = "mutated"
    hit2, _ = SearchCache(cache.settings).lookup("key")
    assert hit2[0] == {"title": "original", "authors": "Alice; Bob", "references": "W1; W2"}


def test_ttl_boundary_and_no_refresh_on_read(cache):
    with patch("paper_search_mcp.search_cache.time.time", return_value=100):
        write(cache)
    with patch("paper_search_mcp.search_cache.time.time", return_value=100 + cache.settings.ttl_seconds - 1):
        assert cache.lookup("key")[0] is not None
    with patch("paper_search_mcp.search_cache.time.time", return_value=100 + cache.settings.ttl_seconds):
        assert cache.lookup("key")[0] is None


def test_shorter_ttl_and_clock_rollback_do_not_resurrect_stale_entries(cache):
    with patch("paper_search_mcp.search_cache.time.time", return_value=100):
        write(cache)
    with patch("paper_search_mcp.search_cache.time.time", return_value=111):
        shortened = SearchCache(replace(cache.settings, ttl_seconds=10))
        assert shortened.lookup("key")[0] is None
    with patch("paper_search_mcp.search_cache.time.time", return_value=200):
        write(cache)
    with patch("paper_search_mcp.search_cache.time.time", return_value=199):
        assert cache.lookup("key")[0] is None


def test_bounded_entries_evict_oldest_deterministically(cache):
    cache = SearchCache(replace(cache.settings, max_entries=2))
    for i in range(3):
        with patch("paper_search_mcp.search_cache.time.time", return_value=100 + i):
            write(cache, f"key{i}")
    with patch("paper_search_mcp.search_cache.time.time", return_value=103):
        assert cache.lookup("key0")[0] is None
        assert cache.lookup("key1")[0] and cache.lookup("key2")[0]
        assert cache.status()["entries"] == 2


def test_physical_database_and_payload_bytes_stay_bounded(cache):
    cache = SearchCache(replace(cache.settings, max_bytes=1024 * 1024))
    for i in range(15):
        write(cache, str(i), [{"title": str(i), "abstract": "x" * 200000}])
    assert cache.settings.path.stat().st_size <= cache.settings.max_bytes
    with sqlite3.connect(cache.settings.path) as con:
        assert con.execute("SELECT SUM(size) FROM cache_entries").fetchone()[0] <= cache.settings.max_bytes // 2
    _, generation = cache.lookup("oversized")
    assert not cache.put("oversized", [{"abstract": "x" * 300000}], generation)


def test_clear_works_when_disabled_and_invalidates_inflight_generation(cache):
    write(cache)
    _, old_generation = cache.lookup("late")
    disabled = SearchCache(replace(cache.settings, enabled=False))
    assert disabled.clear() == {"enabled": False, "cleared": 1}
    assert not cache.put("late", [{"title": "late"}], old_generation)
    assert cache.lookup("key")[0] is None
    assert cache.status()["entries"] == 0


def test_concurrent_threads_and_instances_are_consistent(cache):
    cache.lookup("initialize")
    def run(i):
        other = SearchCache(cache.settings)
        _, generation = other.lookup(f"key{i}")
        assert other.put(f"key{i}", [{"title": f"value{i}"}], generation)
        assert other.lookup(f"key{i}")[0] == [{"title": f"value{i}"}]
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(run, range(20)))
    assert cache.status()["entries"] == 20


def test_lock_contention_is_bounded_and_fails_open(cache):
    write(cache)
    with sqlite3.connect(cache.settings.path) as con:
        con.execute("BEGIN EXCLUSIVE")
        assert cache.lookup("key") == (None, None)
        assert not cache.put("key", [{"title": "new"}], 0)
    assert cache.lookup("key")[0] == [{"title": "key"}]


def test_corrupt_cache_and_unrelated_database_are_not_overwritten(cache):
    path = cache.settings.path
    path.parent.mkdir()
    original = b"not a database"
    path.write_bytes(original)
    assert cache.lookup("x") == (None, None)
    assert not cache.status()["available"]
    with pytest.raises(RuntimeError, match="Unable to clear"):
        cache.clear()
    assert path.read_bytes() == original
    path.unlink()
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE user_data (value TEXT)")
        con.execute("INSERT INTO user_data VALUES ('keep me')")
    assert cache.lookup("x") == (None, None)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT value FROM user_data").fetchone()[0] == "keep me"


def test_invalid_payload_is_a_miss_and_logs_do_not_leak_queries(cache, caplog):
    write(cache, "sensitive query")
    with sqlite3.connect(cache.settings.path) as con:
        con.execute("UPDATE cache_entries SET payload='not JSON'")
    assert cache.lookup("sensitive query") == (None, None)
    assert "sensitive query" not in caplog.text


def test_new_cache_files_have_private_permissions(cache):
    write(cache)
    if os.name == "posix":
        assert cache.settings.path.stat().st_mode & 0o777 == 0o600
        assert cache.settings.path.parent.stat().st_mode & 0o777 == 0o700


def test_async_search_cache_hit_preserves_serialization(cache):
    provider = Provider()
    with patch.object(server, "_SEARCH_CACHE", cache):
        first = asyncio.run(server.async_search(provider, "query", 10, filter="x"))
        second = asyncio.run(server.async_search(provider, "query", 10, filter="x"))
        assert first == second and provider.calls == 1
        asyncio.run(server.async_search(provider, "query", 11, filter="x"))
        assert provider.calls == 2


def test_provider_failures_and_empty_results_are_not_cached(cache):
    provider = Provider()
    provider.search = Mock(side_effect=RuntimeError("upstream failure"))
    with patch.object(server, "_SEARCH_CACHE", cache):
        for _ in range(2):
            with pytest.raises(RuntimeError, match="upstream failure"):
                asyncio.run(server.async_search(provider, "query", 10))
        assert provider.search.call_count == 2
        provider.search = Mock(return_value=[])
        assert asyncio.run(server.async_search(provider, "query", 10)) == []
        assert asyncio.run(server.async_search(provider, "query", 10)) == []
        assert provider.search.call_count == 2
    assert cache.status()["entries"] == 0


def test_clear_during_async_search_prevents_late_repopulation(cache):
    started, release = Event(), Event()
    provider = Provider()
    def search(*args, **kwargs):
        started.set()
        release.wait(timeout=2)
        return [SimpleNamespace(to_dict=lambda: {"title": "late"})]
    provider.search = search
    async def run():
        task = asyncio.create_task(server.async_search(provider, "query", 10))
        await asyncio.to_thread(started.wait, 1)
        await server.clear_search_cache()
        release.set()
        assert await task == [{"title": "late"}]
    with patch.object(server, "_SEARCH_CACHE", cache):
        asyncio.run(run())
    assert cache.status()["entries"] == 0


def test_cancelled_search_does_not_cache_late_provider_result(cache):
    started, release, finished = Event(), Event(), Event()
    provider = Provider()
    def search(*args, **kwargs):
        started.set()
        release.wait(timeout=2)
        finished.set()
        return [SimpleNamespace(to_dict=lambda: {"title": "late"})]
    provider.search = search
    async def run():
        task = asyncio.create_task(server.async_search(provider, "query", 10))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
    with patch.object(server, "_SEARCH_CACHE", cache):
        asyncio.run(run())
    assert cache.status()["entries"] == 0


def test_mcp_cache_tools_and_annotations(cache):
    with patch.object(server, "_SEARCH_CACHE", cache):
        assert asyncio.run(server.get_search_cache_status())["enabled"]
        assert asyncio.run(server.clear_search_cache())["cleared"] == 0
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    assert tools["get_search_cache_status"].annotations.readOnlyHint
    assert tools["clear_search_cache"].annotations.destructiveHint


def _process_cache_writer(args):
    path, number = args
    cache = SearchCache(CacheSettings(enabled=True, path=path))
    key = f"process-{number}"
    _, generation = cache.lookup(key)
    success = cache.put(key, [{"title": key}], generation)
    return success, cache.lookup(key)[0]


def test_separate_processes_share_transactional_cache(cache):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    write(cache, "initial")
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_process_cache_writer, [(cache.settings.path, i) for i in range(9)]))
    assert results == [(True, [{"title": f"process-{i}"}]) for i in range(9)]
    assert cache.status()["entries"] == 10


def test_disabled_search_does_not_read_or_write_existing_cache(cache):
    write(cache, "existing")
    disabled = SearchCache(replace(cache.settings, enabled=False))
    provider = Provider()
    with patch.object(server, "_SEARCH_CACHE", disabled), patch.object(disabled, "lookup") as lookup, patch.object(disabled, "put") as put:
        asyncio.run(server.async_search(provider, "query", 10))
        asyncio.run(server.async_search(provider, "query", 10))
        lookup.assert_not_called()
        put.assert_not_called()
    assert provider.calls == 2


def test_corrupt_cache_does_not_fail_search(cache):
    cache.settings.path.parent.mkdir()
    cache.settings.path.write_bytes(b"corrupt")
    provider = Provider()
    with patch.object(server, "_SEARCH_CACHE", cache):
        assert asyncio.run(server.async_search(provider, "query", 10))[0]["title"] == "query"
    assert provider.calls == 1


def test_clear_can_empty_database_after_disk_limit_is_lowered(cache):
    for i in range(20):
        write(cache, str(i), [{"abstract": "x" * 200000}])
    assert cache.settings.path.stat().st_size > 1024 * 1024
    smaller = SearchCache(replace(cache.settings, max_bytes=1024 * 1024))
    assert smaller.lookup("key") == (None, None)
    assert smaller.clear()["cleared"] == 20
    assert smaller.status()["entries"] == 0


def test_dynamic_semantic_key_is_not_cached():
    from paper_search_mcp.academic_platforms.semantic import SemanticSearcher
    with patch.dict(os.environ, {"PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY": "private"}):
        assert search_key(SemanticSearcher(), "Q", 10, {}) is None


def test_public_builtin_without_credentials_is_cacheable():
    from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher
    assert search_key(OpenAlexSearcher(api_key="", email=""), "Q", 10, {}) is not None
