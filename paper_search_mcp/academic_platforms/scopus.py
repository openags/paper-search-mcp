"""Explicit Scopus metadata and identity-verified ScienceDirect text retrieval.

Adapted from PR #89. No title-search fallback, redirects, retries, or PDF writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import quote

from lxml import etree
import requests

from .base import PaperSource
from .institutional import (
    CredentialsRequiredError, IdentityMismatchError, PermissionDeniedError,
    ProviderResponseError, RecordNotFoundError, request, request_json,
    result_limit, safe_int, text,
)
from ..config import get_env
from ..paper import Paper


def _doi(value: str) -> str:
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value.strip(), flags=re.I)
    return value.lower() if re.fullmatch(r'10\.\d{4,9}/\S+', value) else ''


def _pii(value: str) -> str:
    value = re.sub(r'[()\-\s]', '', value).upper()
    return value if re.fullmatch(r'[A-Z0-9]{8,32}', value) else ''


def _scopus_id(value: str) -> str:
    value = value.strip().removeprefix('SCOPUS_ID:')
    if not re.fullmatch(r'[0-9]{1,20}', value):
        raise ValueError('Expected a numeric Scopus ID (optional SCOPUS_ID: prefix)')
    return value


class ScopusSearcher(PaperSource):
    SEARCH_URL = 'https://api.elsevier.com/content/search/scopus'
    ABSTRACT_URL = 'https://api.elsevier.com/content/abstract/scopus_id/'
    ARTICLE_URL = 'https://api.elsevier.com/content/article/'

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self.session = requests.Session()

    def _headers(self, accept: str = 'application/json') -> dict:
        key = (get_env('SCOPUS_API_KEY', '') if self._api_key is None else self._api_key).strip()
        if not key:
            raise CredentialsRequiredError('scopus', 'Set PAPER_SEARCH_MCP_SCOPUS_API_KEY (or SCOPUS_API_KEY).')
        headers = {'X-ELS-APIKey': key, 'Accept': accept, 'User-Agent': 'paper-search-mcp/0.1.4'}
        token = get_env('SCOPUS_INST_TOKEN', '').strip()
        if token:
            headers['X-ELS-Insttoken'] = token
        return headers

    @staticmethod
    def _normalize_date_range(value: str) -> str:
        value = value.strip()
        if not value:
            return ''
        if not re.fullmatch(r'(?:\d{4}|\d{4}-\d{4}|\d{4}-|-\d{4})', value):
            raise ValueError('Scopus date must be YYYY, YYYY-YYYY, YYYY-, or -YYYY')
        if value.endswith('-'):
            value += str(datetime.now(timezone.utc).year + 1)
        if value.startswith('-'):
            value = '1788' + value
        years = [int(year) for year in value.split('-')]
        if min(years) < 1788 or years[0] > years[-1]:
            raise ValueError('Scopus date range is invalid')
        return value

    def search(self, query: str, max_results: int = 10, *, view: str = 'STANDARD',
               sort: str = 'relevance', field: str = '', date: str = '') -> list[Paper]:
        """At most four pages / 100 metadata results; COMPLETE is explicit opt-in."""
        limit = result_limit(max_results)
        if not query.strip() or not limit:
            return []
        if view not in {'STANDARD', 'COMPLETE'}:
            raise ValueError('Scopus view must be STANDARD or COMPLETE')
        sorts = {'relevance': 'relevancy', 'coverDate': '-coverDate',
                 'citedby-count': '-citedby-count', 'creator': '+creator'}
        if sort not in sorts:
            raise ValueError('Unsupported Scopus sort')
        if field not in {'', 'TITLE', 'ABS', 'KEY', 'AUTH', 'AFFILORG'}:
            raise ValueError('Unsupported Scopus field')
        headers = self._headers()
        params = {'query': f'{field}({query.strip()})' if field else query.strip(),
                  'view': view, 'sort': sorts[sort], 'count': min(limit, 25)}
        normalized_date = self._normalize_date_range(date)
        if normalized_date:
            params['date'] = normalized_date
        papers: list[Paper] = []
        seen: set[str] = set()
        for page in range(4):
            params['start'] = page * params['count']
            payload = request_json(self.session, 'scopus', self.SEARCH_URL,
                                   headers=headers, params=dict(params))
            envelope = payload.get('search-results')
            if not isinstance(envelope, dict):
                raise ProviderResponseError('scopus', 'Expected a search-results envelope.')
            total = safe_int(envelope.get('opensearch:totalResults'), default=-1)
            entries = envelope.get('entry', [])
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                raise ProviderResponseError('scopus', 'Expected an entry array.')
            if total == 0:
                if any(isinstance(entry, dict) and entry.get('dc:identifier') for entry in entries):
                    raise ProviderResponseError('scopus', 'Zero count conflicts with result records.')
                if all(isinstance(entry, dict) and entry.get('error') == 'Result set was empty' for entry in entries):
                    return papers
            if not entries and total != 0:
                raise ProviderResponseError('scopus', 'Missing entries without an explicit zero count.')
            for item in entries:
                if not isinstance(item, dict) or 'error' in item:
                    raise ProviderResponseError('scopus', 'API returned an invalid result entry.')
                paper = self._parse_paper(item)
                if paper.paper_id not in seen:
                    seen.add(paper.paper_id)
                    papers.append(paper)
                if len(papers) == limit:
                    return papers
            if len(entries) < params['count'] or (total >= 0 and params['start'] + len(entries) >= total):
                break
        return papers

    @staticmethod
    def _parse_paper(item: dict) -> Paper:
        try:
            paper_id = _scopus_id(text(item.get('dc:identifier')))
        except ValueError as exc:
            raise ProviderResponseError('scopus', 'A record has no valid Scopus ID.') from exc
        title = text(item.get('dc:title'))
        if not title:
            raise ProviderResponseError('scopus', 'A record has no title.')
        authors = item.get('author') or []
        if isinstance(authors, dict):
            authors = [authors]
        authors = [text(author.get('authname')) if isinstance(author, dict) else text(author)
                   for author in authors] if isinstance(authors, list) else []
        authors = [author for author in authors if author] or ([text(item['dc:creator'])] if item.get('dc:creator') else [])
        try:
            published = datetime.fromisoformat(text(item.get('prism:coverDate')))
        except ValueError:
            published = None
        url = text(item.get('prism:url'))
        links = item.get('link', [])
        if isinstance(links, dict):
            links = [links]
        for link in links if isinstance(links, list) else []:
            if isinstance(link, dict) and link.get('@ref') == 'scopus':
                url = text(link.get('@href')) or url
                break
        return Paper(paper_id=paper_id, title=title, authors=authors,
                     abstract=text(item.get('dc:description')), doi=_doi(text(item.get('prism:doi'))),
                     published_date=published, pdf_url='', url=url, source='scopus',
                     citations=safe_int(item.get('citedby-count')),
                     extra={'metadata_only': True, 'citation_count_available': 'citedby-count' in item})

    def download_pdf(self, paper_id: str, save_path: str = './downloads') -> str:
        raise NotImplementedError('Scopus PDF download is unsupported; use the DOI with an authorized PDF source.')

    def read_paper(self, paper_id: str, save_path: str = './downloads', *, full_text: bool = False) -> dict:
        """Return explicit abstract_only/full_text/unavailable status; never write a file.

        Only full_text=True attempts ScienceDirect, directly by verified DOI/PII.
        API failures raise typed errors; article entitlement/not-found is reported
        separately from a successfully retrieved abstract.
        """
        paper_id = _scopus_id(paper_id)
        payload = request_json(self.session, 'scopus', self.ABSTRACT_URL + paper_id,
                               headers=self._headers(), params={'view': 'FULL'})
        envelope = payload.get('abstracts-retrieval-response')
        core = envelope.get('coredata') if isinstance(envelope, dict) else None
        if not isinstance(core, dict):
            raise ProviderResponseError('scopus', 'Expected abstract coredata.')
        try:
            returned_id = _scopus_id(text(core.get('dc:identifier')))
        except ValueError as exc:
            raise IdentityMismatchError('scopus', 'Abstract identity could not be verified.') from exc
        if returned_id != paper_id:
            raise IdentityMismatchError('scopus', 'Abstract Scopus ID does not match the requested record.')
        doi = _doi(text(core.get('prism:doi')))
        pii = _pii(text(core.get('pii')))
        abstract = text(core.get('dc:description'))
        result = {'status': 'abstract_only' if abstract else 'unavailable',
                  'paper_id': paper_id, 'title': text(core.get('dc:title')),
                  'doi': doi, 'pii': pii, 'abstract': abstract, 'full_text': '',
                  'full_text_requested': full_text, 'reason': 'full_text_not_requested'}
        if not full_text:
            if not abstract:
                result['reason'] = 'abstract_unavailable'
            return result
        if not doi and not pii:
            result['reason'] = 'no_verified_doi_or_pii'
            return result
        route = 'doi/' + quote(doi, safe='') if doi else 'pii/' + quote(pii, safe='')
        try:
            response = request(self.session, 'sciencedirect', self.ARTICLE_URL + route,
                               headers=self._headers('text/xml'), params={'view': 'FULL'})
        except (PermissionDeniedError, RecordNotFoundError) as exc:
            result['reason'] = exc.code
            result['full_text_error'] = {'code': exc.code, 'status_code': exc.status_code,
                                         'message': str(exc)}
            return result
        body = self._verified_article_body(response.content, doi, pii)
        if body:
            result.update(status='full_text', full_text=body, reason='verified_article_body')
        else:
            result['reason'] = 'article_body_unavailable'
        return result

    @staticmethod
    def _verified_article_body(content: bytes, expected_doi: str, expected_pii: str) -> str:
        if len(content) > 8 * 1024 * 1024:
            raise ProviderResponseError('sciencedirect', 'Article exceeds the 8 MiB parsing limit.')
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                                 remove_comments=True, remove_pis=True)
        try:
            root = etree.fromstring(content, parser)
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise ProviderResponseError('sciencedirect', 'Expected structured article XML.') from exc
        if root.getroottree().docinfo.doctype or etree.QName(root).localname != 'full-text-retrieval-response':
            raise ProviderResponseError('sciencedirect', 'Unexpected article XML envelope.')
        core = root.xpath('./*[local-name()="coredata"]')
        if len(core) != 1:
            raise IdentityMismatchError('sciencedirect', 'Article identity metadata is missing.')
        def values(name, normalize):
            return {normalize(''.join(node.itertext())) for node in core[0]
                    if etree.QName(node).localname == name} - {''}
        dois = values('doi', _doi)
        piis = values('pii', _pii)
        if (expected_doi and dois and dois != {expected_doi}) or (expected_pii and piis and piis != {expected_pii}):
            raise IdentityMismatchError('sciencedirect', 'Article identifiers conflict with the target.')
        if not ((expected_doi and expected_doi in dois) or (expected_pii and expected_pii in piis)):
            raise IdentityMismatchError('sciencedirect', 'Article DOI/PII could not be verified.')
        # Never treat coredata/abstract/originalText strings as full text. Only a
        # structured article body qualifies; reference DOI mentions cannot prove identity.
        bodies = root.xpath('./*[local-name()="originalText"]//*[local-name()="body"]')
        if len(bodies) != 1:
            return ''
        return ' '.join(' '.join(bodies[0].itertext()).split())
