"""Build the standalone Claude Code skill archive without repository nesting."""
import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def build_archive(output: Path, *, force: bool = False) -> Path:
    source = ROOT / "claude-code" / "SKILL.md"
    text = source.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\nname: paper-search\n" not in text:
        raise ValueError("Expected the paper-search skill with YAML frontmatter")
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents an accidental overwrite unless explicitly requested.
    with ZipFile(output, "w" if force else "x", compression=ZIP_DEFLATED) as archive:
        archive.writestr("paper-search/SKILL.md", text)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("paper-search-skill.zip"))
    parser.add_argument("--force", action="store_true", help="Replace an existing archive")
    args = parser.parse_args()
    try:
        path = build_archive(args.output, force=args.force)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Cannot build skill archive: {exc}\n")
    print(path)


if __name__ == "__main__":
    main()
