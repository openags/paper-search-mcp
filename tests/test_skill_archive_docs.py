"""Keep the README's packaging-only example executable and correctly scoped."""

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


class SkillArchiveDocumentationTests(unittest.TestCase):
    def test_documented_archive_contains_only_the_named_skill(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.split("#### Skill ZIP uploads and other Claude runtimes", 1)[1]
        example = re.search(r"```python\n(.*?)\n```", section, re.DOTALL)
        self.assertIsNotNone(example)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "claude-code").mkdir()
            source = (ROOT / "claude-code" / "SKILL.md").read_bytes()
            (root / "claude-code" / "SKILL.md").write_bytes(source)

            subprocess.run(
                [sys.executable, "-c", example.group(1)],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            with ZipFile(root / "paper-search-skill.zip") as archive:
                self.assertEqual(archive.namelist(), ["paper-search/SKILL.md"])
                self.assertEqual(archive.read("paper-search/SKILL.md"), source)


if __name__ == "__main__":
    unittest.main()
