"""Optional local search-result cache. Disabled until the operator opts in."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import time

from .config import get_env

logger = logging.getLogger(__name__)
_APPLICATION_ID = 0x50534D43
_SCHEMA_VERSION = 1
_MAX_KEY_BYTES = 16384
_MAX_ENTRY_BYTES = 1024 * 1024


# New/institutional connectors are excluded until explicitly reviewed as public.
_PUBLIC_CONNECTORS = {
    "arxiv", "pubmed", "biorxiv", "medrxiv", "google_scholar", "iacr",
    "semantic", "crossref", "openalex", "pmc", "core", "europepmc", "dblp",
    "openaire", "citeseerx", "doaj", "base_search", "zenodo", "hal", "ssrn",
    "unpaywall", "acm",
}


def _ambient_netrc_possible():
    """Bypass ambient Requests auth without reading credential file contents.

    Several connectors use module-level Requests calls, so inspecting a
    connector's explicit key/session alone cannot establish anonymous access.
    A present netrc file is conservatively sufficient to disable caching.
    """
    configured = os.environ.get("NETRC")
    locations = (configured,) if configured is not None else ("~/.netrc", "~/_netrc")
    try:
        return any(os.path.exists(os.path.expanduser(path)) for path in locations)
    except (OSError, RuntimeError):
        return True


def _public_cache_eligible(searcher):
    module = type(searcher).__module__
    built_in = any(module == f"paper_search_mcp.academic_platforms.{name}"
                   for name in _PUBLIC_CONNECTORS)
    if not built_in and getattr(type(searcher), "search_cache_public", False) is not True:
        return False
    if _ambient_netrc_possible():
        return False
    if module == "paper_search_mcp.academic_platforms.semantic" and get_env("SEMANTIC_SCHOLAR_API_KEY", ""):
        return False  # This connector reads its key dynamically per request.
    # Public API metadata may still differ with credentials/permissions. Until a
    # principal-aware connector cache contract exists, bypass authenticated use.
    for name, value in vars(searcher).items():
        lowered = name.lower().lstrip("_")
        if value and (lowered in {"api_key", "access_token", "token", "password", "username",
                                 "client_secret", "api_token", "inst_token", "insttoken", "proxy_url"}
                      or lowered.endswith(("_api_key", "_access_token", "_password"))):
            return False
    session = getattr(searcher, "session", None)
    if session is not None:
        if getattr(session, "auth", None):
            return False
        if any(str(key).lower() in {"authorization", "x-api-key", "api-key"} and value
               for key, value in session.headers.items()):
            return False
        if any(cookie.name != "CONSENT" for cookie in getattr(session, "cookies", [])):
            return False
    return True


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def search_key(searcher, query, max_results, options):
    """Canonical keys do not concatenate ambiguous delimiters or case-fold queries.

    The complete query/options key is stored only in the opt-in database. Secret
    configuration is represented by a digest, never the raw value. JSON types
    (including strings versus numbers) remain distinct.
    """
    if not _public_cache_eligible(searcher):
        return None
    config = {
        name: getattr(searcher, name, None)
        for name in ("api_key", "email", "proxy_url", "base_url", "endpoint")
    }
    resolver = getattr(searcher, "resolver", None)
    if resolver is not None:
        config["resolver_email"] = getattr(resolver, "email", None)
    session = getattr(searcher, "session", None)
    if session is not None:
        config["headers"] = {
            str(k).lower(): v for k, v in session.headers.items()
            if str(k).lower() in {"authorization", "x-api-key", "api-key"}
        }
    # Include unified configuration and recognized legacy credential names.
    config["environment"] = {
        key: value for key, value in os.environ.items()
        if (key.startswith("PAPER_SEARCH_MCP_") and "SEARCH_CACHE" not in key)
        or key in {"SEMANTIC_SCHOLAR_API_KEY", "OPENALEX_API_KEY", "OPENALEX_EMAIL",
                   "CORE_API_KEY", "UNPAYWALL_EMAIL", "DOAJ_API_KEY", "ZENODO_ACCESS_TOKEN",
                   "GOOGLE_SCHOLAR_PROXY_URL", "OPENAIRE_API_KEY", "CITESEERX_API_KEY", "IEEE_API_KEY"}
    }
    config["endpoints"] = {
        name: getattr(searcher, name) for name in dir(type(searcher))
        if name.isupper() and name.endswith(("URL", "ENDPOINT"))
        and isinstance(getattr(searcher, name), str)
    }
    try:
        fingerprint = hashlib.sha256(_json(config).encode("utf-8")).hexdigest()
        key = _json({"version": _SCHEMA_VERSION,
                     "source": f"{type(searcher).__module__}.{type(searcher).__qualname__}",
                     "configuration": fingerprint, "query": query,
                     "max_results": max_results, "options": options})
    except (TypeError, ValueError):
        return None
    return key if len(key.encode("utf-8")) <= _MAX_KEY_BYTES else None


@dataclass(frozen=True)
class CacheSettings:
    enabled: bool = False
    path: Path = Path.home() / ".cache" / "paper-search-mcp" / "search.sqlite3"
    ttl_seconds: int = 86400
    max_entries: int = 1000
    max_bytes: int = 16 * 1024 * 1024

    def __post_init__(self):
        for name, minimum, maximum in (("ttl_seconds", 1, 30 * 86400),
                                       ("max_entries", 1, 10000),
                                       ("max_bytes", 1024 * 1024, 512 * 1024 * 1024)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")

    @classmethod
    def from_env(cls):
        enabled = get_env("SEARCH_CACHE_ENABLED", "0").strip().lower()
        if enabled not in {"0", "1", "true", "false", "yes", "no", "on", "off", ""}:
            raise ValueError("SEARCH_CACHE_ENABLED must explicitly enable or disable caching")
        return cls(
            enabled=enabled in {"1", "true", "yes", "on"},
            path=Path(get_env("SEARCH_CACHE_PATH", str(cls.path))).expanduser(),
            ttl_seconds=int(get_env("SEARCH_CACHE_TTL_SECONDS", "86400")),
            max_entries=int(get_env("SEARCH_CACHE_MAX_ENTRIES", "1000")),
            max_bytes=int(get_env("SEARCH_CACHE_MAX_BYTES", str(16 * 1024 * 1024))),
        )


class SearchCache:
    """SQLite transactions coordinate threads/processes; failures fall back to search.

    Empty results are not cached because several legacy connectors still mask
    upstream failures as empty lists. Cache methods never log keys or payloads.
    """

    def __init__(self, settings):
        self.settings = settings

    @classmethod
    def from_env(cls):
        try:
            return cls(CacheSettings.from_env())
        except ValueError:
            logger.warning("Invalid search cache configuration; caching is disabled")
            return cls(CacheSettings())

    @contextmanager
    def _connection(self, create=False):
        path = self.settings.path
        if not path.exists():
            if not create:
                yield None
                return
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        if create and path.stat().st_size > self.settings.max_bytes:
            raise sqlite3.DatabaseError("cache exceeds configured disk limit")
        connection = sqlite3.connect(str(path), timeout=0.1)
        try:
            # A warm cache must not take a write lock merely to open it. In
            # particular, rewriting application_id/schema on every operation
            # adds fsyncs and can starve concurrent lookups of their 100 ms budget.
            app_id = connection.execute("PRAGMA application_id").fetchone()[0]
            if app_id != _APPLICATION_ID:
                if app_id:
                    raise sqlite3.DatabaseError("path is not a paper-search cache")
                if not create:
                    # Check ownership and schema in the same read snapshot.
                    connection.execute("BEGIN")
                    app_id = connection.execute("PRAGMA application_id").fetchone()[0]
                    tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    connection.commit()
                    if app_id != _APPLICATION_ID:
                        if app_id or tables:
                            raise sqlite3.DatabaseError("path is not a paper-search cache")
                        yield None
                        return
                else:
                    # Serialize first initialization across processes, then
                    # recheck: another initializer may have won while we waited.
                    connection.execute("BEGIN IMMEDIATE")
                    app_id = connection.execute("PRAGMA application_id").fetchone()[0]
                    if app_id != _APPLICATION_ID:
                        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                        if app_id or tables:
                            raise sqlite3.DatabaseError("path is not a paper-search cache")
                        connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                        connection.execute("""CREATE TABLE cache_entries (
                            key TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL,
                            payload TEXT NOT NULL, size INTEGER NOT NULL)""")
                        connection.execute("""CREATE TABLE cache_meta (
                            id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL)""")
                        connection.execute("INSERT INTO cache_meta VALUES (1, 0)")
                    connection.commit()
            connection.execute("PRAGMA secure_delete=ON")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={self.settings.max_bytes // page_size}")
            # Newly created SQLite files already use DELETE mode. Repeatedly
            # setting journal_mode can contend with active writers; only verify
            # it here, and reject externally changed modes rather than growing WAL.
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise sqlite3.DatabaseError("unsupported search cache journal mode")
            yield connection
        finally:
            connection.close()

    def lookup(self, key):
        if not self.settings.enabled or key is None:
            return None, None
        try:
            with self._connection(create=True) as con:
                # Read value and generation from one snapshot without reserving
                # a write lock. A concurrent clear still invalidates late puts.
                # Expired rows are excluded here and pruned by the next put.
                con.execute("BEGIN")
                now = time.time()
                generation = con.execute("SELECT generation FROM cache_meta WHERE id=1").fetchone()[0]
                row = con.execute(
                    "SELECT payload FROM cache_entries WHERE key=? AND expires > ? "
                    "AND created <= ? AND created + ? > ?",
                    (key, now, now, self.settings.ttl_seconds, now),
                ).fetchone()
                con.commit()
                if row is None:
                    return None, generation
                payload = json.loads(row[0])
                if not isinstance(payload, list) or not payload or not all(isinstance(item, dict) for item in payload):
                    return None, generation
                return payload, generation
        except (OSError, sqlite3.Error, ValueError, TypeError):
            logger.warning("Search cache read unavailable; using provider")
            return None, None

    def put(self, key, papers, generation):
        if not self.settings.enabled or key is None or generation is None or not papers:
            return False
        try:
            payload = _json(papers)
            size = len(key.encode("utf-8")) + len(payload.encode("utf-8"))
            if size > min(_MAX_ENTRY_BYTES, self.settings.max_bytes // 4):
                return False
            with self._connection(create=True) as con:
                con.execute("BEGIN IMMEDIATE")
                current = con.execute("SELECT generation FROM cache_meta WHERE id=1").fetchone()[0]
                if current != generation:
                    return False  # A clear during the network call wins over late writes.
                now = time.time()
                con.execute("DELETE FROM cache_entries WHERE expires <= ? OR created > ? OR created + ? <= ? OR key=?",
                            (now, now, self.settings.ttl_seconds, now, key))
                # Keep payload + keys under half the disk cap; the rest is SQLite
                # B-tree/page overhead. max_page_count is the physical backstop.
                while True:
                    count, total = con.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM cache_entries").fetchone()
                    if count < self.settings.max_entries and total + size <= self.settings.max_bytes // 2:
                        break
                    con.execute("DELETE FROM cache_entries WHERE key=(SELECT key FROM cache_entries ORDER BY created, key LIMIT 1)")
                con.execute("INSERT INTO cache_entries VALUES (?, ?, ?, ?, ?)",
                            (key, now, now + self.settings.ttl_seconds, payload, size))
                con.commit()
                return True
        except (OSError, sqlite3.Error, ValueError, TypeError):
            logger.warning("Search cache write unavailable; result was not cached")
            return False

    def clear(self):
        """Clear existing entries even when disabled; never creates a new database."""
        try:
            with self._connection() as con:
                if con is None:
                    return {"enabled": self.settings.enabled, "cleared": 0}
                con.execute("BEGIN IMMEDIATE")
                count = con.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
                con.execute("DELETE FROM cache_entries")
                con.execute("UPDATE cache_meta SET generation=generation+1 WHERE id=1")
                con.commit()
                return {"enabled": self.settings.enabled, "cleared": count}
        except (OSError, sqlite3.Error):
            raise RuntimeError("Unable to clear search cache; check configured path and access") from None

    def status(self):
        result = {"enabled": self.settings.enabled, "path": str(self.settings.path),
                  "ttl_seconds": self.settings.ttl_seconds, "max_entries": self.settings.max_entries,
                  "max_bytes": self.settings.max_bytes, "entries": 0, "available": True}
        try:
            with self._connection() as con:
                if con is not None:
                    result["entries"] = con.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        except (OSError, sqlite3.Error):
            result["available"] = False
        return result
