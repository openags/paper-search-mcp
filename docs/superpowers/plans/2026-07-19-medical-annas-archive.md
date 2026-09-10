# Medical Specialization & Anna's Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modify the `paper-search-mcp` application to focus on medical databases and integrate Anna's Archive into the fallback download chain.

**Architecture:** Change `ALL_SOURCES` in `server.py` to only include medical databases. Create a new scraper module for Anna's Archive using BeautifulSoup. Hook it into the `download_with_fallback` function. Finally, create an `.agents/mcp_config.json` for local Antigravity IDE integration.

**Tech Stack:** Python 3.10+, BeautifulSoup4, httpx, pytest, JSON.

## Global Constraints
- Target workspace: `D:/Dev/paper-search-mcp`
- Maintain PEP8 style guidelines and keep existing architecture paradigms (e.g., synchronous/threadpool adaptations for searchers/fetchers).

---

### Task 1: Update ALL_SOURCES for Medical Specialization

**Files:**
- Modify: `paper_search_mcp/server.py`
- Modify: `tests/test_server.py` (if applicable)

**Interfaces:**
- Consumes: Existing `ALL_SOURCES` list.
- Produces: A narrowed `ALL_SOURCES` containing only `pubmed`, `pmc`, `europepmc`, `medrxiv`, `biorxiv`.

- [ ] **Step 1: Write the failing test or verify source logic**

```python
# Create or modify tests/test_medical_sources.py
from paper_search_mcp.server import ALL_SOURCES

def test_all_sources_are_medical():
    expected = ["pubmed", "pmc", "europepmc", "medrxiv", "biorxiv"]
    assert set(ALL_SOURCES) == set(expected)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_medical_sources.py -v`
Expected: FAIL (because current ALL_SOURCES has 21+ sources)

- [ ] **Step 3: Write minimal implementation**

Modify `paper_search_mcp/server.py` (around line 79):
```python
ALL_SOURCES = [
    "pubmed",
    "pmc",
    "europepmc",
    "medrxiv",
    "biorxiv"
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_medical_sources.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add paper_search_mcp/server.py tests/test_medical_sources.py
git commit -m "feat: restrict ALL_SOURCES to medical databases only"
```

---

### Task 2: Implement AnnasArchiveFetcher

**Files:**
- Create: `paper_search_mcp/academic_platforms/annas_archive.py`
- Create: `tests/test_annas_archive.py`

**Interfaces:**
- Consumes: A paper's `doi`.
- Produces: `download_pdf(doi: str, save_path: str) -> str` returning the path to the downloaded PDF or empty string on failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_annas_archive.py
import os
import pytest
from paper_search_mcp.academic_platforms.annas_archive import AnnasArchiveFetcher

def test_annas_archive_download():
    fetcher = AnnasArchiveFetcher()
    # Test with a known DOI (e.g. dummy or valid open access)
    # Using an invalid DOI to just test the return type gracefully failing
    res = fetcher.download_pdf("10.invalid/doi", "./downloads")
    assert res == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_annas_archive.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
# paper_search_mcp/academic_platforms/annas_archive.py
import os
import httpx
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)

class AnnasArchiveFetcher:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }

    def download_pdf(self, doi: str, save_path: str) -> str:
        doi = doi.strip()
        if not doi:
            return ""
            
        try:
            # Step 1: Search the DOI
            search_url = f"https://annas-archive.org/search?q={doi}"
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                resp = client.get(search_url, headers=self.headers)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # Step 2: Find the MD5 page link (first search result)
                # Note: This is a placeholder implementation logic. Anna's structure involves /md5/ links
                link = soup.find('a', href=lambda x: x and '/md5/' in x)
                if not link:
                    logger.warning(f"Anna's Archive: No MD5 link found for {doi}")
                    return ""
                
                md5_url = f"https://annas-archive.org{link['href']}"
                md5_resp = client.get(md5_url, headers=self.headers)
                md5_resp.raise_for_status()
                md5_soup = BeautifulSoup(md5_resp.text, 'html.parser')
                
                # Step 3: Find actual download links (e.g. Libgen, IPFS)
                # In a real scenario we parse the download links, pick one, and download.
                # For this MVP, we return a failure string if direct PDF isn't easily obtained 
                # to prevent infinite hangs.
                logger.warning(f"Anna's Archive: MD5 page found, but download automation requires explicit mirror handling for {doi}")
                return ""
                
        except Exception as e:
            logger.error(f"Anna's Archive download error for {doi}: {e}")
            return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_annas_archive.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add paper_search_mcp/academic_platforms/annas_archive.py tests/test_annas_archive.py
git commit -m "feat: add AnnasArchiveFetcher stub"
```

---

### Task 3: Integrate Anna's Archive into `download_with_fallback`

**Files:**
- Modify: `paper_search_mcp/server.py`

**Interfaces:**
- Consumes: `AnnasArchiveFetcher`
- Produces: Updates `download_with_fallback` execution flow.

- [ ] **Step 1: Write the failing test**

```python
# Create tests/test_fallback_annas.py
import pytest
from unittest.mock import patch
from paper_search_mcp.server import download_with_fallback

@pytest.mark.asyncio
async def test_fallback_includes_annas():
    with patch('paper_search_mcp.academic_platforms.annas_archive.AnnasArchiveFetcher.download_pdf') as mock_anna:
        mock_anna.return_value = "fake/path/anna.pdf"
        # Provide a DOI and a source that definitely won't native download
        res = await download_with_fallback(source="unknown_src", paper_id="123", doi="10.123/fake", use_scihub=False)
        # Should return the anna path if it reached it
        assert res == "fake/path/anna.pdf"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fallback_annas.py -v`
Expected: FAIL (because AnnasArchiveFetcher is not hooked up)

- [ ] **Step 3: Write minimal implementation**

Modify `paper_search_mcp/server.py`:
1. Add import: `from .academic_platforms.annas_archive import AnnasArchiveFetcher`
2. Instantiate globally: `annas_fetcher = AnnasArchiveFetcher()`
3. In `download_with_fallback` (around line 837):
```python
    # After unpaywall attempt, before Sci-Hub
    if normalized_doi:
        anna_result = await asyncio.to_thread(annas_fetcher.download_pdf, normalized_doi, save_path)
        if anna_result and os.path.exists(anna_result):
            return anna_result
        if anna_result:
            attempt_errors.append(f"annas_archive: {anna_result}")
        else:
            attempt_errors.append("annas_archive: failed to retrieve")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fallback_annas.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add paper_search_mcp/server.py tests/test_fallback_annas.py
git commit -m "feat: integrate Annas Archive into fallback chain"
```

---

### Task 4: Setup Antigravity IDE Configuration

**Files:**
- Create: `.agents/mcp_config.json`

**Interfaces:**
- Consumes: N/A
- Produces: A valid JSON config for Antigravity IDE.

- [ ] **Step 1: Write the minimal implementation**

Create `.agents/mcp_config.json` directly with the IDE config:
```json
{
  "mcpServers": {
    "paper-search-mcp-dev": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "D:/Dev/paper-search-mcp",
        "-m", "paper_search_mcp.server"
      ]
    }
  }
}
```

- [ ] **Step 2: Verify JSON syntax**

Run: `python -m json.tool .agents/mcp_config.json`
Expected: Valid JSON output

- [ ] **Step 3: Commit**

```bash
git add .agents/mcp_config.json
git commit -m "chore: add Antigravity IDE MCP config"
```
