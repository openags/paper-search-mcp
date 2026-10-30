"""Offline section extraction, conservative labels and file-boundary regressions."""
import asyncio
import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from paper_search_mcp import sections, server, cli
from paper_search_mcp.sections import SectionExtractionError, extract_pdf_sections, split_sections


def make_pdf(pages, *, encrypted=False):
    writer = PdfWriter()
    for lines in pages:
        page = writer.add_blank_page(612, 792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        commands = ["BT /F1 12 Tf 50 700 Td 16 TL"]
        for line in lines:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({safe}) Tj T*")
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("fixture")
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


@pytest.fixture
def root(tmp_path):
    try:
        sections._require_resource_limits()
    except SectionExtractionError:
        pytest.skip("This tool requires POSIX process resource limits")
    directory = tmp_path / "downloads"
    directory.mkdir()
    (directory / "paper.pdf").write_bytes(make_pdf([
        ["Paper title", "Abstract", "Summary of the work", "1 Introduction", "Context"],
        ["2. Materials and Methods", "Procedure", "3. Results", "Observed findings"],
        ["IV. Discussion", "Interpretation", "References", "Reference one"],
    ]))
    return directory


def test_real_pdf_preserves_sections_and_pages(root):
    result = extract_pdf_sections("paper.pdf", root=root)
    assert result["method"] == "standalone_heading_heuristic"
    assert [s["label"] for s in result["sections"]] == [
        "unclassified", "abstract", "introduction", "methods", "results", "discussion", "references"]
    assert [s["page_start"] for s in result["sections"]] == [1, 1, 1, 2, 2, 3, 3]
    assert result["sections"][3]["heading"] == "2. Materials and Methods"
    assert result["sections"][3]["text"] == "Procedure"
    assert result["pages_processed"] == result["total_pages"] == 3
    assert not result["truncated"]
    assert "heuristic" in result["warnings"][0]


def test_exact_text_slices_raw_order_and_repeated_headings():
    text = "Title\nMethods\n A procedure.\nMethods\nAnother procedure.\nResults and Discussion\nCombined.\n"
    result, limited = split_sections(text, [0, 27], 10)
    assert [s["label"] for s in result] == ["unclassified", "methods", "methods", "results_and_discussion"]
    assert [s["heading"] for s in result][1:3] == ["Methods", "Methods"]
    assert all(s["text"] == text[s["start_char"]:s["end_char"]] for s in result)
    assert not limited


def test_unknown_headings_and_inline_words_are_not_inferred():
    text = "Study design\nMethods: this is a sentence.\nThe results are here.\nNo section labels."
    result, limited = split_sections(text, [0], 10)
    assert len(result) == 1
    assert result[0]["label"] == "unclassified"
    assert result[0]["text"] == text
    assert result[0]["heading_match"] == "none"
    assert not limited


@pytest.mark.parametrize("heading,label", [
    ("ABSTRACT:", "abstract"), ("1.2 Methods", "methods"), ("II. Results", "results"),
    ("Conclusions", "conclusion"), ("Acknowledgements", "acknowledgments"),
])
def test_supported_heading_variants(heading, label):
    result, _ = split_sections(heading + "\nbody", [0], 10)
    assert result[0]["label"] == label
    assert result[0]["heading"] == heading


def test_page_limit_is_explicit(root):
    result = extract_pdf_sections("paper.pdf", max_pages=1, root=root)
    assert result["pages_processed"] == 1 and result["total_pages"] == 3
    assert result["truncated"] and result["stop_reasons"] == ["page_limit"]
    assert all(s["page_end"] == 1 for s in result["sections"])


def test_character_limit_is_explicit_and_bounded(root):
    result = extract_pdf_sections("paper.pdf", max_chars=20, root=root)
    assert result["truncated"] and result["stop_reasons"] == ["character_limit"]
    assert result["characters_scanned"] == 20
    assert result["characters_returned"] <= 20


def test_section_limit_preserves_complete_earlier_spans(root):
    result = extract_pdf_sections("paper.pdf", max_sections=2, root=root)
    assert result["stop_reasons"] == ["section_limit"]
    assert [s["label"] for s in result["sections"]] == ["unclassified", "abstract"]
    assert result["sections"][1]["text"] == "Summary of the work"


def test_section_with_no_body_is_preserved():
    result, _ = split_sections("Methods\nResults\n", [0], 10)
    assert [(s["label"], s["text"]) for s in result] == [("methods", ""), ("results", "")]
    assert all(s["page_start"] == s["page_end"] == 1 for s in result)


def test_spanning_pages_and_empty_pages_keep_real_page_numbers(root):
    (root / "span.pdf").write_bytes(make_pdf([["Methods", "First part"], [], ["Last part", "Results", "Done"]]))
    result = extract_pdf_sections("span.pdf", root=root)
    assert result["sections"][0]["page_start"] == 1
    assert result["sections"][0]["page_end"] == 3
    assert result["sections"][1]["page_start"] == 3


def test_image_only_or_blank_pdf_is_explicit_without_invented_sections(root):
    (root / "blank.pdf").write_bytes(make_pdf([[]]))
    result = extract_pdf_sections("blank.pdf", root=root)
    assert result["sections"] == []
    assert any("OCR is not supported" in warning for warning in result["warnings"])


@pytest.mark.parametrize("options", [
    {"max_pages": 0}, {"max_pages": 101}, {"max_pages": True},
    {"max_chars": 0}, {"max_chars": 200001}, {"max_chars": 1.5},
    {"max_sections": 0}, {"max_sections": 101}, {"max_sections": "2"},
    {"timeout_seconds": 0}, {"timeout_seconds": 61}, {"timeout_seconds": float("nan")},
    {"timeout_seconds": float("inf")}, {"timeout_seconds": True},
])
def test_invalid_budgets_fail_before_read(root, options, monkeypatch):
    read = Mock()
    monkeypatch.setattr(sections, "_read_allowed_pdf", read)
    with pytest.raises(ValueError):
        extract_pdf_sections("paper.pdf", root=root, **options)
    read.assert_not_called()


@pytest.mark.parametrize("path", [
    "../secret.pdf", "nested/../../secret.pdf", "/etc/passwd.pdf", "C:\\secret.pdf",
    "//server/share/secret.pdf", "paper.pdf:alternate", "paper.txt", "", "./paper.pdf",
    "nested//paper.pdf", "a/./paper.pdf", "a\x00.pdf", "a" * 4100 + ".pdf",
])
def test_untrusted_paths_fail_before_pdf_parse(root, path, monkeypatch):
    reader = Mock()
    monkeypatch.setattr(sections, "PdfReader", reader)
    with pytest.raises(SectionExtractionError):
        extract_pdf_sections(path, root=root)
    reader.assert_not_called()


def test_valid_nested_relative_path_and_operator_config(root, monkeypatch):
    (root / "nested").mkdir()
    (root / "nested" / "copy.pdf").write_bytes((root / "paper.pdf").read_bytes())
    monkeypatch.setenv("PAPER_SEARCH_MCP_SECTION_PDF_ROOT", str(root))
    assert extract_pdf_sections("nested/copy.pdf")["total_pages"] == 3


@pytest.mark.parametrize("kind", ["file", "directory", "internal"])
def test_symlinks_never_expand_read_authority(root, kind):
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(make_pdf([["Outside secret"]]))
    try:
        if kind == "directory":
            (root / "link").symlink_to(outside, target_is_directory=True)
            path = "link/secret.pdf"
        else:
            target = outside / "secret.pdf" if kind == "file" else root / "paper.pdf"
            (root / "link.pdf").symlink_to(target)
            path = "link.pdf"
    except OSError:
        pytest.skip("Symlink creation unavailable on this test platform")
    with pytest.raises(SectionExtractionError, match="Symlinks|outside"):
        extract_pdf_sections(path, root=root)


def test_directory_and_missing_file_are_rejected(root):
    (root / "directory.pdf").mkdir()
    for path in ("directory.pdf", "missing.pdf"):
        with pytest.raises(SectionExtractionError):
            extract_pdf_sections(path, root=root)


def test_oversized_pdf_rejected_before_read(root, monkeypatch):
    monkeypatch.setattr(sections, "MAX_PDF_BYTES", 10)
    reader = Mock()
    monkeypatch.setattr(sections, "PdfReader", reader)
    with pytest.raises(SectionExtractionError, match="size limit"):
        extract_pdf_sections("paper.pdf", root=root)
    reader.assert_not_called()


@pytest.mark.parametrize("body", [b"not a PDF", b"%PDF-1.7\ncorrupt"])
def test_invalid_pdf_is_a_sanitized_error(root, body):
    (root / "invalid.pdf").write_bytes(body)
    with pytest.raises(SectionExtractionError) as caught:
        extract_pdf_sections("invalid.pdf", root=root)
    assert "corrupt" not in str(caught.value) and str(root) not in str(caught.value)


def test_encrypted_pdf_is_rejected_without_password_handling(root):
    (root / "encrypted.pdf").write_bytes(make_pdf([["Methods", "Private"]], encrypted=True))
    with pytest.raises(SectionExtractionError, match="Encrypted PDFs are not supported"):
        extract_pdf_sections("encrypted.pdf", root=root)


def test_timeout_after_extraction_does_not_claim_complete_success(root, monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(sections.time, "monotonic", lambda: clock.now)
    def extract():
        clock.now = 2
        return "Methods\nbody"
    reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=extract)])
    monkeypatch.setattr(sections, "PdfReader", Mock(return_value=reader))
    with pytest.raises(TimeoutError, match="time budget"):
        sections._parse_pdf_sections((root / "paper.pdf").read_bytes(), 30, 60000, 50, 1)


def test_mcp_tool_registration_schema_and_real_call(root, monkeypatch):
    monkeypatch.setenv("PAPER_SEARCH_MCP_SECTION_PDF_ROOT", str(root))
    registry = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    tool = registry["extract_sections"]
    assert tool.annotations.readOnlyHint
    assert "root" not in tool.inputSchema["properties"]
    assert asyncio.run(server.extract_sections("paper.pdf"))["total_pages"] == 3
    with pytest.raises(SectionExtractionError):
        asyncio.run(server.extract_sections(str(root / "paper.pdf")))


def test_generic_cli_exposes_sections_without_new_custom_command(capsys):
    args = cli.build_parser().parse_args(["tool", "extract_sections", "--help"])
    with pytest.raises(SystemExit) as exited:
        asyncio.run(cli.cmd_tool(args))
    assert exited.value.code == 0
    output = capsys.readouterr().out
    assert "pdf_path" in output and "max-pages" in output


@pytest.mark.skipif(os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"), reason="openat/no-follow unavailable")
@pytest.mark.parametrize("portable", [False, True])
def test_directory_swap_cannot_read_pdf_outside_root(root, monkeypatch, portable):
    nested = root / "nested"
    nested.mkdir()
    (nested / "paper.pdf").write_bytes((root / "paper.pdf").read_bytes())
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "paper.pdf").write_bytes(make_pdf([["Secret outside data"]]))
    original_open = os.open
    swapped = False
    def swap_then_open(path, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            nested.rename(root / "detached")
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, *args, **kwargs)
    proxy = SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
    proxy.open = swap_then_open
    proxy.supports_dir_fd = set() if portable else {swap_then_open}
    monkeypatch.setattr(sections, "os", proxy)
    reader = Mock()
    monkeypatch.setattr(sections, "PdfReader", reader)
    with pytest.raises(SectionExtractionError):
        extract_pdf_sections("nested/paper.pdf", root=root)
    assert swapped
    reader.assert_not_called()


def test_character_budget_stops_before_extracting_another_page(root, monkeypatch):
    next_page = Mock(return_value="must not be extracted")
    reader = SimpleNamespace(is_encrypted=False, pages=[
        SimpleNamespace(extract_text=lambda: "1234"), SimpleNamespace(extract_text=next_page)])
    monkeypatch.setattr(sections, "PdfReader", Mock(return_value=reader))
    result = sections._parse_pdf_sections((root / "paper.pdf").read_bytes(), 30, 5, 50, 30)
    assert result["characters_scanned"] == 5
    assert result["pages_processed"] == 1
    assert result["stop_reasons"] == ["character_limit"]
    next_page.assert_not_called()


def test_pdf_parser_error_does_not_expose_path_or_embedded_text(root, monkeypatch):
    monkeypatch.setattr(sections, "PdfReader", Mock(side_effect=RuntimeError("private embedded content")))
    with pytest.raises(SectionExtractionError) as caught:
        sections._parse_pdf_sections((root / "paper.pdf").read_bytes(), 30, 60000, 50, 30)
    assert "private" not in str(caught.value)
    assert str(root) not in str(caught.value)


@pytest.mark.parametrize("configured", ["", "   "])
def test_empty_operator_root_does_not_expand_access_to_working_directory(configured, monkeypatch):
    monkeypatch.setenv("PAPER_SEARCH_MCP_SECTION_PDF_ROOT", configured)
    read = Mock()
    monkeypatch.setattr(sections, "_read_allowed_pdf", read)
    with pytest.raises(SectionExtractionError, match="root must not be empty"):
        extract_pdf_sections("paper.pdf")
    read.assert_not_called()



def test_truncated_inline_prefix_is_never_invented_as_a_heading(root):
    (root / "inline.pdf").write_bytes(make_pdf([["Methods are described in this sentence."]]))
    result = extract_pdf_sections("inline.pdf", max_chars=7, root=root)
    assert result["sections"][0]["label"] == "unclassified"
    assert result["sections"][0]["text"] == "Methods"
    assert result["characters_returned"] == 7
    assert result["partial_final_line"]
    assert any("partial" in warning for warning in result["warnings"])


def test_cut_at_actual_line_boundary_can_keep_a_real_heading(root):
    (root / "heading.pdf").write_bytes(make_pdf([["Methods", "Procedure"]]))
    result = extract_pdf_sections("heading.pdf", max_chars=7, root=root)
    assert result["sections"][0]["label"] == "methods"
    assert not result["partial_final_line"]


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_real_mcp_dispatch_requires_strict_integer_limits(value, monkeypatch):
    read = Mock()
    monkeypatch.setattr(sections, "_read_allowed_pdf", read)
    with pytest.raises(Exception, match="valid integer"):
        asyncio.run(server.mcp.call_tool("extract_sections", {"pdf_path": "paper.pdf", "max_pages": value}))
    read.assert_not_called()


def test_compressed_stream_budget_rejects_expansion_before_text_extraction(root):
    from pypdf import PdfReader
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf([["Methods", "small"]]))))
    expanded = DecodedStreamObject()
    expanded.set_data(b" " * (8 * 1024 * 1024 + 4096))
    writer.pages[0][NameObject("/Contents")] = writer._add_object(expanded.flate_encode())
    target = root / "expanded.pdf"
    writer.write(target)
    assert target.stat().st_size < 100_000
    with pytest.raises(SectionExtractionError, match="stream or structure budget"):
        extract_pdf_sections("expanded.pdf", root=root)
    # The rejection must release capacity; a normal document still succeeds.
    assert extract_pdf_sections("paper.pdf", root=root)["total_pages"] == 3


def test_worker_memory_limit_rejects_large_allocation_without_harming_parent():
    try:
        sections._require_resource_limits()
    except SectionExtractionError:
        pytest.skip("POSIX resource limits unavailable")
    import subprocess, sys
    command = [sys.executable, "-c", "from paper_search_mcp._section_worker import _resource_limits; _resource_limits(3); bytearray(600 * 1024 * 1024)"]
    result = subprocess.run(command, capture_output=True, timeout=8)
    assert result.returncode != 0
    assert b"MemoryError" in result.stderr


def test_parser_timeout_terminates_non_gil_friendly_child_and_releases_capacity(root, monkeypatch):
    import subprocess, sys
    processes = []
    original_popen = subprocess.Popen
    def record(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(sections.subprocess, "Popen", record)
    real_command = sections._worker_command
    monkeypatch.setattr(sections, "_worker_command", lambda: [sys.executable, "-c", "import re; re.fullmatch('(a+)+', 'a'*45+'!')"])
    with pytest.raises(TimeoutError, match="timed out"):
        extract_pdf_sections("paper.pdf", timeout_seconds=0.3, root=root)
    assert processes and all(process.poll() is not None for process in processes)
    monkeypatch.setattr(sections, "_worker_command", real_command)
    assert extract_pdf_sections("paper.pdf", root=root)["total_pages"] == 3


def test_process_start_failure_releases_capacity(root, monkeypatch):
    original = sections.subprocess.Popen
    monkeypatch.setattr(sections.subprocess, "Popen", Mock(side_effect=OSError("private path")))
    for _ in range(3):
        with pytest.raises(SectionExtractionError, match="could not be started"):
            extract_pdf_sections("paper.pdf", root=root)
    monkeypatch.setattr(sections.subprocess, "Popen", original)
    assert extract_pdf_sections("paper.pdf", root=root)["total_pages"] == 3


def test_parser_capacity_is_bounded_before_read(root, monkeypatch):
    read = Mock()
    monkeypatch.setattr(sections, "_read_allowed_pdf", read)
    assert sections._PARSER_SLOTS.acquire(blocking=False)
    assert sections._PARSER_SLOTS.acquire(blocking=False)
    try:
        with pytest.raises(SectionExtractionError, match="capacity is exhausted"):
            extract_pdf_sections("paper.pdf", root=root)
    finally:
        sections._PARSER_SLOTS.release()
        sections._PARSER_SLOTS.release()
    read.assert_not_called()


def test_real_cli_large_result_drains_worker_pipe(root):
    import json, subprocess, sys
    text = "Evidence " * 9000
    lines = [text[index:index + 500] for index in range(0, len(text), 500)]
    (root / "large.pdf").write_bytes(make_pdf([["Methods", *lines]]))
    env = dict(os.environ, PAPER_SEARCH_MCP_SECTION_PDF_ROOT=str(root), PAPER_SEARCH_MCP_ENV_FILE=os.devnull)
    result = subprocess.run([sys.executable, "-m", "paper_search_mcp.cli", "tool", "extract_sections",
                             "large.pdf", "--max-chars", "150000"],
                            capture_output=True, text=True, timeout=20, env=env)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) > 65536
    payload = json.loads(result.stdout)
    assert payload["sections"][0]["text"] == "\n".join(lines).strip()
    assert not payload["truncated"]


def test_mcp_cancellation_reaps_child_without_occupying_search_pool(root, monkeypatch):
    import subprocess, sys, time
    from threading import Event
    started = Event()
    processes = []
    original_popen = subprocess.Popen
    def record(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        started.set()
        return process
    monkeypatch.setattr(sections.subprocess, "Popen", record)
    monkeypatch.setattr(sections, "_worker_command", lambda: [sys.executable, "-c", "import re; re.fullmatch('(a+)+', 'a'*45+'!')"])
    monkeypatch.setenv("PAPER_SEARCH_MCP_SECTION_PDF_ROOT", str(root))
    async def run():
        task = asyncio.create_task(server.extract_sections("paper.pdf", timeout_seconds=10))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        deadline = time.monotonic() + 3
        while any(process.poll() is None for process in processes) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert all(process.poll() is not None for process in processes)
        assert server._SEARCH_EXECUTOR.submit(lambda: "unaffected").result(timeout=1) == "unaffected"
    asyncio.run(run())



def test_large_request_finishes_after_delayed_worker_stdin_reader(monkeypatch):
    import sys, time
    command = [sys.executable, "-c", (
        "import json,sys,time; time.sleep(.15); "
        "header=json.loads(sys.stdin.buffer.readline()); data=sys.stdin.buffer.read(); "
        "print(json.dumps({'status':'ok','result':{'size':len(data)}}))"
    )]
    monkeypatch.setattr(sections, "_worker_command", lambda: command)
    data = b"%PDF-" + b"x" * 200_000
    options = dict(max_pages=1, max_chars=100, max_sections=1, timeout_seconds=2)
    assert sections._run_parser_process(data, options, time.monotonic() + 2, None) == {"size": len(data)}



def test_unsupported_resource_platform_fails_before_read(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "resource", None)
    read = Mock()
    monkeypatch.setattr(sections, "_read_allowed_pdf", read)
    with pytest.raises(SectionExtractionError, match="requires POSIX"):
        extract_pdf_sections("paper.pdf")
    read.assert_not_called()
