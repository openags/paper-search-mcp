from typing import List
from datetime import datetime
import requests
import logging
from ..paper import Paper
from .base import PaperSource
from ..utils import extract_doi
from ..config import get_env

logger = logging.getLogger(__name__)


def _work_id(value: str) -> str:
    return (value or "").removeprefix("https://openalex.org/")



class OpenAlexSearcher(PaperSource):
    """OpenAlex paper search implementation"""

    BASE_URL = "https://api.openalex.org/works"
    DEFAULT_USER_AGENT = "paper-search-mcp/1.0"
    DEFAULT_EMAIL = "openags@example.com"

    def __init__(
        self,
        api_key: str | None = None,
        email: str | None = None,
    ):
        self.session = requests.Session()
        self.api_key = (
            api_key if api_key is not None else get_env("OPENALEX_API_KEY", "")
        ).strip()
        self.email = (
            email if email is not None else get_env("OPENALEX_EMAIL", self.DEFAULT_EMAIL)
        ).strip()

        user_agent = self.DEFAULT_USER_AGENT
        if self.email:
            user_agent = f"{user_agent} (mailto:{self.email})"
        self.session.headers.update({"User-Agent": user_agent})
        if self.api_key:
            # Keep credentials out of URLs and proxy/access logs. OpenAlex
            # supports Bearer authentication as an alternative to api_key=.
            self.session.headers.update(
                {"Authorization": f"Bearer {self.api_key}"}
            )

    def _reconstruct_abstract(self, inverted_index: dict) -> str:
        """
        OpenAlex provides abstracts as an inverted index to save space.
        This function reconstructs the original abstract text.
        """
        if not inverted_index:
            return ""
        try:
            word_positions = []
            for word, positions in inverted_index.items():
                for pos in positions:
                    word_positions.append((pos, word))
            # Sort by position
            word_positions.sort(key=lambda x: x[0])
            return " ".join([word for _, word in word_positions])
        except Exception as e:
            logger.warning(f"Error reconstructing OpenAlex abstract: {e}")
            return ""

    def _parse_work(self, item: dict) -> Paper | None:
        """Map one OpenAlex work object to a Paper. Returns None if it has no title."""
        if not isinstance(item, dict):
            return None
        # ID usually looks like 'https://openalex.org/W2741809807'
        paper_id = _work_id(item.get("id", ""))
        title = item.get("title")
        if not title:
            return None  # Skip items without a title

        # Process Authors
        # Every `or <default>` below is load-bearing: OpenAlex returns JSON null for
        # absent fields, so .get(key, default) yields None rather than the default.
        authors = [
            (author.get("author") or {}).get("display_name", "")
            for author in (item.get("authorships") or [])
            if isinstance(author, dict) and (author.get("author") or {}).get("display_name")
        ]

        # Abstract
        abstract = self._reconstruct_abstract(item.get("abstract_inverted_index"))

        # Process DOI
        doi = item.get("doi") or ""
        if doi:
            # OpenAlex DOI is returned as a full url e.g. https://doi.org/10...
            doi = doi.replace("https://doi.org/", "")

        if not doi and abstract:
            doi = extract_doi(abstract)

        # Process URLs (Landing page vs direct PDF)
        url = ""
        pdf_url = ""

        primary_location = item.get("primary_location")
        if primary_location:
            url = primary_location.get("landing_page_url", "")
            pdf_url = primary_location.get("pdf_url", "")

        if not url:
            url = item.get("id", "")

        # best_oa_location points at whichever repository actually hosts an open copy,
        # which is often not the primary location.
        if not pdf_url:
            best_oa = item.get("best_oa_location") or {}
            pdf_url = best_oa.get("pdf_url") or ""

        # Everything above is a field OpenAlex asserts to be a PDF. oa_url below is only
        # the "best free link", which is frequently a landing page -- worth having, but
        # callers must not be told it is a file.
        pdf_is_direct = bool(pdf_url)

        # Check general open access availability for PDF fallback
        open_access = item.get("open_access") or {}
        if not pdf_url and open_access.get("is_oa"):
            pdf_url = open_access.get("oa_url", "")

        # Dates
        pub_date_str = item.get("publication_date")
        published_date = None
        if pub_date_str:
            try:
                published_date = datetime.strptime(pub_date_str, "%Y-%m-%d")
            except (TypeError, ValueError):
                pass

        # Categories / Concepts
        concepts = [
            concept.get("display_name")
            for concept in (item.get("concepts") or [])
            if isinstance(concept, dict) and concept.get("display_name")
        ]

        return Paper(
            paper_id=paper_id,
            title=title,
            authors=authors,
            abstract=abstract,
            url=url,
            pdf_url=pdf_url or "",
            published_date=published_date,
            source="openalex",
            categories=concepts[:5],  # Keep top 5 concepts to reduce size
            doi=doi,
            citations=item.get("cited_by_count") or 0,
            references=[
                _work_id(ref)
                for ref in (item.get("referenced_works") or [])
                if isinstance(ref, str) and _work_id(ref)
            ],
            # OpenAlex's own openness verdict, kept separate from pdf_url because a
            # paper can be open access while the only indexed link is a landing page.
            extra={
                "is_oa": bool(open_access.get("is_oa")),
                "pdf_is_direct": pdf_is_direct,
            },
        )


    def search(
        self,
        query: str,
        max_results: int = 10,
        filter: str = "",
    ) -> List[Paper]:
        """
        Search OpenAlex works. Uses the 'search' filter.

        Args:
            query: Search query string
            max_results: Maximum results to return (natively max 100 per page)
            filter: Optional OpenAlex works filter expression.

        Returns:
            List[Paper]: List of found papers with metadata.
        """
        papers = []

        try:
            params = {
                "search": query,
                "per_page": min(max_results, 100),
            }
            filter_value = (filter or "").strip()
            if filter_value:
                params["filter"] = filter_value

            response = self.session.get(self.BASE_URL, params=params, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"OpenAlex search failed with status {response.status_code}")
                return papers

            data = response.json()
            results = data.get("results", [])

            for item in results:
                if len(papers) >= max_results:
                    break

                paper = self._parse_work(item)
                if paper is not None:
                    papers.append(paper)

        except Exception as e:
            logger.error(f"OpenAlex search error: {e}")

        return papers

    SSRN_SOURCE_ID = "S4210172589"
    SSRN_PAGE_SIZE = 100
    SSRN_MAX_RESULTS = 1000
    SSRN_MAX_PAGES = 20

    @staticmethod
    def _extract_ssrn_abstract_id(value: str) -> str:
        """Accept SSRN locators only, never a lookalike host or arbitrary URL."""
        if not isinstance(value, str):
            return ""
        from urllib.parse import parse_qs, urlparse
        import re
        try:
            url = urlparse(value.strip())
            if url.scheme not in {"http", "https"} or url.username or url.password or url.port:
                return ""
            if url.hostname in {"doi.org", "dx.doi.org"}:
                match = re.fullmatch(r"/10\.2139/ssrn\.([0-9]+)", url.path, re.IGNORECASE)
                return match.group(1) if match else ""
            if url.hostname not in {"papers.ssrn.com", "ssrn.com", "www.ssrn.com"}:
                return ""
            match = re.fullmatch(r"/abstract=([0-9]+)", url.path)
            if match:
                return match.group(1)
            if url.path.lower() not in {"/sol3/papers.cfm", "/sol3/delivery.cfm"}:
                return ""
            ids = parse_qs(url.query).get("abstract_id", [])
            return ids[0] if len(ids) == 1 and re.fullmatch(r"[0-9]+", ids[0]) else ""
        except ValueError:
            return ""

    def _normalize_ssrn_paper_id(self, item: dict) -> tuple[str, str]:
        locations = [item.get("primary_location") or {}] + (item.get("locations") or [])
        candidates = [item.get("doi") or ""]
        for location in locations:
            if isinstance(location, dict):
                candidates.extend([location.get("landing_page_url") or "", location.get("pdf_url") or ""])
        ids = {self._extract_ssrn_abstract_id(value) for value in candidates}
        ids.discard("")
        # Multiple SSRN versions cannot be identified as one paper safely.
        if len(ids) != 1:
            return "", ""
        abstract_id = ids.pop()
        return f"ssrn:{abstract_id}", f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={abstract_id}"

    def search_ssrn(self, query: str, max_results: int = 10) -> List[Paper]:
        """SSRN discovery via OpenAlex, including secondary SSRN locations.

        Cursor pagination scans up to 20 pages of 100 records. max_results must
        be 0..1000. Unidentifiable/ambiguous SSRN records are skipped; API,
        schema, scan-limit and cursor-cycle failures raise rather than reporting
        false zero results or silently returning partial data. No SSRN HTML
        search fallback is used. Returned PDF URLs are intentionally empty:
        OpenAlex landing/OA locators are not verified PDF downloads.
        """
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 0 <= max_results <= self.SSRN_MAX_RESULTS:
            raise ValueError("SSRN max_results must be an integer from 0 to 1000")
        if not max_results:
            return []
        if not isinstance(query, str) or not query.strip():
            raise ValueError("SSRN query must not be empty")
        papers, seen, cursors = [], set(), set()
        cursor = "*"
        for _ in range(self.SSRN_MAX_PAGES):
            if cursor in cursors:
                raise RuntimeError("OpenAlex SSRN pagination repeated a cursor")
            cursors.add(cursor)
            try:
                response = self.session.get(self.BASE_URL, params={
                    "search": query, "filter": f"locations.source.id:{self.SSRN_SOURCE_ID}",
                    "per_page": min(max_results, self.SSRN_PAGE_SIZE), "cursor": cursor,
                }, timeout=30, allow_redirects=False)
            except requests.RequestException:
                raise RuntimeError("OpenAlex SSRN request failed (network or timeout)") from None
            if response.status_code != 200:
                raise RuntimeError(f"OpenAlex SSRN search returned HTTP {response.status_code}")
            try:
                data = response.json()
            except (ValueError, TypeError):
                raise RuntimeError("OpenAlex SSRN returned invalid JSON") from None
            if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
                    or not isinstance(data.get("meta"), dict) or "next_cursor" not in data["meta"]):
                raise RuntimeError("OpenAlex SSRN returned a malformed paginated response")
            for item in data["results"]:
                if not isinstance(item, dict):
                    raise RuntimeError("OpenAlex SSRN returned a malformed work")
                title = item.get("title")
                if not isinstance(title, str) or not title.strip():
                    continue
                paper_id, url = self._normalize_ssrn_paper_id(item)
                if not paper_id or paper_id in seen:
                    continue
                seen.add(paper_id)
                authors = []
                for authorship in item.get("authorships") or []:
                    if isinstance(authorship, dict):
                        name = (authorship.get("author") or {}).get("display_name")
                        if isinstance(name, str) and name:
                            authors.append(name)
                try:
                    date = datetime.strptime(item.get("publication_date") or "", "%Y-%m-%d")
                except (ValueError, TypeError):
                    date = None
                doi = item.get("doi") or ""
                if not isinstance(doi, str):
                    doi = ""
                citations = item.get("cited_by_count")
                papers.append(Paper(
                    paper_id=paper_id, title=title.strip(), authors=authors,
                    abstract=self._reconstruct_abstract(item.get("abstract_inverted_index")),
                    doi=doi.removeprefix("https://doi.org/"), published_date=date,
                    pdf_url="", url=url, source="ssrn",
                    citations=citations if isinstance(citations, int) and citations >= 0 else 0,
                    extra={"discovery_source": "openalex", "openalex_id": item.get("id") or ""},
                ))
                if len(papers) == max_results:
                    return papers
            next_cursor = data["meta"]["next_cursor"]
            if next_cursor is None or not data["results"]:
                return papers
            if not isinstance(next_cursor, str) or not next_cursor:
                raise RuntimeError("OpenAlex SSRN returned an invalid cursor")
            cursor = next_cursor
        raise RuntimeError("OpenAlex SSRN scan limit reached; narrow the query")

    def get_citations(self, identifier: str, max_results: int = 10, **options) -> dict:
        """One-hop citing works; accepts only a DOI or OpenAlex work ID."""
        from .openalex_relations import related_works
        return related_works(self, identifier, "cites", max_results, **options)

    def get_references(self, identifier: str, max_results: int = 10, **options) -> dict:
        """One-hop referenced works; accepts only a DOI or OpenAlex work ID."""
        from .openalex_relations import related_works
        return related_works(self, identifier, "cited_by", max_results, **options)

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        OpenAlex does not host PDFs natively, it only links to open access versions.
        """
        raise NotImplementedError(
            "OpenAlex does not provide direct PDF downloads natively. "
            "Please use the extracted 'pdf_url' if available, or DOI for fallback."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Not implemented for OpenAlex.
        """
        return (
            "OpenAlex papers cannot be read directly through this aggregator. "
            "Please use the paper's DOI or pdf_url to access the full text."
        )


if __name__ == "__main__":
    searcher = OpenAlexSearcher()
    print("Testing OpenAlex search...")
    papers = searcher.search("CRISPR Cas9 Nature", max_results=3)

    for i, paper in enumerate(papers, 1):
        print(f"\n{i}. {paper.title}")
        print(f"   DOI: {paper.doi}")
        print(f"   URL: {paper.url}")
        print(f"   OA PDF: {paper.pdf_url}")
        print(f"   Citations: {paper.citations}")
        print(f"   Authors: {', '.join(paper.authors[:3])}")
        if paper.abstract:
            print(f"   Abstract: {paper.abstract[:100]}...")
