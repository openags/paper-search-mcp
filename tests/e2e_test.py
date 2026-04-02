"""
End-to-end tests: search → download → read content for every supported platform.
Run with: python tests/e2e_test.py
"""
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_search_mcp.config import get_env

SAVE_PATH = "/tmp/paper_e2e_test"
os.makedirs(SAVE_PATH, exist_ok=True)

results = {}

SEP = "=" * 70
SUBSEP = "-" * 50

def ok(msg):   print(f"    ✅ {msg}")
def warn(msg): print(f"    ⚠️  {msg}")
def err(msg):  print(f"    ❌ {msg}")

def check_search(name, papers, optional=False):
    if not papers:
        if optional:
            warn("search returned 0 results (upstream unavailable/rate-limited), marking N/A")
            return None, None
        err(f"search returned 0 results")
        return False, None
    p = papers[0]
    ok(f"search: {len(papers)} results | '{p.title[:55]}'")
    ok(f"  authors={p.authors[:2]} | date={p.published_date} | doi={p.doi or 'N/A'}")
    return True, p

def check_download(name, fn, paper_id):
    try:
        path = fn(paper_id, SAVE_PATH)
        if os.path.isfile(path) and os.path.getsize(path) > 1024:
            ok(f"download: saved to {path} ({os.path.getsize(path)//1024}KB)")
            return True, path
        else:
            warn(f"download returned path but file missing/tiny: {path}")
            return False, None
    except Exception as e:
        err(f"download exception: {type(e).__name__}: {e}")
        return False, None

def check_read(name, fn, paper_id):
    try:
        text = fn(paper_id, SAVE_PATH)
        if text and len(text) > 200:
            ok(f"read: {len(text)} chars extracted")
            ok(f"  preview: {text[:120].strip()!r}")
            return True
        else:
            warn(f"read returned short/empty text ({len(text) if text else 0} chars): {repr(text)[:80]}")
            return False
    except Exception as e:
        err(f"read exception: {type(e).__name__}: {e}")
        return False


print(SEP)
print("End-to-End Tests: Search → Download → Read")
print(SEP)

# ── 1. arXiv ─────────────────────────────────────────────────────────────────
print("\n[1] arXiv")
from paper_search_mcp.academic_platforms.arxiv import ArxivSearcher
s = ArxivSearcher()
ok_s, paper = check_search("arxiv", s.search("attention mechanism", max_results=2), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    ok_d, _ = check_download("arxiv", s.download_pdf, paper.paper_id)
    if ok_d:
        ok_r = check_read("arxiv", s.read_paper, paper.paper_id)
results["arxiv"] = (ok_s, ok_d, ok_r)

# ── 2. PubMed ─────────────────────────────────────────────────────────────────
print("\n[2] PubMed  (download not supported)")
from paper_search_mcp.academic_platforms.pubmed import PubMedSearcher
s = PubMedSearcher()
ok_s, paper = check_search("pubmed", s.search("transformer neural network", max_results=2))
ok_d = False
ok_r = False
if ok_s:
    try:
        s.download_pdf(paper.paper_id, SAVE_PATH)
        err("download should have raised NotImplementedError")
    except NotImplementedError as e:
        ok(f"download correctly raises NotImplementedError: {str(e)[:60]}")
        ok_d = True
    msg = s.read_paper(paper.paper_id)
    if msg and len(msg) > 10:
        ok(f"read returns informational message: {repr(msg[:80])}")
        ok_r = True
    else:
        warn(f"read returned unexpected value: {repr(msg)}")
results["pubmed"] = (ok_s, ok_d, ok_r)

# ── 3. bioRxiv ────────────────────────────────────────────────────────────────
print("\n[3] bioRxiv  (search by category; download may be slow)")
from paper_search_mcp.academic_platforms.biorxiv import BioRxivSearcher
s = BioRxivSearcher()
ok_s, paper = check_search("biorxiv", s.search("bioinformatics", max_results=2, days=30), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    ok_d, _ = check_download("biorxiv", s.download_pdf, paper.paper_id)
    if ok_d:
        ok_r = check_read("biorxiv", s.read_paper, paper.paper_id)
results["biorxiv"] = (ok_s, ok_d, ok_r)

# ── 4. medRxiv ────────────────────────────────────────────────────────────────
print("\n[4] medRxiv  (search by category; download may be slow)")
from paper_search_mcp.academic_platforms.medrxiv import MedRxivSearcher
s = MedRxivSearcher()
ok_s, paper = check_search("medrxiv", s.search("infectious_diseases", max_results=2, days=30), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    ok_d, _ = check_download("medrxiv", s.download_pdf, paper.paper_id)
    if ok_d:
        ok_r = check_read("medrxiv", s.read_paper, paper.paper_id)
results["medrxiv"] = (ok_s, ok_d, ok_r)

# ── 5. Google Scholar ─────────────────────────────────────────────────────────
print("\n[5] Google Scholar  (download not supported)")
from paper_search_mcp.academic_platforms.google_scholar import GoogleScholarSearcher
s = GoogleScholarSearcher()
ok_s, paper = check_search("google_scholar", s.search("deep learning survey", max_results=2), optional=True)
ok_d = None if ok_s is None else False
ok_r = None
if ok_s:
    try:
        s.download_pdf(paper.paper_id, SAVE_PATH)
        err("download should have raised NotImplementedError")
    except NotImplementedError:
        ok("download correctly raises NotImplementedError")
        ok_d = True
        ok("read is not supported for Google Scholar results")
results["google_scholar"] = (ok_s, ok_d, ok_r)

# ── 6. IACR ───────────────────────────────────────────────────────────────────
print("\n[6] IACR  (fetch_details=False for speed; download uses paper_id)")
from paper_search_mcp.academic_platforms.iacr import IACRSearcher
s = IACRSearcher()
ok_s, paper = check_search("iacr", s.search("zero knowledge proof", max_results=2, fetch_details=False))
ok_d = ok_r = False
if ok_s:
    ok_d, _ = check_download("iacr", s.download_pdf, paper.paper_id)
    if ok_d:
        ok_r = check_read("iacr", s.read_paper, paper.paper_id)
results["iacr"] = (ok_s, ok_d, ok_r)

# ── 7. Semantic Scholar ───────────────────────────────────────────────────────
print("\n[7] Semantic Scholar  (download requires openAccessPdf)")
from paper_search_mcp.academic_platforms.semantic import SemanticSearcher
s = SemanticSearcher()
papers = s.search("BERT language model", max_results=5)
ok_s, paper = check_search("semantic", papers, optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    # Find first paper that has a pdf_url (open access)
    pdf_paper = next((p for p in papers if p.pdf_url), None)
    if pdf_paper:
        ok(f"  Using paper with open access PDF: '{pdf_paper.title[:50]}'")
        ok_d, _ = check_download("semantic", s.download_pdf, pdf_paper.paper_id)
        if ok_d:
            ok_r = check_read("semantic", s.read_paper, pdf_paper.paper_id)
    else:
        warn("No open-access PDF found in results; skip download/read test")
        ok_d = ok_r = None  # N/A
results["semantic"] = (ok_s, ok_d, ok_r)

# ── 8. CrossRef ───────────────────────────────────────────────────────────────
print("\n[8] CrossRef  (download not supported)")
from paper_search_mcp.academic_platforms.crossref import CrossRefSearcher
s = CrossRefSearcher()
ok_s, paper = check_search("crossref", s.search("graph neural network", max_results=2))
ok_d = ok_r = False
if ok_s:
    try:
        s.download_pdf(paper.paper_id, SAVE_PATH)
        err("download should have raised NotImplementedError")
    except NotImplementedError:
        ok("download correctly raises NotImplementedError")
        ok_d = True
    msg = s.read_paper(paper.paper_id)
    if msg and len(msg) > 10:
        ok(f"read returns informational message: {repr(msg[:80])}")
        ok_r = True
results["crossref"] = (ok_s, ok_d, ok_r)

# ── 9. OpenAlex ───────────────────────────────────────────────────────────────
print("\n[9] OpenAlex  (download not supported natively)")
from paper_search_mcp.academic_platforms.openalex import OpenAlexSearcher
s = OpenAlexSearcher()
ok_s, paper = check_search("openalex", s.search("vision transformers", max_results=2))
ok_d = ok_r = False
if ok_s:
    try:
        s.download_pdf(paper.paper_id, SAVE_PATH)
        err("download should have raised NotImplementedError")
    except NotImplementedError:
        ok("download correctly raises NotImplementedError")
        ok_d = True
    msg = s.read_paper(paper.paper_id)
    if msg and len(msg) > 10:
        ok(f"read returns informational message: {repr(msg[:80])}")
        ok_r = True
results["openalex"] = (ok_s, ok_d, ok_r)

# ── 10. PubMed Central (PMC) ───────────────────────────────────────────────────────────────
print("\n[10] PubMed Central (PMC)  (open access PDF download)")
from paper_search_mcp.academic_platforms.pmc import PMCSearcher
s = PMCSearcher()
papers = s.search("cancer immunotherapy", max_results=5)
ok_s, paper = check_search("pmc", papers, optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    candidates = [p for p in papers if p.pdf_url] or papers
    ok_d = False
    for candidate in candidates:
        ok_d, _ = check_download("pmc", s.download_pdf, candidate.paper_id)
        if ok_d:
            ok_r = check_read("pmc", s.read_paper, candidate.paper_id)
            break
    if not ok_d:
        warn("No PMC candidate with accessible PDF found; marking download/read as N/A")
        ok_d = ok_r = None
results["pmc"] = (ok_s, ok_d, ok_r)

# ── 11. CORE ───────────────────────────────────────────────────────────────────────────────
print("\n[11] CORE  (requires API key for full functionality)")
from paper_search_mcp.academic_platforms.core import CORESearcher
s = CORESearcher()
papers = s.search("machine learning", max_results=5)
ok_s, paper = check_search("core", papers, optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    if not get_env("CORE_API_KEY", ""):
        warn("CORE_API_KEY not set; skipping CORE download/read test")
        ok_d = ok_r = None
    else:
        candidates = [p for p in papers if p.pdf_url] or papers
        ok_d = False
        for candidate in candidates:
            ok_d, _ = check_download("core", s.download_pdf, candidate.paper_id)
            if ok_d:
                ok_r = check_read("core", s.read_paper, candidate.paper_id)
                break
        if not ok_d:
            warn("No downloadable CORE candidate found; marking download/read as N/A")
            ok_d = ok_r = None
results["core"] = (ok_s, ok_d, ok_r)

# ── 12. Europe PMC ─────────────────────────────────────────────────────────────────────────
print("\n[12] Europe PMC  (biomedical literature)")
from paper_search_mcp.academic_platforms.europepmc import EuropePMCSearcher
s = EuropePMCSearcher()
papers = s.search("genomics", max_results=5)
ok_s, paper = check_search("europepmc", papers)
ok_d = ok_r = False
if ok_s:
    candidates = [p for p in papers if p.pdf_url] or papers
    ok_d = False
    for candidate in candidates:
        ok_d, _ = check_download("europepmc", s.download_pdf, candidate.paper_id)
        if ok_d:
            ok_r = check_read("europepmc", s.read_paper, candidate.paper_id)
            break
    if not ok_d:
        warn("No Europe PMC candidate with accessible PDF found; marking download/read as N/A")
        ok_d = ok_r = None
results["europepmc"] = (ok_s, ok_d, ok_r)

# ── 13. dblp ─────────────────────────────────────────────────────────────────
print("\n[13] dblp  (metadata source; download not supported)")
from paper_search_mcp.academic_platforms.dblp import DBLPSearcher
s = DBLPSearcher()
ok_s, paper = check_search("dblp", s.search("machine learning", max_results=2), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    try:
        s.download_pdf(paper.paper_id, SAVE_PATH)
        err("download should have raised NotImplementedError")
    except NotImplementedError:
        ok("download correctly raises NotImplementedError")
        ok_d = True
    try:
        s.read_paper(paper.paper_id, SAVE_PATH)
        err("read should have raised NotImplementedError")
    except NotImplementedError:
        ok("read correctly raises NotImplementedError")
        ok_r = True
results["dblp"] = (ok_s, ok_d, ok_r)

# ── 14. OpenAIRE ─────────────────────────────────────────────────────────────
print("\n[14] OpenAIRE  (search only; direct download/read not supported)")
from paper_search_mcp.academic_platforms.openaire import OpenAiresearcher
s = OpenAiresearcher()
ok_s, paper = check_search("openaire", s.search("climate change", max_results=2))
ok_d = ok_r = False
if ok_s:
    try:
        s.download_pdf(paper.paper_id, SAVE_PATH)
        err("download should have raised NotImplementedError")
    except NotImplementedError:
        ok("download correctly raises NotImplementedError")
        ok_d = True

    try:
        s.read_paper(paper.paper_id, SAVE_PATH)
        err("read should have raised NotImplementedError")
    except NotImplementedError:
        ok("read correctly raises NotImplementedError")
        ok_r = True
results["openaire"] = (ok_s, ok_d, ok_r)

# ── 15. CiteSeerX ────────────────────────────────────────────────────────────
print("\n[15] CiteSeerX  (download depends on source PDF availability)")
from paper_search_mcp.academic_platforms.citeseerx import CiteSeerXSearcher
s = CiteSeerXSearcher()
ok_s, paper = check_search("citeseerx", s.search("machine learning", max_results=2), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    read_msg = s.read_paper(paper.paper_id, SAVE_PATH)
    if isinstance(read_msg, str):
        ok(f"read returned text/message: {repr(read_msg[:80])}")
        ok_r = True

    if paper.pdf_url:
        ok_d, _ = check_download("citeseerx", s.download_pdf, paper.paper_id)
    else:
        warn("No PDF URL in selected CiteSeerX result; skipping download check")
        ok_d = None
results["citeseerx"] = (ok_s, ok_d, ok_r)

# ── 16. DOAJ ─────────────────────────────────────────────────────────────────
print("\n[16] DOAJ  (open access journal articles)")
from paper_search_mcp.academic_platforms.doaj import DOAJSearcher
s = DOAJSearcher()
papers = s.search("machine learning", max_results=5)
ok_s, paper = check_search("doaj", papers, optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    candidates = [p for p in papers if p.pdf_url] or papers
    ok_d = False
    for candidate in candidates:
        if not candidate.pdf_url:
            continue
        ok_d, _ = check_download("doaj", s.download_pdf, candidate.paper_id)
        if ok_d:
            ok_r = check_read("doaj", s.read_paper, candidate.paper_id)
            break
    if not ok_d:
        warn("DOAJ result has no direct PDF URL; skipping download/read")
        ok_d = ok_r = None
results["doaj"] = (ok_s, ok_d, ok_r)

# ── 17. BASE ─────────────────────────────────────────────────────────────────
print("\n[17] BASE  (OAI-PMH metadata, PDF availability varies)")
from paper_search_mcp.academic_platforms.base_search import BASESearcher
s = BASESearcher()
ok_s, paper = check_search("base", s.search("machine learning", max_results=2), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    if paper.pdf_url:
        ok_d, _ = check_download("base", s.download_pdf, paper.paper_id)
        if ok_d:
            ok_r = check_read("base", s.read_paper, paper.paper_id)
    else:
        warn("BASE result has no direct PDF URL; skipping download/read")
        ok_d = ok_r = None
results["base"] = (ok_s, ok_d, ok_r)

# ── 18. Zenodo ───────────────────────────────────────────────────────────────
print("\n[18] Zenodo  (open repository, PDF availability varies)")
from paper_search_mcp.academic_platforms.zenodo import ZenodoSearcher
s = ZenodoSearcher()
ok_s, paper = check_search("zenodo", s.search("machine learning", max_results=2), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    if paper.pdf_url:
        ok_d, _ = check_download("zenodo", s.download_pdf, paper.paper_id)
        if ok_d:
            ok_r = check_read("zenodo", s.read_paper, paper.paper_id)
    else:
        warn("Zenodo result has no direct PDF URL; skipping download/read")
        ok_d = ok_r = None
results["zenodo"] = (ok_s, ok_d, ok_r)

# ── 19. HAL ──────────────────────────────────────────────────────────────────
print("\n[19] HAL  (open archive, PDF availability varies)")
from paper_search_mcp.academic_platforms.hal import HALSearcher
s = HALSearcher()
ok_s, paper = check_search("hal", s.search("machine learning", max_results=2), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    if paper.pdf_url:
        ok_d, _ = check_download("hal", s.download_pdf, paper.paper_id)
        if ok_d is False:
            # HAL PDFs are often under embargo/metadata-only; treat as N/A not failure
            warn("HAL PDF not accessible (embargo or metadata-only); marking N/A")
            ok_d = ok_r = None
        elif ok_d:
            ok_r = check_read("hal", s.read_paper, paper.paper_id)
    else:
        warn("HAL result has no direct PDF URL; skipping download/read")
        ok_d = ok_r = None
results["hal"] = (ok_s, ok_d, ok_r)

# ── 20. SSRN ─────────────────────────────────────────────────────────────────
print("\n[20] SSRN  (best-effort download/read; often login-dependent)")
from paper_search_mcp.academic_platforms.ssrn import SSRNSearcher
s = SSRNSearcher()
ok_s, paper = check_search("ssrn", s.search("machine learning", max_results=2), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    dl_result = s.download_pdf(paper.paper_id, SAVE_PATH)
    if isinstance(dl_result, str) and dl_result.endswith(".pdf") and os.path.exists(dl_result):
        ok(f"download succeeded: {dl_result}")
        ok_d = True
    else:
        warn(f"download returned message (expected in many SSRN cases): {repr(str(dl_result)[:120])}")
        ok_d = None

    read_result = s.read_paper(paper.paper_id, SAVE_PATH)
    if isinstance(read_result, str) and len(read_result) > 200:
        ok("read extracted text")
        ok_r = True
    else:
        warn(f"read returned message/short text (expected in many SSRN cases): {repr(str(read_result)[:120])}")
        ok_r = None
results["ssrn"] = (ok_s, ok_d, ok_r)

# ── 21. Unpaywall ────────────────────────────────────────────────────────────
print("\n[21] Unpaywall  (DOI metadata and OA links; no direct download/read)")
from paper_search_mcp.academic_platforms.unpaywall import UnpaywallSearcher
s = UnpaywallSearcher()
ok_s, paper = check_search("unpaywall", s.search("10.1038/nature12373", max_results=1), optional=True)
ok_d = ok_r = (None if ok_s is None else False)
if ok_s:
    try:
        s.download_pdf(paper.paper_id, SAVE_PATH)
        err("download should have raised NotImplementedError")
    except NotImplementedError:
        ok("download correctly raises NotImplementedError")
        ok_d = True

    try:
        s.read_paper(paper.paper_id, SAVE_PATH)
        err("read should have raised NotImplementedError")
    except NotImplementedError:
        ok("read correctly raises NotImplementedError")
        ok_r = True
results["unpaywall"] = (ok_s, ok_d, ok_r)


# ── Summary ───────────────────────────────────────────────────────────────────
def icon(v):
    if v is True:  return "✅"
    if v is False: return "❌"
    return "➖"  # N/A

print("\n" + SEP)
print(f"{'Platform':<20} {'Search':^8} {'Download':^10} {'Read':^8}")
print(SUBSEP)
for name, (s, d, r) in results.items():
    print(f"  {name:<18} {icon(s):^8} {icon(d):^10} {icon(r):^8}")
print(SEP)
print(f"Files saved to: {SAVE_PATH}")
