from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ENV_LOADED = False
ENV_PREFIX = "PAPER_SEARCH_MCP_"


def _candidate_env_files() -> list[Path]:
    explicit_path = os.getenv(f"{ENV_PREFIX}ENV_FILE", "").strip()
    if explicit_path:
        return [Path(explicit_path).expanduser()]

    cwd_env = Path.cwd() / ".env"
    project_env = Path(__file__).resolve().parent.parent / ".env"

    if cwd_env == project_env:
        return [cwd_env]
    return [cwd_env, project_env]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _load_env_from_file(env_file: Path) -> None:
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = _strip_quotes(value.strip())
        os.environ.setdefault(key, value)


def load_env_file(force: bool = False) -> None:
    global _ENV_LOADED

    if _ENV_LOADED and not force:
        return

    for env_file in _candidate_env_files():
        if not env_file.exists() or not env_file.is_file():
            continue

        try:
            _load_env_from_file(env_file)
            logger.debug("Loaded environment values from %s", env_file)
            break
        except Exception as exc:
            logger.warning("Failed to load environment file %s: %s", env_file, exc)

    _ENV_LOADED = True


def get_env(name: str, default: Optional[str] = "") -> str:
    load_env_file()

    normalized = name.strip()
    if not normalized:
        return "" if default is None else str(default)

    keys = [f"{ENV_PREFIX}{normalized}", normalized]
    for key in keys:
        if key in os.environ:
            return os.environ.get(key, "")

    return "" if default is None else str(default)
