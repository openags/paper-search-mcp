"""Opt-in CLI deadlines for blocking searchers, isolated in disposable workers."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any


# Bound memory and upstream concurrency even for the broad source preset.
MAX_SEARCH_WORKERS = 4


def _worker_command() -> list[str]:
    return [sys.executable, "-m", "paper_search_mcp.cli_search_worker"]


async def _kill_and_reap(
    process: asyncio.subprocess.Process,
    communication: asyncio.Task | None = None,
) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass  # The worker exited between the check and kill.
    if communication is not None:
        communication.cancel()
        await asyncio.gather(communication, return_exceptions=True)
    # Drain before waiting: wait() alone can deadlock on a full stdout pipe,
    # including when asyncio.run has cancelled every task during Ctrl-C cleanup.
    await process.communicate()


async def _search_source(job: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    # Shield creation so cancellation cannot lose the handle to a new worker.
    creation = asyncio.create_task(asyncio.create_subprocess_exec(
        *_worker_command(), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # Inherit stderr: diagnostics cannot fill an unread pipe or pollute JSON.
    ))
    try:
        process = await asyncio.shield(creation)
    except asyncio.CancelledError:
        process = await creation
        await _kill_and_reap(process)
        raise

    communication = asyncio.create_task(process.communicate(json.dumps(job).encode("utf-8")))
    try:
        # A thread timeout cannot stop requests or unblock asyncio.run shutdown.
        # This timeout covers worker initialization, I/O, serialization and exit.
        stdout, _ = await asyncio.wait_for(asyncio.shield(communication), timeout)
    except BaseException as exc:
        await _kill_and_reap(process, communication)
        if isinstance(exc, asyncio.TimeoutError):
            raise TimeoutError(
                f"search for source '{job['source']}' timed out after {timeout:g} seconds"
            ) from exc
        raise

    try:
        result = json.loads(stdout)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"search worker exited with code {process.returncode} without a valid result") from exc
    if isinstance(result, dict) and isinstance(result.get("error"), str):
        raise RuntimeError(result["error"])
    if process.returncode != 0:
        raise RuntimeError(f"search worker exited with code {process.returncode}")
    if not isinstance(result, list) or not all(isinstance(paper, dict) for paper in result):
        raise RuntimeError("search worker returned an invalid result")
    return result


async def search_sources(jobs: list[dict[str, Any]], timeout: float) -> list[Any]:
    """Search in source order, with a fresh budget after each worker gets a slot."""
    slots = asyncio.Semaphore(MAX_SEARCH_WORKERS)

    async def run(job: dict[str, Any]) -> list[dict[str, Any]]:
        async with slots:
            return await _search_source(job, timeout)

    return await asyncio.gather(*(run(job) for job in jobs), return_exceptions=True)
