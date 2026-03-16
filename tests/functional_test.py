"""
Functional test script to verify all academic platform searchers work correctly.
Run with: python tests/functional_test.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

QUERY = "machine learning"
MAX_RESULTS = 3
results = {}

def test_platform(name, fn, optional: bool = False):
    try:
        papers = fn()
        if papers and len(papers) > 0:
            p = papers[0]
            print(f"  [OK] {name}: {len(papers)} results, first: '{p.title[:60]}'")
            results[name] = ("OK", len(papers))
        else:
            if optional:
                print(f"  [SKIP] {name}: returned 0 results (upstream unavailable/rate-limited)")
                results[name] = ("SKIP", 0)
            else:
                print(f"  [EMPTY] {name}: returned 0 results")
                results[name] = ("EMPTY", 0)
    except Exception as e:
        if optional:
            print(f"  [SKIP] {name}: upstream error ({type(e).__name__}: {e})")
            results[name] = ("SKIP", str(e))
        else:
            print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
            results[name] = ("ERROR", str(e))


print("=" * 70)
print("Functional tests for paper-search-mcp")
print("=" * 70)

# ── arXiv ────────────────────────────────────────────────────────────────────
print("\n[1] arXiv")
from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher
test_platform("arxiv.search", lambda: ArxivSearcher().search(QUERY, max_results=MAX_RESULTS))

# ── PubMed ───────────────────────────────────────────────────────────────────
print("\n[2] PubMed")
from paper_search_mcp.academic_platforms.pubmed import PubMedSearcher
test_platform("pubmed.search", lambda: PubMedSearcher().search(QUERY, max_results=MAX_RESULTS))

# ── bioRxiv ──────────────────────────────────────────────────────────────────
print("\n[3] bioRxiv  (uses category + date range, not keyword search)")
from paper_search_mcp.academic_platforms.biorxiv import BioRxivSearcher
test_platform("biorxiv.search", lambda: BioRxivSearcher().search("bioinformatics", max_results=MAX_RESULTS, days=30))

# ── medRxiv ──────────────────────────────────────────────────────────────────
print("\n[4] medRxiv  (uses category + date range, not keyword search)")
from paper_search_mcp.academic_platforms.medrxiv import MedRxivSearcher
test_platform("medrxiv.search", lambda: MedRxivSearcher().search("infectious_diseases", max_results=MAX_RESULTS, days=30))

# ── Google Scholar ────────────────────────────────────────────────────────────
print("\n[5] Google Scholar  (may be blocked by bot-detection)")
from paper_search_mcp.academic_platforms.google_scholar import GoogleScholarSearcher
test_platform("google_scholar.search", lambda: GoogleScholarSearcher().search(QUERY, max_results=MAX_RESULTS), optional=True)

# ── IACR ─────────────────────────────────────────────────────────────────────
print("\n[6] IACR  (fetch_details=False for speed)")
from paper_search_mcp.academic_platforms.iacr import IACRSearcher
test_platform("iacr.search", lambda: IACRSearcher().search("cryptography", max_results=MAX_RESULTS, fetch_details=False))

# ── Semantic Scholar ──────────────────────────────────────────────────────────
print("\n[7] Semantic Scholar")
from paper_search_mcp.academic_platforms.semantic import SemanticSearcher
test_platform("semantic.search", lambda: SemanticSearcher().search(QUERY, max_results=MAX_RESULTS), optional=True)

# ── CrossRef ──────────────────────────────────────────────────────────────────
print("\n[8] CrossRef")
from paper_search_mcp.academic_platforms.crossref import CrossRefSearcher
test_platform("crossref.search", lambda: CrossRefSearcher().search(QUERY, max_results=MAX_RESULTS))

# ── 9. PubMed Central (PMC) ──────────────────────────────────────────────────────────────
print("\n[9] PubMed Central (PMC)")
from paper_search_mcp.academic_platforms.pmc import PMCSearcher
test_platform("pmc.search", lambda: PMCSearcher().search(QUERY, max_results=MAX_RESULTS), optional=True)

# ── 10. CORE ─────────────────────────────────────────────────────────────────────────────
print("\n[10] CORE  (requires API key for full functionality)")
from paper_search_mcp.academic_platforms.core import CORESearcher
test_platform("core.search", lambda: CORESearcher().search(QUERY, max_results=MAX_RESULTS))

# ── 11. Europe PMC ───────────────────────────────────────────────────────────────────────
print("\n[11] Europe PMC  (biomedical literature)")
from paper_search_mcp.academic_platforms.europepmc import EuropePMCSearcher
test_platform("europepmc.search", lambda: EuropePMCSearcher().search(QUERY, max_results=MAX_RESULTS))

# ── 12. dblp ─────────────────────────────────────────────────────────────────────────────
print("\n[12] dblp  (computer science metadata)")
from paper_search_mcp.academic_platforms.dblp import DBLPSearcher
test_platform("dblp.search", lambda: DBLPSearcher().search(QUERY, max_results=MAX_RESULTS), optional=True)

# ── 13. OpenAIRE ─────────────────────────────────────────────────────────────────────────
print("\n[13] OpenAIRE  (European open access infrastructure)")
from paper_search_mcp.academic_platforms.openaire import OpenAiresearcher
test_platform("openaire.search", lambda: OpenAiresearcher().search(QUERY, max_results=MAX_RESULTS))

# ── 14. CiteSeerX ────────────────────────────────────────────────────────────────────────
print("\n[14] CiteSeerX  (computer science digital library)")
from paper_search_mcp.academic_platforms.citeseerx import CiteSeerXSearcher
test_platform("citeseerx.search", lambda: CiteSeerXSearcher().search(QUERY, max_results=MAX_RESULTS), optional=True)

# ── 15. DOAJ ─────────────────────────────────────────────────────────────────────────────
print("\n[15] DOAJ  (open access journals)")
from paper_search_mcp.academic_platforms.doaj import DOAJSearcher
test_platform("doaj.search", lambda: DOAJSearcher().search(QUERY, max_results=MAX_RESULTS))

# ── 16. BASE ─────────────────────────────────────────────────────────────────────────────
print("\n[16] BASE  (OAI-PMH academic repositories)")
from paper_search_mcp.academic_platforms.base_search import BASESearcher
test_platform("base.search", lambda: BASESearcher().search(QUERY, max_results=MAX_RESULTS), optional=True)

# ── 17. Zenodo ───────────────────────────────────────────────────────────────────────────
print("\n[17] Zenodo  (open repository)")
from paper_search_mcp.academic_platforms.zenodo import ZenodoSearcher
test_platform("zenodo.search", lambda: ZenodoSearcher().search(QUERY, max_results=MAX_RESULTS))

# ── 18. HAL ──────────────────────────────────────────────────────────────────────────────
print("\n[18] HAL  (French open archive)")
from paper_search_mcp.academic_platforms.hal import HALSearcher
test_platform("hal.search", lambda: HALSearcher().search(QUERY, max_results=MAX_RESULTS))

# ── 19. SSRN ─────────────────────────────────────────────────────────────────────────────
print("\n[19] SSRN  (social sciences; best-effort full-text)")
from paper_search_mcp.academic_platforms.ssrn import SSRNSearcher
test_platform("ssrn.search", lambda: SSRNSearcher().search(QUERY, max_results=MAX_RESULTS), optional=True)

# ── 20. Unpaywall ────────────────────────────────────────────────────────────────────────
print("\n[20] Unpaywall  (DOI-based OA metadata; requires UNPAYWALL_EMAIL)")
from paper_search_mcp.academic_platforms.unpaywall import UnpaywallSearcher
test_platform("unpaywall.search", lambda: UnpaywallSearcher().search("10.1038/nature12373", max_results=1), optional=True)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("Summary:")
for name, (status, detail) in results.items():
    icon = "✅" if status == "OK" else ("➖" if status == "SKIP" else ("⚠️ " if status == "EMPTY" else "❌"))
    print(f"  {icon}  {name}: {status} ({detail})")
print("=" * 70)
