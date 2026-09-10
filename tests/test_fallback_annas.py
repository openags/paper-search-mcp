import pytest
import asyncio
from unittest.mock import patch
from paper_search_mcp.server import download_with_fallback

def test_fallback_includes_annas():
    with patch('paper_search_mcp.server.AnnasArchiveFetcher.download_pdf') as mock_anna, \
         patch('paper_search_mcp.server._try_repository_fallback') as mock_repo, \
         patch('paper_search_mcp.server.unpaywall_resolver.resolve_best_pdf_url') as mock_upw, \
         patch('paper_search_mcp.server.os.path.exists') as mock_exists:
        
        mock_repo.return_value = (None, "no repo")
        mock_upw.return_value = None
        mock_anna.return_value = "fake/path/anna.pdf"
        mock_exists.return_value = True
        res = asyncio.run(download_with_fallback(source="unknown_src", paper_id="123", doi="10.123/fake", use_scihub=False))
        assert res == "fake/path/anna.pdf"
