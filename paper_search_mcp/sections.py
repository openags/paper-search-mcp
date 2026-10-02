"""Bounded, evidence-preserving section extraction from an allowed local PDF."""
from __future__ import annotations

from bisect import bisect_right
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from threading import BoundedSemaphore, Event, Thread
import time

from pypdf import PdfReader

from .config import get_env

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_WORKER_OUTPUT_BYTES = 3 * 1024 * 1024
_PARSER_SLOTS = BoundedSemaphore(2)
LABELS = {
    "abstract": "abstract", "introduction": "introduction", "background": "background",
    "methods": "methods", "materials and methods": "methods", "methodology": "methods",
    "results": "results", "discussion": "discussion",
    "results and discussion": "results_and_discussion",
    "conclusion": "conclusion", "conclusions": "conclusion", "limitations": "limitations",
    "references": "references", "bibliography": "references",
    "acknowledgments": "acknowledgments", "acknowledgements": "acknowledgments",
}
HEADING = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*\.?|[IVXLC]+[.)]?)\s+)?(?P<title>"
    + "|".join(re.escape(title) for title in sorted(LABELS, key=len, reverse=True))
    + r")[:.]?$", re.IGNORECASE,
)


class SectionExtractionError(ValueError):
    """The input cannot be read safely or parsed as a supported PDF."""


def validate_limits(max_pages, max_chars, max_sections, timeout_seconds):
    for name, value, ceiling in (("max_pages", max_pages, 100),
                                 ("max_chars", max_chars, 200_000),
                                 ("max_sections", max_sections, 100)):
        if type(value) is not int or not 1 <= value <= ceiling:
            raise ValueError(f"{name} must be an integer between 1 and {ceiling}")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60):
        raise ValueError("timeout_seconds must be greater than 0 and at most 60")


def _read_allowed_pdf(pdf_path: str, root: Path) -> bytes:
    """Open only a relative regular PDF under the operator-controlled root.

    On platforms with openat support, no-follow directory descriptors protect
    every component against symlink replacement. The portable path additionally
    checks canonical containment and file identity before and after opening.
    """
    if not isinstance(pdf_path, str) or not pdf_path or len(pdf_path) > 4096:
        raise SectionExtractionError("pdf_path must be a relative PDF filename")
    normalized = pdf_path.replace("\\", "/")
    parts = normalized.split("/")
    if (normalized.startswith("/") or ":" in normalized or "\x00" in normalized
            or any(part in {"", ".", ".."} for part in parts)
            or Path(parts[-1]).suffix.lower() != ".pdf"):
        raise SectionExtractionError("Only relative .pdf paths inside the configured PDF root are allowed")
    descriptors = []
    try:
        root = root.resolve(strict=True)
        candidate = root.joinpath(*parts)
        current = root
        for part in parts:
            current = current / part
            attributes = getattr(current.lstat(), "st_file_attributes", 0)
            reparse_point = attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if current.is_symlink() or reparse_point or getattr(current, "is_junction", lambda: False)():
                raise SectionExtractionError("Symlinks and junctions are not allowed in PDF paths")
        canonical = candidate.resolve(strict=True)
        if not canonical.is_relative_to(root):
            raise SectionExtractionError("The PDF path is outside the configured PDF root")
        before = canonical.stat()
        if not stat.S_ISREG(before.st_mode):
            raise SectionExtractionError("The PDF must be a regular file")
        if before.st_size > MAX_PDF_BYTES:
            raise SectionExtractionError("The PDF exceeds the 20 MiB size limit")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptors.append(os.open(root, directory_flags))
            for part in parts[:-1]:
                descriptors.append(os.open(part, directory_flags, dir_fd=descriptors[-1]))
            fd = os.open(parts[-1], flags | os.O_NOFOLLOW, dir_fd=descriptors[-1])
        else:
            fd = os.open(canonical, flags | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_PDF_BYTES
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or candidate.resolve(strict=True) != canonical):
                raise SectionExtractionError("The PDF changed or is outside the configured PDF root")
            data = stream.read(MAX_PDF_BYTES + 1)
        if len(data) > MAX_PDF_BYTES:
            raise SectionExtractionError("The PDF exceeds the 20 MiB size limit")
        if not data.startswith(b"%PDF-"):
            raise SectionExtractionError("The file is not a PDF")
        return data
    except SectionExtractionError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise SectionExtractionError("The PDF is unavailable under the configured PDF root") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def split_sections(text: str, page_starts: list[int], max_sections: int, final_line_complete: bool = True) -> tuple[list[dict], bool]:
    """Keep original order, page spans and exact body slices; never infer fields."""
    headings = []
    position = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        match = HEADING.fullmatch(stripped)
        if match and (final_line_complete or position + len(line) < len(text)):
            headings.append((position, position + len(line), stripped,
                             LABELS[match.group("title").lower()]))
        position += len(line)
    boundaries = []
    if not headings or text[:headings[0][0]].strip():
        boundaries.append((0, 0, "", "unclassified"))
    boundaries.extend(headings)
    sections = []
    for index, (heading_start, body_start, heading, label) in enumerate(boundaries):
        if len(sections) == max_sections:
            return sections, True
        body_end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        raw = text[body_start:body_end]
        start = body_start + len(raw) - len(raw.lstrip())
        end = body_end - len(raw) + len(raw.rstrip())
        end = max(start, end)
        if not heading and not text[start:end]:
            continue
        page_start = bisect_right(page_starts, heading_start if heading else start)
        page_end = bisect_right(page_starts, end - 1 if end > start else heading_start)
        sections.append({"label": label, "heading": heading, "text": text[start:end],
                         "page_start": page_start, "page_end": max(page_start, page_end),
                         "start_char": start, "end_char": end,
                         "heading_match": "standalone_line" if heading else "none"})
    return sections, False


def _worker_command() -> list[str]:
    # Use this installed/source checkout, never a user-supplied module or path.
    bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv[1]); "
                 "runpy.run_module('paper_search_mcp._section_worker',run_name='__main__')")
    return [sys.executable, "-I", "-c", bootstrap, str(Path(__file__).resolve().parents[1])]


def _require_resource_limits():
    try:
        import resource
        if not hasattr(resource, "RLIMIT_AS") or not hasattr(resource, "RLIMIT_CPU"):
            raise ImportError
    except ImportError as exc:
        raise SectionExtractionError(
            "PDF section extraction requires POSIX address-space and CPU resource limits on this platform"
        ) from exc


def _run_parser_process(data: bytes, options: dict, deadline: float, cancel_event: Event | None):
    if time.monotonic() >= deadline or (cancel_event is not None and cancel_event.is_set()):
        raise TimeoutError("PDF section extraction timed out or was cancelled")
    header = json.dumps({"size": len(data), **options}, allow_nan=False).encode() + b"\n"
    payload = header + data
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    environment["PAPER_SEARCH_MCP_ENV_FILE"] = os.devnull
    process = None
    monitor = None
    finished, interrupted = Event(), Event()
    def stop_child():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
    def watch_cancellation():
        while not finished.wait(0.02):
            if time.monotonic() >= deadline or (cancel_event is not None and cancel_event.is_set()):
                interrupted.set()
                try:
                    stop_child()
                except OSError:
                    pass  # A concurrently completed child no longer needs termination.
                return
    try:
        process = subprocess.Popen(_worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=environment, close_fds=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PDF section extraction timed out or was cancelled")
        # At most two jobs can reach here. Their cancellation monitors are also
        # bounded and joined before the admission slot is released.
        monitor = Thread(target=watch_cancellation, name="pdf-cancellation", daemon=True)
        monitor.start()
        try:
            # One complete communicate call is essential: retrying with input=None
            # after a short timeout can strand partially-written input on POSIX.
            # communicate drains stdout concurrently, including >pipe-size results.
            output, _ = process.communicate(input=payload, timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("PDF section extraction timed out or was cancelled") from exc
        if interrupted.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("PDF section extraction timed out or was cancelled")
        if process.returncode != 0 or len(output) > MAX_WORKER_OUTPUT_BYTES:
            raise SectionExtractionError("The PDF parser exited or exceeded its resource budget")
        try:
            message = json.loads(output)
            if message["status"] == "ok" and isinstance(message["result"], dict):
                return message["result"]
            if message["status"] == "timeout":
                raise TimeoutError("PDF section extraction exceeded its time budget")
            if message["status"] == "error":
                raise SectionExtractionError(message["message"])
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, SectionExtractionError):
                raise
            raise SectionExtractionError("The PDF parser returned an invalid response") from exc
        raise SectionExtractionError("The PDF parser returned an invalid response")
    except TimeoutError:
        raise
    except OSError as exc:
        raise SectionExtractionError("The isolated PDF parser could not be started") from exc
    finally:
        finished.set()
        if process is not None:
            stop_child()
            if monitor is not None:
                monitor.join(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def extract_pdf_sections(pdf_path: str, max_pages: int = 30, max_chars: int = 60_000,
                         max_sections: int = 50, timeout_seconds: float = 30.0,
                         *, root: Path | None = None, _cancel_event: Event | None = None) -> dict:
    """Confine local file access, then parse bounded bytes in an isolated process."""
    validate_limits(max_pages, max_chars, max_sections, timeout_seconds)
    _require_resource_limits()
    if not _PARSER_SLOTS.acquire(blocking=False):
        raise SectionExtractionError("PDF parser capacity is exhausted; try again later")
    try:
        deadline = time.monotonic() + timeout_seconds
        if root is None:
            configured_root = get_env("SECTION_PDF_ROOT", "./downloads").strip()
            if not configured_root:
                raise SectionExtractionError("The configured PDF root must not be empty")
            allowed_root = Path(configured_root).expanduser()
        else:
            allowed_root = root
        if _cancel_event is not None and _cancel_event.is_set():
            raise TimeoutError("PDF section extraction was cancelled")
        data = _read_allowed_pdf(pdf_path, allowed_root)
        if time.monotonic() >= deadline:
            raise TimeoutError("PDF section extraction exceeded its time budget")
        options = dict(max_pages=max_pages, max_chars=max_chars, max_sections=max_sections,
                       timeout_seconds=timeout_seconds)
        return _run_parser_process(data, options, deadline, _cancel_event)
    finally:
        _PARSER_SLOTS.release()


def _parse_pdf_sections(data: bytes, max_pages: int, max_chars: int,
                        max_sections: int, timeout_seconds: float) -> dict:
    """Called only inside the resource-limited worker (directly in unit tests)."""
    validate_limits(max_pages, max_chars, max_sections, timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    def check_deadline():
        if time.monotonic() >= deadline:
            raise TimeoutError("PDF section extraction exceeded its time budget")
    text_parts, page_starts, reasons = [], [], []
    used = pages_processed = 0
    final_line_complete = True
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise SectionExtractionError("Encrypted PDFs are not supported; no password is requested")
        total_pages = len(reader.pages)
        check_deadline()
        for page_number in range(min(total_pages, max_pages)):
            if used >= max_chars:
                reasons.append("character_limit")
                break
            check_deadline()
            text = reader.pages[page_number].extract_text() or ""
            check_deadline()
            page_starts.append(used)
            pages_processed += 1
            remaining = max_chars - used
            part = text[:remaining]
            text_parts.append(part)
            used += len(part)
            if len(text) > remaining or (used == max_chars and page_number + 1 < total_pages):
                if len(text) > remaining:
                    final_line_complete = bool(part.endswith(("\n", "\r")) or text[remaining] in "\r\n")
                reasons.append("character_limit")
                break
            if used < max_chars:
                text_parts.append("\n")
                used += 1
        if pages_processed < total_pages and pages_processed >= max_pages:
            reasons.append("page_limit")
    except (SectionExtractionError, TimeoutError):
        raise
    except MemoryError as exc:
        raise SectionExtractionError("The PDF exceeded the parser memory budget") from exc
    except Exception as exc:
        from pypdf.errors import LimitReachedError
        if isinstance(exc, LimitReachedError):
            raise SectionExtractionError("The PDF exceeded a parser stream or structure budget") from exc
        raise SectionExtractionError("The PDF could not be parsed or its text extracted") from exc
    text = "".join(text_parts)
    sections, section_limited = split_sections(text, page_starts, max_sections, final_line_complete)
    if section_limited:
        reasons.append("section_limit")
    check_deadline()
    warnings = ["Section labels are heuristic heading matches, not semantic or layout validation."]
    if not final_line_complete:
        warnings.append("The final extracted line is partial and is not used as a section heading.")
    if not text.strip():
        warnings.append("No extractable text was found in the processed pages; OCR is not supported.")
    elif not any(section["heading"] for section in sections):
        warnings.append("No supported standalone heading was found; text is unclassified.")
    return {"method": "standalone_heading_heuristic", "sections": sections,
            "total_pages": total_pages, "pages_processed": pages_processed,
            "characters_scanned": len(text),
            "characters_returned": sum(len(section["text"]) for section in sections),
            "truncated": bool(reasons), "stop_reasons": reasons,
            "partial_final_line": not final_line_complete, "warnings": warnings}
