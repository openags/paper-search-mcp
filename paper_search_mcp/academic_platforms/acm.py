"""ACM Digital Library connector — keyless.

Since 1 January 2026 the entire ACM Digital Library is open access, and ACM
does not offer a public search API, so no API key exists for this source.
Search is served from Crossref restricted to ACM's DOI prefix (``10.1145``),
which covers every ACM-published work with full bibliographic metadata.

PDFs live at ``https://dl.acm.org/doi/pdf/<doi>`` and are free to read, but
dl.acm.org sits behind a Cloudflare browser challenge that rejects scripted
clients.  ``download_pdf`` tries the direct link first and, when blocked,
raises with the browser URL so the user (or ``download_with_fallback``) can
take over.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import requests

from .crossref import CrossRefSearcher
from ..paper import Paper

logger = logging.getLogger(__name__)

ACM_DOI_PREFIX = "10.1145"


class ACMSearcher(CrossRefSearcher):
    """ACM Digital Library search via Crossref's ACM DOI prefix."""

    PDF_URL_TEMPLATE = "https://dl.acm.org/doi/pdf/{doi}"
    PAGE_URL_TEMPLATE = "https://dl.acm.org/doi/{doi}"

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        prefix_filter = f"prefix:{ACM_DOI_PREFIX}"
        extra_filter = kwargs.pop("filter", "")
        kwargs["filter"] = f"{prefix_filter},{extra_filter}" if extra_filter else prefix_filter

        papers = super().search(query, max_results=max_results, **kwargs)
        for paper in papers:
            self._to_acm(paper)
        return papers

    def get_paper_by_doi(self, doi: str) -> Optional[Paper]:
        paper = super().get_paper_by_doi(doi)
        if paper is not None:
            self._to_acm(paper)
        return paper

    def _to_acm(self, paper: Paper) -> None:
        paper.source = "acm"
        if paper.doi:
            paper.url = self.PAGE_URL_TEMPLATE.format(doi=paper.doi)
            paper.pdf_url = self.PDF_URL_TEMPLATE.format(doi=paper.doi)

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """Download an ACM PDF by DOI (``10.1145/...``).

        Raises:
            ValueError: If ``paper_id`` is not an ACM DOI.
            IOError: If dl.acm.org blocks the scripted request.
        """
        doi = paper_id.strip().removeprefix("https://doi.org/")
        if not doi.startswith(f"{ACM_DOI_PREFIX}/"):
            raise ValueError(f"Not an ACM DOI (expected {ACM_DOI_PREFIX}/...): {paper_id}")

        pdf_url = self.PDF_URL_TEMPLATE.format(doi=doi)
        response = requests.get(
            pdf_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=30,
        )
        if response.status_code != 200 or not response.content.startswith(b"%PDF"):
            raise IOError(
                f"dl.acm.org blocked the automated download (HTTP {response.status_code}, "
                f"Cloudflare browser check). The paper is free to read: open {pdf_url} in a "
                f"browser, or call download_with_fallback(source='acm', paper_id='{doi}', "
                f"doi='{doi}') to try open repositories (arXiv, OpenAIRE, CORE, Unpaywall)."
            )

        os.makedirs(save_path, exist_ok=True)
        output_path = os.path.join(save_path, f"acm_{doi.replace('/', '_')}.pdf")
        with open(output_path, "wb") as file_obj:
            file_obj.write(response.content)
        return output_path

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        from pypdf import PdfReader

        pdf_path = self.download_pdf(paper_id, save_path)
        reader = PdfReader(pdf_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
