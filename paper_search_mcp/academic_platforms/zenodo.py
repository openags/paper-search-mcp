# paper_search_mcp/academic_platforms/zenodo.py
from typing import List, Optional, Dict, Any
from datetime import datetime
import os
import requests
from ..paper import Paper
from PyPDF2 import PdfReader

import logging
import random

logger = logging.getLogger(__name__)


class PaperSource:
    """Abstract base class for paper sources"""

    def search(self, query: str, **kwargs) -> List[Paper]:
        raise NotImplementedError

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError

    def read_paper(self, paper_id: str, save_path: str) -> str:
        raise NotImplementedError


class ZenodoSearcher(PaperSource):
    """Zenodo paper and dataset search implementation"""

    BASE_URL = os.environ.get("ZENODO_BASE_URL", "https://zenodo.org")
    API_TOKEN = os.environ.get("ZENODO_ACCESS_TOKEN", "").strip()

    BROWSERS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]

    def __init__(self):
        self._setup_session()

    def _setup_session(self):
        """Initialize HTTP session with random user agent and optional token"""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": random.choice(self.BROWSERS),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        if self.API_TOKEN:
            # Zenodo uses OAuth Bearer token
            self.session.headers["Authorization"] = f"Bearer {self.API_TOKEN}"

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        if not date_str:
            return None
        # Try common formats from Zenodo (YYYY-MM-DD or YYYY-MM)
        for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except Exception:
                continue
        logger.warning(f"Zenodo: could not parse date: {date_str}")
        return None

    def _select_pdf_file(self, rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        files = rec.get("files") or []
        if not isinstance(files, list):
            return None
        # Prefer explicit PDFs by key extension or type/mimetype when available
        pdfs = []
        for f in files:
            key = f.get("key", "")
            if key.lower().endswith(".pdf"):
                pdfs.append(f)
                continue
            if (f.get("type") == "pdf") or (f.get("mimetype") == "application/pdf"):
                pdfs.append(f)
        if pdfs:
            return pdfs[0]
        # No PDF found
        return None

    def _record_to_paper(self, rec: Dict[str, Any]) -> Optional[Paper]:
        try:
            metadata = rec.get("metadata", {}) or {}
            title = metadata.get("title", "")
            creators = metadata.get("creators") or []
            authors = [c.get("name") for c in creators if isinstance(c, dict) and c.get("name")]
            description = metadata.get("description") or ""
            publication_date = metadata.get("publication_date") or rec.get("updated")
            published_date = self._parse_date(publication_date)
            doi = rec.get("doi") or metadata.get("doi") or ""
            links = rec.get("links") or {}
            url = links.get("html") or links.get("latest_html") or ""

            pdf_url = ""
            selected_file = self._select_pdf_file(rec)
            if selected_file:
                file_links = selected_file.get("links") or {}
                pdf_url = file_links.get("download") or file_links.get("self") or ""

            keywords = metadata.get("keywords") or []
            if isinstance(keywords, str):
                keywords = [keywords]

            categories: List[str] = []
            try:
                resource_type = metadata.get("resource_type") or {}
                if isinstance(resource_type, dict) and resource_type.get("type"):
                    categories.append(resource_type["type"])  # e.g., 'publication' or 'dataset'
            except Exception:
                pass

            paper_id = str(rec.get("id")) if rec.get("id") is not None else (doi or url or title)

            extra = {
                "conceptdoi": rec.get("conceptdoi"),
                "resource_type": metadata.get("resource_type"),
                "communities": metadata.get("communities"),
            }

            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=description,
                url=url,
                pdf_url=pdf_url,
                published_date=published_date if published_date else None,
                updated_date=self._parse_date(rec.get("updated")) if rec.get("updated") else None,
                source="zenodo",
                categories=categories,
                keywords=keywords,
                doi=doi,
                citations=0,
                extra=extra,
            )
        except Exception as e:
            logger.warning(f"Failed to map Zenodo record to Paper: {e}")
            return None

    def _year_filter(self, year: Optional[str]) -> Optional[str]:
        """Convert year argument to a Lucene publication_date filter.
        Supports: "2025", "2016-2020", "2010-", "-2015".
        """
        if not year:
            return None
        y = year.strip()
        if "-" in y:
            parts = y.split("-")
            start = parts[0].strip() or "*"
            end = parts[1].strip() if len(parts) > 1 else "*"
            return f"metadata.publication_date:[{start} TO {end}]"
        # single year
        return f"metadata.publication_date:[{y} TO {y}]"

    def _build_query(
        self,
        query: str = "",
        community: Optional[str] = None,
        year: Optional[str] = None,
        resource_type: Optional[str] = None,
        subtype: Optional[str] = None,
        creators: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
    ) -> str:
        parts: List[str] = []
        if query:
            parts.append(f"({query})")
        if community:
            # Zenodo community slug, e.g., kios-coe
            parts.append(f"communities:{community}")
        yf = self._year_filter(year)
        if yf:
            parts.append(yf)
        if resource_type:
            parts.append(f"resource_type.type:{resource_type}")
        if subtype:
            parts.append(f"resource_type.subtype:{subtype}")
        if creators:
            # Match any of the creators' names
            names = " OR ".join([f'"{c}"' for c in creators if c])
            if names:
                parts.append(f"creators.name:({names})")
        if keywords:
            kws = " OR ".join([f'"{k}"' for k in keywords if k])
            if kws:
                # Some records index as 'keywords'
                parts.append(f"keywords:({kws})")
        return " AND ".join(parts) if parts else "*"

    def search(
        self,
        query: str = "",
        max_results: int = 10,
        *,
        community: Optional[str] = None,
        year: Optional[str] = None,
        resource_type: Optional[str] = None,
        subtype: Optional[str] = None,
        creators: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
    ) -> List[Paper]:
        """
        Search Zenodo records using the public API.

        Args:
            query: Free-text query (Lucene syntax supported by Zenodo)
            max_results: Maximum number of results to return
            community: Community slug (e.g., 'kios-coe')
            year: Year or range, supports '2025', '2016-2020', '2010-', '-2015'
            resource_type: e.g., 'publication', 'dataset'
            subtype: e.g., 'conferencepaper', 'article'
            creators: List of author names to match
            keywords: List of keywords to match
            sort: Field to sort by (e.g., 'mostrecent', 'bestmatch', 'version')
            order: 'asc' or 'desc'
        Returns:
            List[Paper]
        """
        papers: List[Paper] = []
        page = 1
        page_size = min(max_results, 100)
        try:
            q = self._build_query(
                query=query,
                community=community,
                year=year,
                resource_type=resource_type,
                subtype=subtype,
                creators=creators,
                keywords=keywords,
            )
            while len(papers) < max_results:
                params: Dict[str, Any] = {
                    "q": q,
                    "page": page,
                    "size": page_size,
                }
                if sort:
                    params["sort"] = sort
                if order:
                    params["order"] = order
                url = f"{self.BASE_URL}/api/records"
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    logger.error(
                        f"Zenodo search failed: HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    break
                data = resp.json()
                hits = (data.get("hits") or {}).get("hits") or []
                if not hits:
                    break
                for rec in hits:
                    if len(papers) >= max_results:
                        break
                    paper = self._record_to_paper(rec)
                    if paper:
                        papers.append(paper)
                page += 1
        except Exception as e:
            logger.error(f"Zenodo search error: {e}")
        return papers[:max_results]

    def _get_record(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a Zenodo record by numeric ID or DOI/URL when possible."""
        # Prefer numeric IDs like '1234567'
        try:
            # If paper_id looks like a full URL, try to extract the numeric ID
            if paper_id.startswith("http"):
                # Expected patterns like https://zenodo.org/records/1234567
                for part in paper_id.split("/"):
                    if part.isdigit():
                        paper_id = part
                        break
            url = f"{self.BASE_URL}/api/records/{paper_id}"
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"Failed to fetch Zenodo record {paper_id}: HTTP {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching Zenodo record {paper_id}: {e}")
            return None

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download a PDF file from a Zenodo record if available.

        Args:
            paper_id: Zenodo record ID (numeric) or record URL
            save_path: Directory to save the PDF
        Returns:
            Path to the downloaded PDF, or error message
        """
        try:
            rec = self._get_record(paper_id)
            if not rec:
                return f"Error: Could not fetch Zenodo record {paper_id}"
            file_entry = self._select_pdf_file(rec)
            if not file_entry:
                return "Error: No PDF file available for this record"

            links = file_entry.get("links") or {}
            download_url = links.get("download") or links.get("self")
            if not download_url:
                return "Error: No downloadable link for the selected file"

            os.makedirs(save_path, exist_ok=True)
            filename = file_entry.get("key") or f"zenodo_{rec.get('id', 'file')}.pdf"
            if not filename.lower().endswith(".pdf"):
                filename += ".pdf"
            outfile = os.path.join(save_path, f"zenodo_{str(rec.get('id'))}_{os.path.basename(filename)}")

            with self.session.get(download_url, timeout=60, stream=True) as r:
                r.raise_for_status()
                with open(outfile, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            return outfile
        except Exception as e:
            logger.error(f"Zenodo PDF download error: {e}")
            return f"Error downloading PDF: {e}"

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from a Zenodo record's PDF if available.
        """
        try:
            pdf_path = self.download_pdf(paper_id, save_path)
            if not os.path.isfile(pdf_path):
                # When download_pdf returns an error message
                return pdf_path

            text = ""
            reader = PdfReader(pdf_path)
            for page in reader.pages:
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                except Exception as e:
                    logger.warning(f"Failed to extract text from a page: {e}")
                    continue

            if not text.strip():
                return f"PDF downloaded to {pdf_path}, but unable to extract readable text"
            return text.strip()
        except Exception as e:
            logger.error(f"Zenodo read paper error: {e}")
            return f"Error reading paper: {e}"

    def search_communities(
        self,
        query: str = "",
        max_results: int = 20,
        *,
        sort: Optional[str] = None,
        order: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search Zenodo communities.

        Args:
            query: Free-text query for community title/slug/description.
            max_results: Maximum number of communities to return.
            sort: Sort field (e.g., 'newest', 'bestmatch').
            order: 'asc' or 'desc'.
        Returns:
            A list of community metadata dictionaries.
        """
        results: List[Dict[str, Any]] = []
        page = 1
        page_size = min(max_results, 100)
        try:
            while len(results) < max_results:
                params: Dict[str, Any] = {
                    "q": query or "*",
                    "page": page,
                    "size": page_size,
                }
                if sort:
                    params["sort"] = sort
                if order:
                    params["order"] = order

                url = f"{self.BASE_URL}/api/communities"
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    logger.error(
                        f"Zenodo community search failed: HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    break
                data = resp.json() or {}
                hits = (data.get("hits") or {}).get("hits") or []
                if not hits:
                    break
                for com in hits:
                    if len(results) >= max_results:
                        break
                    results.append(
                        {
                            "id": com.get("id"),
                            "slug": com.get("slug"),
                            "title": com.get("title") or (com.get("metadata", {}).get("title") if isinstance(com.get("metadata"), dict) else None),
                            "description": com.get("description") or (com.get("metadata", {}).get("description") if isinstance(com.get("metadata"), dict) else None),
                            "created": com.get("created"),
                            "updated": com.get("updated"),
                            "links": (com.get("links") or {}),
                        }
                    )
                page += 1
        except Exception as e:
            logger.error(f"Zenodo communities search error: {e}")
        return results[:max_results]

    def get_record_details(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """Public method to fetch the raw Zenodo record JSON by numeric ID or URL."""
        try:
            return self._get_record(paper_id)
        except Exception as e:
            logger.error(f"Zenodo get_record_details error: {e}")
            return None

    def list_files(self, paper_id: str) -> List[Dict[str, Any]]:
        """List files for a given record ID/URL with basic metadata and download links."""
        files_info: List[Dict[str, Any]] = []
        try:
            rec = self._get_record(paper_id)
            if not rec:
                return files_info
            files = rec.get("files") or []
            if not isinstance(files, list):
                return files_info
            for f in files:
                links = f.get("links") or {}
                files_info.append(
                    {
                        "key": f.get("key"),
                        "size": f.get("size"),
                        "checksum": f.get("checksum"),
                        "type": f.get("type"),
                        "mimetype": f.get("mimetype"),
                        "download": links.get("download") or links.get("self"),
                    }
                )
        except Exception as e:
            logger.error(f"Zenodo list_files error: {e}")
        return files_info

    def search_by_creator(
        self,
        creator: str,
        max_results: int = 10,
        *,
        community: Optional[str] = None,
        year: Optional[str] = None,
        resource_type: Optional[str] = None,
        subtype: Optional[str] = None,
        sort: Optional[str] = None,
        order: Optional[str] = None,
    ) -> List[Paper]:
        """Convenience wrapper to search by a single creator name."""
        try:
            return self.search(
                query="",
                max_results=max_results,
                community=community,
                year=year,
                resource_type=resource_type,
                subtype=subtype,
                creators=[creator] if creator else None,
                keywords=None,
                sort=sort,
                order=order,
            )
        except Exception as e:
            logger.error(f"Zenodo search_by_creator error: {e}")
            return []
