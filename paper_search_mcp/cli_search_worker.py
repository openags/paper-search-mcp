"""Private one-source CLI worker. JSON in/out, diagnostic messages on stderr."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout


def main() -> None:
    status = 0
    try:
        job = json.load(sys.stdin)
        # Redirection is safe here: this process executes just one source.
        with redirect_stdout(sys.stderr):
            from .cli import _get_searcher

            searcher = _get_searcher(job["source"])
            papers = searcher.search(job["query"], max_results=job["max_results"], **job["kwargs"])
            result = [paper.to_dict() for paper in papers]
    except Exception as exc:
        result = {"error": str(exc) or type(exc).__name__}
        status = 1
    print(json.dumps(result, default=str))
    raise SystemExit(status)


if __name__ == "__main__":
    main()
