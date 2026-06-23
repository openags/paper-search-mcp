"""Pytest configuration.

Most tests in this directory are integration tests that hit live public
academic APIs (arXiv, PubMed, Crossref, ...). They are non-deterministic in
CI (rate limits, network availability) so they are SKIPPED by default.

To run them locally:

    RUN_LIVE_TESTS=1 python -m pytest tests/

Going forward, prefer adding mocked unit tests (no network) which will always
run. Such tests should be placed here and will NOT be skipped because they do
not require any live API call marker.
"""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip every test unless the caller explicitly opts into live tests."""
    if os.getenv("RUN_LIVE_TESTS", "0") == "1":
        return
    skip_live = pytest.mark.skip(
        reason="Integration test hitting live APIs; set RUN_LIVE_TESTS=1 to run."
    )
    for item in items:
        item.add_marker(skip_live)
