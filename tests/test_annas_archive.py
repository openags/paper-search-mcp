import os
import pytest
from paper_search_mcp.academic_platforms.annas_archive import AnnasArchiveFetcher

def test_annas_archive_download():
    fetcher = AnnasArchiveFetcher()
    res = fetcher.download_pdf("10.invalid/doi", "./downloads")
    assert res == ""
