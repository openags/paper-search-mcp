from typing import Dict, List, Optional, Tuple
from datetime import datetime
import os
import requests
from bs4 import BeautifulSoup
import time
import random
from ..paper import Paper
from ..utils import extract_doi
from .base import PaperSource
import logging
from pypdf import PdfReader
import re
from xml.etree import ElementTree as ET
from ..config import get_env

logger = logging.getLogger(__name__)


class SemanticSearcher(PaperSource):
    """Semantic Scholar paper search implementation"""

    SEMANTIC_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    SEMANTIC_BASE_URL = "https://api.semanticscholar.org/graph/v1"
    EUROPE_PMC_PDF_URL = "https://europepmc.org/articles/{pmcid}?pdf=render"
    PMC_PDF_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
    EUROPE_PMC_FULL_TEXT_XML_URL = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    )
    BROWSERS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ]

    def __init__(self):
        self._setup_session()

    def _setup_session(self):
        """Initialize session with random user agent"""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": random.choice(self.BROWSERS),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse date from Semantic Scholar format (e.g., '2025-06-02')"""
        if not date_str:
            return None

        try:
            return datetime.strptime(date_str.strip(), "%Y-%m-%d")
        except ValueError:
            logger.warning(f"Could not parse date: {date_str}")
            return None

    def _extract_url_from_disclaimer(self, disclaimer: str) -> str:
        """Extract URL from disclaimer text"""
        # 匹配常见的 URL 模式
        url_patterns = [
            r"https?://[^\s,)]+",  # 基本的 HTTP/HTTPS URL
            r"https?://arxiv\.org/abs/[^\s,)]+",  # arXiv 链接
            r"https?://[^\s,)]*\.pdf",  # PDF 文件链接
        ]

        all_urls = []
        for pattern in url_patterns:
            matches = re.findall(pattern, disclaimer)
            all_urls.extend(matches)

        if not all_urls:
            return ""

        doi_urls = [url for url in all_urls if "doi.org" in url]
        if doi_urls:
            return doi_urls[0]

        non_unpaywall_urls = [url for url in all_urls if "unpaywall.org" not in url]
        if non_unpaywall_urls:
            url = non_unpaywall_urls[0]
            if "arxiv.org/abs/" in url:
                pdf_url = url.replace("/abs/", "/pdf/")
                return pdf_url
            return url

        if all_urls:
            url = all_urls[0]
            if "arxiv.org/abs/" in url:
                pdf_url = url.replace("/abs/", "/pdf/")
                return pdf_url
            return url

        return ""

    def _parse_paper(self, item) -> Optional[Paper]:
        """Parse single paper entry from Semantic Scholar HTML and optionally fetch detailed info"""
        try:
            authors = [author["name"] for author in item.get("authors", [])]

            # Parse the publication date
            published_date = self._parse_date(item.get("publicationDate", ""))

            # Safely get PDF URL - 支持从 disclaimer 中提取
            pdf_url = ""
            open_access_pdf = item.get("openAccessPdf") or {}
            if open_access_pdf:
                # 首先尝试直接获取 URL
                if open_access_pdf.get("url"):
                    pdf_url = open_access_pdf["url"]
                # 如果 URL 为空但有 disclaimer，尝试从 disclaimer 中提取
                elif open_access_pdf.get("disclaimer"):
                    pdf_url = self._extract_url_from_disclaimer(
                        open_access_pdf["disclaimer"]
                    )

            # Safely get DOI
            doi = ""
            external_ids = item.get("externalIds") or {}
            if external_ids and external_ids.get("DOI"):
                doi = external_ids["DOI"]

            if not doi and item.get("abstract"):
                doi = extract_doi(item["abstract"])

            # Safely get categories
            categories = item.get("fieldsOfStudy", [])
            if not categories:
                categories = []

            return Paper(
                paper_id=item["paperId"],
                title=item["title"],
                authors=authors,
                abstract=item.get("abstract", ""),
                url=item.get("url", ""),
                pdf_url=pdf_url,
                published_date=published_date,
                source="semantic",
                categories=categories,
                doi=doi,
                citations=item.get("citationCount", 0),
                extra={
                    "externalIds": external_ids,
                    "openAccessPdf": open_access_pdf,
                },
            )

        except Exception as e:
            logger.warning(f"Failed to parse Semantic paper: {e}")
            return None

    @staticmethod
    def get_api_key() -> Optional[str]:
        """
        Get the Semantic Scholar API key from environment variables.
        Returns None if no API key is set or if it's empty, enabling unauthenticated access.
        """
        api_key = get_env("SEMANTIC_SCHOLAR_API_KEY", "")
        if not api_key or api_key.strip() == "":
            logger.warning(
                "No SEMANTIC_SCHOLAR_API_KEY set or it's empty. Using unauthenticated access with lower rate limits."
            )
            return None
        return api_key.strip()

    def request_api(self, path: str, params: dict) -> dict:
        """
        Make a request to the Semantic Scholar API with optional API key.
        """
        max_retries = 3
        api_key = self.get_api_key()
        retry_delay = 5 if api_key is None else 2
        has_retried_without_key = False

        for attempt in range(max_retries):
            try:
                headers = {"x-api-key": api_key} if api_key else {}
                url = f"{self.SEMANTIC_BASE_URL}/{path}"
                response = self.session.get(
                    url, params=params, headers=headers, timeout=30
                )

                if (
                    response.status_code == 403
                    and api_key
                    and not has_retried_without_key
                ):
                    logger.warning(
                        "Semantic Scholar API key was rejected (403). Retrying without API key."
                    )
                    api_key = None
                    has_retried_without_key = True
                    continue

                # 检查是否是429错误（限流）
                if response.status_code == 429:
                    if attempt < max_retries - 1:
                        retry_after = response.headers.get("Retry-After")
                        wait_time = (
                            int(retry_after)
                            if retry_after and retry_after.isdigit()
                            else retry_delay * (2**attempt)
                        )
                        logger.warning(
                            f"Rate limited (429). Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}"
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(
                            f"Rate limited (429) after {max_retries} attempts. Please wait before making more requests."
                        )
                        return {
                            "error": "rate_limited",
                            "status_code": 429,
                            "message": "Too many requests. Please wait before retrying.",
                        }

                response.raise_for_status()
                return response

            except requests.exceptions.HTTPError as e:
                if (
                    e.response.status_code == 403
                    and api_key
                    and not has_retried_without_key
                ):
                    logger.warning(
                        "Semantic Scholar API key was rejected (403). Retrying without API key."
                    )
                    api_key = None
                    has_retried_without_key = True
                    continue
                if e.response.status_code == 429:
                    if attempt < max_retries - 1:
                        retry_after = e.response.headers.get("Retry-After")
                        wait_time = (
                            int(retry_after)
                            if retry_after and retry_after.isdigit()
                            else retry_delay * (2**attempt)
                        )
                        logger.warning(
                            f"Rate limited (429). Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}"
                        )
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(
                            f"Rate limited (429) after {max_retries} attempts. Please wait before making more requests."
                        )
                        return {
                            "error": "rate_limited",
                            "status_code": 429,
                            "message": "Too many requests. Please wait before retrying.",
                        }
                else:
                    logger.error(f"HTTP Error requesting API: {e}")
                    return {
                        "error": "http_error",
                        "status_code": e.response.status_code,
                        "message": str(e),
                    }
            except Exception as e:
                logger.error(f"Error requesting API: {e}")
                return {"error": "general_error", "message": str(e)}

        return {
            "error": "max_retries_exceeded",
            "message": "Maximum retry attempts exceeded",
        }

    def search(
        self,
        query: str,
        year: Optional[str] = None,
        max_results: int = 10,
        fetch_details: bool = False,
    ) -> List[Paper]:
        """
        Search Semantic Scholar

        Args:
            query: Search query string
            year (Optional[str]): Filter by publication year. Supports several formats:
            - Single year: "2019"
            - Year range: "2016-2020"
            - Since year: "2010-"
            - Until year: "-2015"
            max_results: Maximum number of results to return
            fetch_details: Backward-compatible flag retained for older callers.
                Semantic search responses already include the fields this connector uses,
                so the current implementation does not perform extra per-paper detail fetches.

        Returns:
            List[Paper]: List of paper objects
        """
        papers = []

        try:
            fields = [
                "title",
                "abstract",
                "year",
                "citationCount",
                "authors",
                "url",
                "publicationDate",
                "externalIds",
                "fieldsOfStudy",
                "openAccessPdf",
            ]
            # Construct search parameters
            params = {
                "query": query,
                "limit": max_results,
                "fields": ",".join(fields),
            }
            if year:
                params["year"] = year
            # Make request
            response = self.request_api("paper/search", params)

            # Check for errors
            if isinstance(response, dict) and "error" in response:
                error_msg = response.get("message", "Unknown error")
                if response.get("error") == "rate_limited":
                    logger.error(f"Rate limited by Semantic Scholar API: {error_msg}")
                else:
                    logger.error(f"Semantic Scholar API error: {error_msg}")
                return papers

            # Check response status code
            if not hasattr(response, "status_code") or response.status_code != 200:
                status_code = getattr(response, "status_code", "unknown")
                logger.error(
                    f"Semantic Scholar search failed with status {status_code}"
                )
                return papers

            data = response.json()
            results = data["data"]

            if not results:
                logger.info("No results found for the query")
                return papers

            # Process each result
            for i, item in enumerate(results):
                if len(papers) >= max_results:
                    break

                logger.info(
                    f"Processing paper {i + 1}/{min(len(results), max_results)}"
                )
                paper = self._parse_paper(item)
                if paper:
                    papers.append(paper)

        except Exception as e:
            logger.error(f"Semantic Scholar search error: {e}")

        return papers[:max_results]

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    @staticmethod
    def _normalize_pmcid(value: object) -> str:
        if value is None:
            return ""

        text = str(value).strip()
        if not text:
            return ""

        pmcid_match = re.search(r"PMC\d+", text, flags=re.IGNORECASE)
        if pmcid_match:
            return pmcid_match.group(0).upper()

        if re.fullmatch(r"\d+", text):
            return f"PMC{text}"

        return ""

    @staticmethod
    def _is_pmc_article_url(url: str) -> bool:
        normalized_url = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()
        return bool(re.search(r"/(?:pmc/)?articles/pmc\d+$", normalized_url))

    @staticmethod
    def _looks_like_html(content: bytes) -> bool:
        prefix = content[:128].lstrip().lower()
        return prefix.startswith((b"<!doctype", b"<html", b"<?xml"))

    @classmethod
    def _looks_like_pdf(cls, content: bytes, content_type: str = "") -> bool:
        if not content:
            return False

        prefix = content[:1024].lstrip()
        if prefix.startswith(b"%PDF"):
            return True

        content_type = content_type.lower()
        return "pdf" in content_type and not cls._looks_like_html(content)

    @classmethod
    def _pdf_file_is_valid(cls, pdf_path: str) -> bool:
        try:
            with open(pdf_path, "rb") as file:
                return cls._looks_like_pdf(file.read(1024))
        except OSError:
            return False

    def _download_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.session.headers.get(
                "User-Agent", random.choice(self.BROWSERS)
            ),
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _extract_pmcids(self, paper: Paper) -> List[str]:
        pmcids: List[str] = []

        def add(value: object) -> None:
            pmcid = self._normalize_pmcid(value)
            if pmcid and pmcid not in pmcids:
                pmcids.append(pmcid)

        extra = getattr(paper, "extra", None) or {}
        external_ids = extra.get("externalIds", {}) if isinstance(extra, dict) else {}
        if isinstance(external_ids, dict):
            add(external_ids.get("PubMedCentral"))
            add(external_ids.get("PMCID"))

        for text in (getattr(paper, "pdf_url", ""), getattr(paper, "url", "")):
            if not text:
                continue
            for match in re.findall(r"PMC\d+", text, flags=re.IGNORECASE):
                add(match)

        return pmcids

    def _candidate_pdf_urls(self, paper: Paper) -> List[str]:
        candidates: List[str] = []
        seen = set()

        def add(url: str) -> None:
            url = (url or "").strip()
            if url and url not in seen:
                seen.add(url)
                candidates.append(url)

        semantic_pdf_url = getattr(paper, "pdf_url", "")
        if semantic_pdf_url and not self._is_pmc_article_url(semantic_pdf_url):
            add(semantic_pdf_url)

        for pmcid in self._extract_pmcids(paper):
            add(self.EUROPE_PMC_PDF_URL.format(pmcid=pmcid))
            add(self.PMC_PDF_URL.format(pmcid=pmcid))

        if semantic_pdf_url and self._is_pmc_article_url(semantic_pdf_url):
            add(semantic_pdf_url)

        return candidates

    def _download_pdf_url(self, pdf_url: str, pdf_path: str) -> None:
        response = self.session.get(
            pdf_url,
            headers=self._download_headers(),
            timeout=60,
            allow_redirects=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if not self._looks_like_pdf(response.content, content_type):
            first_bytes = response.content[:16]
            raise ValueError(
                "URL did not return a PDF "
                f"(content-type={content_type or 'unknown'}, "
                f"final_url={response.url}, first_bytes={first_bytes!r})"
            )

        with open(pdf_path, "wb") as file:
            file.write(response.content)

    def _download_paper_pdf(
        self, paper_id: str, save_path: str
    ) -> Tuple[Optional[str], Optional[Paper], List[str]]:
        paper = self.get_paper_details(paper_id)
        if not paper:
            return None, None, [f"Could not find paper details for {paper_id}"]

        os.makedirs(save_path, exist_ok=True)
        filename = f"semantic_{paper_id.replace('/', '_')}.pdf"
        pdf_path = os.path.join(save_path, filename)

        if os.path.exists(pdf_path):
            if self._pdf_file_is_valid(pdf_path):
                return pdf_path, paper, []
            try:
                os.remove(pdf_path)
            except OSError as exc:
                return None, paper, [f"Could not remove invalid cached PDF: {exc}"]

        candidates = self._candidate_pdf_urls(paper)
        if not candidates:
            return None, paper, [f"Could not find PDF URL for paper {paper_id}"]

        errors: List[str] = []
        for pdf_url in candidates:
            try:
                self._download_pdf_url(pdf_url, pdf_path)
                return pdf_path, paper, errors
            except Exception as exc:
                errors.append(f"{pdf_url}: {exc}")
                if os.path.exists(pdf_path) and not self._pdf_file_is_valid(pdf_path):
                    try:
                        os.remove(pdf_path)
                    except OSError:
                        pass

        return None, paper, errors

    @staticmethod
    def _format_download_error(paper_id: str, errors: List[str]) -> str:
        details = "; ".join(errors) if errors else "No downloadable PDF found"
        return f"Error downloading PDF for {paper_id}: {details}"

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download PDF from Semantic Scholar

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
            save_path: Path to save the PDF

        Returns:
            str: Path to downloaded file or error message
        """
        try:
            pdf_path, _paper, errors = self._download_paper_pdf(paper_id, save_path)
            if pdf_path:
                return pdf_path
            return self._format_download_error(paper_id, errors)
        except Exception as e:
            logger.error(f"PDF download error: {e}")
            return f"Error downloading PDF: {e}"

    def _extract_pdf_text(self, pdf_path: str) -> str:
        reader = PdfReader(pdf_path)
        text = ""

        for page_num, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text()
                if page_text:
                    text += f"\n--- Page {page_num + 1} ---\n"
                    text += page_text + "\n"
            except Exception as e:
                logger.warning(
                    f"Failed to extract text from page {page_num + 1}: {e}"
                )
                continue

        return text.strip()

    def _format_read_metadata(
        self,
        paper: Optional[Paper],
        paper_id: str,
        pdf_path: str = "",
        full_text_source: str = "",
    ) -> str:
        metadata = f"Title: {paper.title if paper else paper_id}\n"
        metadata += f"Authors: {', '.join(paper.authors) if paper else ''}\n"
        metadata += f"Published Date: {paper.published_date if paper else ''}\n"
        metadata += f"URL: {paper.url if paper else ''}\n"
        if pdf_path:
            metadata += f"PDF downloaded to: {pdf_path}\n"
        if full_text_source:
            metadata += f"Full text source: {full_text_source}\n"
        metadata += "=" * 80 + "\n\n"
        return metadata

    def _element_text(self, element: ET.Element) -> str:
        return self._clean_text(" ".join(element.itertext()))

    def _find_first_element(
        self, root: ET.Element, tag_name: str
    ) -> Optional[ET.Element]:
        for element in root.iter():
            if self._local_name(element.tag) == tag_name:
                return element
        return None

    def _collect_body_text(self, element: ET.Element, parts: List[str]) -> None:
        for child in list(element):
            tag_name = self._local_name(child.tag)
            if tag_name in {"title", "p"}:
                text = self._element_text(child)
                if text:
                    parts.append(text)
            elif tag_name in {"sec", "body"}:
                self._collect_body_text(child, parts)

    def _extract_text_from_article_xml(self, xml_content: bytes) -> str:
        root = ET.fromstring(xml_content)
        parts: List[str] = []

        title = self._find_first_element(root, "article-title")
        if title is not None:
            title_text = self._element_text(title)
            if title_text:
                parts.append(title_text)

        for abstract in root.iter():
            if self._local_name(abstract.tag) == "abstract":
                abstract_text = self._element_text(abstract)
                if abstract_text:
                    parts.append(abstract_text)

        body = self._find_first_element(root, "body")
        if body is not None:
            self._collect_body_text(body, parts)

        deduped_parts: List[str] = []
        seen = set()
        for part in parts:
            if part not in seen:
                seen.add(part)
                deduped_parts.append(part)

        return "\n\n".join(deduped_parts)

    def _read_europe_pmc_full_text(
        self, paper: Optional[Paper]
    ) -> Tuple[str, str]:
        if not paper:
            return "", ""

        for pmcid in self._extract_pmcids(paper):
            full_text_url = self.EUROPE_PMC_FULL_TEXT_XML_URL.format(pmcid=pmcid)
            try:
                response = self.session.get(
                    full_text_url,
                    headers={
                        "User-Agent": self.session.headers.get(
                            "User-Agent", random.choice(self.BROWSERS)
                        ),
                        "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
                    },
                    timeout=60,
                )
                response.raise_for_status()
                text = self._extract_text_from_article_xml(response.content)
                if text:
                    return text, full_text_url
            except Exception as exc:
                logger.warning(
                    "Europe PMC full-text fallback failed for %s: %s",
                    pmcid,
                    exc,
                )

        return "", ""

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from Semantic Scholar paper PDF

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")
            save_path: Directory to save downloaded PDF

        Returns:
            str: Extracted text from the PDF or error message
        """
        try:
            pdf_path, paper, errors = self._download_paper_pdf(paper_id, save_path)
            if not pdf_path:
                full_text, full_text_source = self._read_europe_pmc_full_text(paper)
                if full_text:
                    return (
                        self._format_read_metadata(
                            paper,
                            paper_id,
                            full_text_source=full_text_source,
                        )
                        + full_text
                    )
                return self._format_download_error(paper_id, errors)

            try:
                text = self._extract_pdf_text(pdf_path)
            except Exception as exc:
                logger.warning("PDF text extraction failed for %s: %s", pdf_path, exc)
                full_text, full_text_source = self._read_europe_pmc_full_text(paper)
                if full_text:
                    return (
                        self._format_read_metadata(
                            paper,
                            paper_id,
                            pdf_path=pdf_path,
                            full_text_source=full_text_source,
                        )
                        + full_text
                    )
                return f"PDF downloaded to {pdf_path}, but text extraction failed: {exc}"

            if not text:
                full_text, full_text_source = self._read_europe_pmc_full_text(paper)
                if full_text:
                    return (
                        self._format_read_metadata(
                            paper,
                            paper_id,
                            pdf_path=pdf_path,
                            full_text_source=full_text_source,
                        )
                        + full_text
                    )
                return (
                    f"PDF downloaded to {pdf_path}, but unable to extract readable text"
                )

            return self._format_read_metadata(paper, paper_id, pdf_path=pdf_path) + text

        except requests.RequestException as e:
            logger.error(f"Error downloading PDF: {e}")
            return f"Error downloading PDF: {e}"
        except Exception as e:
            logger.error(f"Read paper error: {e}")
            return f"Error reading paper: {e}"

    def get_paper_details(self, paper_id: str) -> Optional[Paper]:
        """
        Fetch detailed information for a specific Semantic Scholar paper

        Args:
            paper_id (str): Paper identifier in one of the following formats:
            - Semantic Scholar ID (e.g., "649def34f8be52c8b66281af98ae884c09aef38b")
            - DOI:<doi> (e.g., "DOI:10.18653/v1/N18-3011")
            - ARXIV:<id> (e.g., "ARXIV:2106.15928")
            - MAG:<id> (e.g., "MAG:112218234")
            - ACL:<id> (e.g., "ACL:W12-3903")
            - PMID:<id> (e.g., "PMID:19872477")
            - PMCID:<id> (e.g., "PMCID:2323736")
            - URL:<url> (e.g., "URL:https://arxiv.org/abs/2106.15928v1")

        Returns:
            Paper: Detailed paper object with full metadata
        """
        try:
            fields = [
                "title",
                "abstract",
                "year",
                "citationCount",
                "authors",
                "url",
                "publicationDate",
                "externalIds",
                "fieldsOfStudy",
                "openAccessPdf",
            ]
            params = {
                "fields": ",".join(fields),
            }

            response = self.request_api(f"paper/{paper_id}", params)

            # Check for errors
            if isinstance(response, dict) and "error" in response:
                error_msg = response.get("message", "Unknown error")
                if response.get("error") == "rate_limited":
                    logger.error(f"Rate limited by Semantic Scholar API: {error_msg}")
                else:
                    logger.error(f"Semantic Scholar API error: {error_msg}")
                return None

            # Check response status code
            if not hasattr(response, "status_code") or response.status_code != 200:
                status_code = getattr(response, "status_code", "unknown")
                logger.error(
                    f"Semantic Scholar paper details fetch failed with status {status_code}"
                )
                return None

            results = response.json()
            paper = self._parse_paper(results)
            if paper:
                return paper
            else:
                return None
        except Exception as e:
            logger.error(f"Error fetching paper details for {paper_id}: {e}")
            return None


if __name__ == "__main__":
    # Test Semantic searcher
    searcher = SemanticSearcher()

    print("Testing Semantic search functionality...")
    query = "secret sharing"
    max_results = 2

    print("\n" + "=" * 60)
    print("1. Testing search with detailed information")
    print("=" * 60)
    try:
        papers = searcher.search(query, year=None, max_results=max_results)
        print(f"\nFound {len(papers)} papers for query '{query}' (with details):")
        for i, paper in enumerate(papers, 1):
            print(f"\n{i}. {paper.title}")
            print(f"   Paper ID: {paper.paper_id}")
            print(f"   Authors: {', '.join(paper.authors)}")
            print(f"   Categories: {', '.join(paper.categories)}")
            print(f"   URL: {paper.url}")
            if paper.pdf_url:
                print(f"   PDF: {paper.pdf_url}")
            if paper.published_date:
                print(f"   Published Date: {paper.published_date}")
            if paper.abstract:
                print(f"   Abstract: {paper.abstract[:200]}...")
    except Exception as e:
        print(f"Error during detailed search: {e}")

    print("\n" + "=" * 60)
    print("2. Testing manual paper details fetching")
    print("=" * 60)
    test_paper_id = "5bbfdf2e62f0508c65ba6de9c72fe2066fd98138"
    try:
        paper_details = searcher.get_paper_details(test_paper_id)
        if paper_details:
            print(f"\nManual fetch for paper {test_paper_id}:")
            print(f"Title: {paper_details.title}")
            print(f"Authors: {', '.join(paper_details.authors)}")
            print(f"Categories: {', '.join(paper_details.categories)}")
            print(f"URL: {paper_details.url}")
            if paper_details.pdf_url:
                print(f"PDF: {paper_details.pdf_url}")
            if paper_details.published_date:
                print(f"Published Date: {paper_details.published_date}")
            print(f"DOI: {paper_details.doi}")
            print(f"Citations: {paper_details.citations}")
            print(f"Abstract: {paper_details.abstract[:200]}...")
        else:
            print(f"Could not fetch details for paper {test_paper_id}")
    except Exception as e:
        print(f"Error fetching paper details: {e}")
