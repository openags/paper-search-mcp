"""Anonymous, public-paper-only OpenReview API v2 connector.

No accounts, credentials, private submissions, reviews, or replies are used.
Search examines at most 1,000 public paper hits in 100-record offset pages.
HTTP failures and malformed responses raise; only successful empty searches
return an empty list. API v1-only records are outside this connector's scope.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

import requests

from .base import PaperSource
from .public_pdf import save_verified_pdf, read_pdf_text
from ..paper import Paper


class _AnonymousAuth(requests.auth.AuthBase):
    def __call__(self, request):
        # An explicit no-op auth object suppresses requests' .netrc lookup,
        # while preserving normal proxy and TLS-certificate configuration.
        request.headers.pop("Authorization", None)
        return request


class OpenReviewError(RuntimeError):
    """The public OpenReview service could not fulfill a request."""


class OpenReviewSearcher(PaperSource):
    BASE_URL = "https://api2.openreview.net"
    PAGE_SIZE = 100
    MAX_RESULTS = 1000

    def __init__(self):
        self.session = requests.Session()
        # Do not pick up .netrc credentials for this anonymous-only connector.
        self.session.auth = _AnonymousAuth()
        self.session.headers.update({"User-Agent": "paper-search-mcp/0.1.4", "Accept": "application/json"})

    @staticmethod
    def _note_id(value: str) -> str:
        value = value.strip()
        if value.startswith("openreview:"):
            value = value[len("openreview:"):]
        if value.startswith(("https://", "http://")):
            url = urlparse(value)
            ids = parse_qs(url.query).get("id", [])
            if (url.scheme != "https" or url.hostname not in {"openreview.net", "www.openreview.net", "api2.openreview.net"}
                    or url.username or url.password or url.port or url.path not in {"/forum", "/pdf"} or len(ids) != 1):
                raise ValueError("Expected an OpenReview note ID or official forum/PDF URL")
            value = ids[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("Invalid OpenReview note ID")
        return value

    def _get_json(self, path: str, params: dict) -> dict:
        try:
            response = self.session.get(self.BASE_URL + path, params=params, timeout=30, allow_redirects=False)
        except requests.RequestException:
            raise OpenReviewError("OpenReview request failed (network or timeout)") from None
        if response.status_code in {401, 403}:
            raise OpenReviewError("OpenReview denied anonymous access; private/authenticated content is not supported")
        if response.status_code != 200:
            raise OpenReviewError(f"OpenReview API returned HTTP {response.status_code}")
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise OpenReviewError("OpenReview returned invalid JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("notes"), list):
            raise OpenReviewError("OpenReview response is missing its notes array")
        return data

    @staticmethod
    def _value(content: dict, key: str, default=None):
        value = content.get(key, default)
        return value.get("value", default) if isinstance(value, dict) else value

    @staticmethod
    def _date(value):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None

    def _parse_note(self, note: dict) -> Paper | None:
        if not isinstance(note, dict):
            raise OpenReviewError("OpenReview returned a malformed note")
        content = note.get("content")
        if not isinstance(content, dict):
            raise OpenReviewError("OpenReview note is missing its content object")
        # Explicitly exclude public reviews/replies, private and deleted records.
        note_id = note.get("id")
        if (not isinstance(note_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", note_id)
                or note.get("forum") != note_id or note.get("replyto") or note.get("ddate")
                or not isinstance(note.get("readers"), list) or "everyone" not in note["readers"]):
            return None
        title = self._value(content, "title", "")
        if not isinstance(title, str) or not title.strip():
            return None
        authors = self._value(content, "authors", [])
        abstract = self._value(content, "abstract", "")
        # Top-level announcements are not papers. Public papers must expose at
        # least one paper-specific field in addition to a title.
        pdf = self._value(content, "pdf", "")
        if not authors and not abstract and not pdf:
            return None
        pdf_url = ""
        if isinstance(pdf, str) and pdf:
            parsed = urlparse(pdf)
            if ((not parsed.scheme and not parsed.netloc and pdf.startswith("/pdf")) or
                    (parsed.scheme == "https" and parsed.hostname in {"openreview.net", "api2.openreview.net"}
                     and not parsed.username and not parsed.password and not parsed.port and parsed.path.startswith("/pdf"))):
                pdf_url = f"https://openreview.net/pdf?id={note_id}"
        doi = self._value(content, "doi", "")
        return Paper(
            paper_id=note_id, title=title.strip(),
            authors=[a for a in authors if isinstance(a, str)] if isinstance(authors, list) else [],
            abstract=abstract if isinstance(abstract, str) else "",
            doi=doi if isinstance(doi, str) else "",
            published_date=self._date(note.get("pdate") or note.get("cdate") or note.get("tcdate")),
            updated_date=self._date(note.get("mdate") or note.get("tmdate")),
            pdf_url=pdf_url, url=f"https://openreview.net/forum?id={note_id}", source="openreview",
            keywords=[k for k in (self._value(content, "keywords", []) or []) if isinstance(k, str)]
            if isinstance(self._value(content, "keywords", []), list) else [],
            extra={"venue": self._value(content, "venue", ""), "api_version": 2},
        )

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[Paper]:
        """Search up to 1,000 public API v2 papers; at most 1,000 hits scanned."""
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 0 <= max_results <= self.MAX_RESULTS:
            raise ValueError("OpenReview max_results must be an integer from 0 to 1000")
        if not max_results:
            return []
        if not isinstance(query, str) or not query.strip():
            raise ValueError("OpenReview query must not be empty")
        papers, seen = [], set()
        offset = 0
        while offset < self.MAX_RESULTS:
            limit = min(self.PAGE_SIZE, max_results - len(papers), self.MAX_RESULTS - offset)
            data = self._get_json("/notes/search", {
                "term": query, "content": "all", "group": "all", "source": "forum",
                "limit": limit, "offset": offset,
            })
            notes = data["notes"]
            previous_count = len(seen)
            page_ids = set()
            for note in notes:
                paper = self._parse_note(note)
                if paper:
                    page_ids.add(paper.paper_id)
                    if paper.paper_id not in seen:
                        seen.add(paper.paper_id)
                        papers.append(paper)
                        if len(papers) == max_results:
                            return papers
            if not notes or len(notes) < limit:
                return papers
            if page_ids and len(seen) == previous_count:
                raise OpenReviewError("OpenReview pagination did not advance")
            offset += len(notes)
        raise OpenReviewError("OpenReview scan limit of 1000 reached before enough public papers were found; narrow the query")

    def download_pdf(self, paper_id: str, save_path: str = "./downloads") -> str:
        note_id = self._note_id(paper_id)
        notes = self._get_json("/notes", {"id": note_id})["notes"]
        if len(notes) != 1 or not isinstance(notes[0], dict) or notes[0].get("id") != note_id:
            raise OpenReviewError("OpenReview did not return the requested paper identity")
        paper = self._parse_note(notes[0])
        if not paper or not paper.pdf_url:
            raise OpenReviewError("This note is not a public paper with an OpenReview-hosted PDF")
        try:
            response = self.session.get(self.BASE_URL + "/pdf", params={"id": note_id},
                                        timeout=60, stream=True, allow_redirects=False)
        except requests.RequestException:
            raise OpenReviewError("OpenReview PDF request failed (network or timeout)") from None
        with response:
            if response.status_code != 200:
                raise OpenReviewError(f"OpenReview public PDF returned HTTP {response.status_code}; no authenticated fallback is attempted")
            return save_verified_pdf(response, Path(save_path) / f"openreview_{note_id}.pdf", paper.title)

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        return read_pdf_text(self.download_pdf(paper_id, save_path))
