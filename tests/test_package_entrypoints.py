import importlib.metadata as metadata
import unittest


class TestPackageEntrypoints(unittest.TestCase):
    def test_console_scripts_are_exposed(self):
        dist = metadata.distribution("paper-search-mcp")
        console_scripts = {
            entry_point.name: entry_point.value
            for entry_point in dist.entry_points
            if entry_point.group == "console_scripts"
        }

        self.assertEqual(
            console_scripts.get("paper-search-mcp"),
            "paper_search_mcp.server:main",
        )
        self.assertEqual(
            console_scripts.get("paper-search"),
            "paper_search_mcp.cli:main",
        )


if __name__ == "__main__":
    unittest.main()
