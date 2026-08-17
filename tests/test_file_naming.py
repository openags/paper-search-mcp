from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_search_mcp.cli import _save_abstract_only_metadata
from paper_search_mcp.file_naming import paper_filename


class TestPaperFileNaming(unittest.TestCase):
    def test_first_author_year_short_title_pdf_name(self):
        filename = paper_filename(
            title="Deep Learning for Biology: A Practical Survey",
            authors=["Ada Lovelace"],
            published_date=datetime(2024, 1, 1),
            extension=".pdf",
        )
        self.assertEqual(filename, "Lovelace_2024_Deep_Learning_for_Biology_A.pdf")

    def test_abstract_only_metadata_uses_txt_extension(self):
        metadata = {
            "title": "A Useful Paper About Proteins",
            "authors": ["Grace Hopper"],
            "published_date": "2023",
            "abstract": "Short abstract.",
            "doi": "10.1000/example",
            "url": "https://doi.org/10.1000/example",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("paper_search_mcp.cli.metadata_for_identifier", return_value=metadata):
                path = Path(_save_abstract_only_metadata("10.1000/example", tmp_dir))

            self.assertEqual(path.name, "Hopper_2023_A_Useful_Paper_About_Proteins.txt")
            self.assertIn("Short abstract.", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
