"""Offline acceptance checks for the standalone skill package builder."""
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_skill_zip.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_standalone_archive_layout_and_content(tmp_path):
    target = tmp_path / "nested" / "skill.zip"
    result = run("--output", target)
    assert result.returncode == 0, result.stderr
    with ZipFile(target) as archive:
        assert archive.namelist() == ["paper-search/SKILL.md"]
        text = archive.read("paper-search/SKILL.md").decode()
        assert text == (ROOT / "claude-code" / "SKILL.md").read_text()
        assert text.startswith("---\nname: paper-search\n")
        assert "description:" in text.split("---", 2)[1]
        assert archive.testzip() is None


def test_builder_refuses_unrequested_overwrite(tmp_path):
    target = tmp_path / "skill.zip"
    target.write_bytes(b"keep")
    result = run("--output", target)
    assert result.returncode != 0
    assert target.read_bytes() == b"keep"


def test_builder_explicit_force_replaces_archive(tmp_path):
    target = tmp_path / "skill.zip"
    target.write_bytes(b"old")
    result = run("--output", target, "--force")
    assert result.returncode == 0, result.stderr
    with ZipFile(target) as archive:
        assert archive.testzip() is None
