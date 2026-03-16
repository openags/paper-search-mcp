# paper_search_mcp/academic_platforms/openaire.py
from typing import List, Optional, Dict, Any
from datetime import datetime
import requests
import logging
import xml.etree.ElementTree as ET
import urllib3
import time
from requests.exceptions import SSLError

from ..paper import Paper
from ..utils import extract_doi
from ..config import get_env
from .base import PaperSource

logger = logging.getLogger(__name__)


class OpenAiresearcher(PaperSource):
    """Searcher for OpenAIRE - European Open Access Research Infrastructure"""

    BASE_URL = "https://api.openaire.eu"
    RESEARCH_PRODUCTS_URL = f"{BASE_URL}/search/researchProducts"
    RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}

    # OpenAIRE supports both JSON and XML formats
    DEFAULT_FORMAT = "json"

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize OpenAIRE searcher.

        Args:
            api_key: Optional OpenAIRE API key (not usually required for basic access)
        """
        self.api_key = api_key or get_env("OPENAIRE_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'paper-search-mcp/1.0 (https://github.com/openags/paper-search-mcp)',
            'Accept': 'application/json, application/xml'
        })
        if self.api_key:
            self.session.headers.update({'Authorization': f'Bearer {self.api_key}'})

    def _search_with_retry(self, query: str, max_results: int, **kwargs) -> List[Paper]:
        request_profiles = [
            {
                'params': {
                    'keywords': query,
                    'page': 1,
                    'size': min(max_results, 100),
                },
            },
            {
                'params': {
                    'keywords': query,
                    'page': 0,
                    'size': min(max_results, 100),
                },
                'headers': {
                    'Accept': 'application/xml',
                },
            },
            {
                'params': {
                    'keywords': query,
                    'page': 1,
                    'size': min(max_results, 100),
                },
                'use_raw_request': True,
                'headers': {
                    'User-Agent': 'Mozilla/5.0',
                    'Accept': 'application/xml,text/xml;q=0.9,*/*;q=0.8',
                },
            },
        ]

        last_error: Optional[Exception] = None
        for profile in request_profiles:
            for attempt in range(3):
                try:
                    if profile.get('use_raw_request'):
                        response = requests.get(
                            self.RESEARCH_PRODUCTS_URL,
                            params=profile['params'],
                            headers=profile.get('headers'),
                            timeout=30,
                        )
                    else:
                        response = self._get(
                            self.RESEARCH_PRODUCTS_URL,
                            params=profile['params'],
                            headers=profile.get('headers'),
                        )

                    if response.status_code in self.RETRYABLE_STATUS_CODES:
                        wait_seconds = min(8, 2 ** attempt)
                        logger.warning(
                            "OpenAIRE request returned %s (attempt %s/3). Retrying in %ss",
                            response.status_code,
                            attempt + 1,
                            wait_seconds,
                        )
                        time.sleep(wait_seconds)
                        continue

                    response.raise_for_status()
                    root = ET.fromstring(response.content)
                    papers: List[Paper] = []
                    result_nodes = self._find_top_level_results(root)

                    for node in result_nodes:
                        paper = self._parse_openaire_xml_result(node)
                        if paper and self._matches_filters(paper, kwargs):
                            papers.append(paper)
                        if len(papers) >= max_results:
                            break

                    return papers
                except Exception as exc:
                    last_error = exc

        if last_error:
            raise last_error
        return []

    @staticmethod
    def _local_name(tag: Any) -> str:
        return tag.split('}')[-1] if isinstance(tag, str) else ''

    def _first_child(self, parent: Optional[ET.Element], local_name: str) -> Optional[ET.Element]:
        if parent is None:
            return None
        for child in list(parent):
            if self._local_name(child.tag).lower() == local_name.lower():
                return child
        return None

    def _direct_texts(self, parent: Optional[ET.Element], local_name: str) -> List[str]:
        if parent is None:
            return []

        values: List[str] = []
        for child in list(parent):
            if self._local_name(child.tag).lower() == local_name.lower() and child.text:
                text = child.text.strip()
                if text:
                    values.append(text)
        return values

    def _get(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault('timeout', 30)
        try:
            return self.session.get(url, **kwargs)
        except SSLError:
            logger.warning("OpenAIRE SSL verification failed; retrying without cert verification")
            kwargs['verify'] = False
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            return self.session.get(url, **kwargs)

    def _find_top_level_results(self, root: ET.Element) -> List[ET.Element]:
        for element in root.iter():
            if self._local_name(element.tag).lower() != 'results':
                continue

            return [
                child for child in list(element)
                if self._local_name(child.tag).lower() == 'result'
            ]

        return []

    def _parse_date(self, value: str) -> Optional[datetime]:
        if not value:
            return None

        value = value.strip()
        for parser in (
            lambda x: datetime.fromisoformat(x.replace('Z', '+00:00')),
            lambda x: datetime.strptime(x[:10], '%Y-%m-%d'),
            lambda x: datetime(int(x[:4]), 1, 1),
        ):
            try:
                return parser(value)
            except Exception:
                continue
        return None

    def _matches_filters(self, paper: Paper, filters: Dict[str, Any]) -> bool:
        extra = paper.extra or {}

        year_filter = filters.get('year')
        if year_filter:
            if not paper.published_date:
                return False
            if isinstance(year_filter, str) and '-' in year_filter:
                bounds = year_filter.split('-', 1)
                if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit():
                    year = paper.published_date.year
                    if year < int(bounds[0]) or year > int(bounds[1]):
                        return False
            elif str(year_filter).isdigit() and paper.published_date.year != int(year_filter):
                return False

        if filters.get('open_access') and not extra.get('open_access'):
            return False

        language = str(filters.get('language', '')).strip().lower()
        if language:
            paper_language = str(extra.get('language', '')).strip().lower()
            if not paper_language or paper_language != language:
                return False

        from_date = self._parse_date(str(filters.get('from_date', '')))
        if from_date:
            if not paper.published_date or paper.published_date < from_date:
                return False

        to_date = self._parse_date(str(filters.get('to_date', '')))
        if to_date:
            if not paper.published_date or paper.published_date > to_date:
                return False

        return True

    def _extract_rel_data(self, rel_node: Optional[ET.Element]) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            'authors': [],
            'pids': [],
            'urls': [],
            'descriptions': [],
            'titles': [],
            'publishers': [],
            'dates': [],
            'score': 0,
        }

        if rel_node is None:
            return data

        def walk(node: ET.Element, under_children: bool = False):
            tag = self._local_name(node.tag).lower()
            next_under_children = under_children or tag == 'children'

            if not next_under_children:
                text = node.text.strip() if node.text else ''
                if text:
                    if tag == 'creator' and text not in data['authors']:
                        data['authors'].append(text)
                    elif tag in {'pid', 'identifier'} and text not in data['pids']:
                        data['pids'].append(text)
                    elif tag in {'url', 'webresource'} and text.startswith('http') and text not in data['urls']:
                        data['urls'].append(text)
                    elif tag == 'description' and text not in data['descriptions']:
                        data['descriptions'].append(text)
                    elif tag == 'title' and text not in data['titles']:
                        data['titles'].append(text)
                    elif tag == 'publisher' and text not in data['publishers']:
                        data['publishers'].append(text)
                    elif tag in {'dateofacceptance', 'publicationdate'} and text not in data['dates']:
                        data['dates'].append(text)

            for child in list(node):
                walk(child, next_under_children)

        walk(rel_node)
        data['score'] = len(data['pids']) * 3 + len(data['urls']) * 2 + len(data['authors'])
        return data

    def search(self, query: str, max_results: int = 10, **kwargs) -> List[Paper]:
        """
        Search OpenAIRE for publications.

        Args:
            query: Search query string
            max_results: Maximum results to return (default: 10)
            **kwargs: Additional parameters:
                - year: Filter by publication year
                - from_date: Filter by start date (YYYY-MM-DD)
                - to_date: Filter by end date (YYYY-MM-DD)
                - project_id: Filter by project identifier
                - organization: Filter by organization
                - open_access: Filter by open access status (true/false)
                - language: Filter by language code

        Returns:
            List of Paper objects
        """
        papers: List[Paper] = []

        try:
            papers = self._search_with_retry(query, max_results, **kwargs)
            logger.info(f"Found {len(papers)} papers from OpenAIRE for query: {query}")
            if papers:
                return papers
        except Exception as exc:
            logger.warning("OpenAIRE v2 XML search failed, attempting legacy parser fallback: %s", exc)

        # Legacy fallback path (may not be available in some environments)
        try:
            params = {
                'size': min(max_results, 100),
                'format': self.DEFAULT_FORMAT,
                'query': query,
            }
            response = self._get(f"{self.BASE_URL}/search/publications", params=params)
            response.raise_for_status()
            data = response.json()
            results = data.get('response', {}).get('results', {}).get('result', [])
            if isinstance(results, dict):
                results = [results]
            for result in results:
                paper = self._parse_openaire_result(result)
                if paper and self._matches_filters(paper, kwargs):
                    papers.append(paper)
                if len(papers) >= max_results:
                    break
        except Exception as exc:
            logger.error("OpenAIRE API request error: %s", exc)

        return papers

    def _parse_openaire_xml_result(self, node: ET.Element) -> Optional[Paper]:
        """Parse OpenAIRE researchProducts XML result element."""
        try:
            header = self._first_child(node, 'header')
            metadata = self._first_child(node, 'metadata')
            entity = self._first_child(metadata, 'entity')
            inner_result = self._first_child(entity, 'result')
            target = inner_result if inner_result is not None else node

            title_candidates = self._direct_texts(target, 'title')

            main_titles: List[str] = []
            for child in list(target):
                if self._local_name(child.tag).lower() != 'title' or not child.text:
                    continue
                value = child.text.strip()
                if not value:
                    continue
                classid = (child.get('classid') or '').lower()
                classname = (child.get('classname') or '').lower()
                if 'main' in classid or 'main' in classname:
                    main_titles.append(value)

            title = (main_titles[0] if main_titles else (title_candidates[0] if title_candidates else ''))
            if not title:
                return None

            rels_node = self._first_child(target, 'rels')
            relation_parent = rels_node if rels_node is not None else []
            relation_nodes = [
                child for child in list(relation_parent)
                if self._local_name(child.tag).lower() == 'rel'
            ]
            relation_data = [self._extract_rel_data(rel) for rel in relation_nodes]
            relation_data.sort(key=lambda x: x['score'], reverse=True)
            primary_rel = relation_data[0] if relation_data else self._extract_rel_data(None)

            authors = primary_rel['authors'][:20]

            description_candidates = self._direct_texts(target, 'description')
            if not description_candidates:
                description_candidates = primary_rel['descriptions']
            abstract = description_candidates[0] if description_candidates else ''

            pid_candidates = self._direct_texts(target, 'pid') + primary_rel['pids']
            doi = ''
            for value in pid_candidates + [abstract, title] + primary_rel['titles']:
                doi = extract_doi(value)
                if doi:
                    break

            date_candidates = self._direct_texts(target, 'publicationdate') + \
                self._direct_texts(target, 'dateofacceptance') + primary_rel['dates']
            published_date = None
            for date_value in date_candidates:
                published_date = self._parse_date(date_value)
                if published_date:
                    break

            direct_urls = self._direct_texts(target, 'url') + self._direct_texts(target, 'webresource')
            code_repo_urls = self._direct_texts(target, 'codeRepositoryUrl')
            url_candidates = [
                value for value in (direct_urls + code_repo_urls + primary_rel['urls'] + pid_candidates)
                if value.startswith('http')
            ]
            url = url_candidates[0] if url_candidates else (f"https://doi.org/{doi}" if doi else '')
            pdf_url = ''
            for candidate in url_candidates:
                lowered = candidate.lower()
                if lowered.endswith('.pdf') or '/pdf' in lowered:
                    pdf_url = candidate
                    break

            obj_ids = self._direct_texts(header, 'objIdentifier')
            paper_id = obj_ids[0] if obj_ids else f"openaire_{hash(title) & 0xffffffff:08x}"

            if not url:
                url = f"https://explore.openaire.eu/search/publication?articleId={paper_id}"

            best_access_elem = self._first_child(target, 'bestaccessright')
            best_access = ''
            if best_access_elem is not None:
                best_access = (
                    best_access_elem.get('classname')
                    or best_access_elem.get('classid')
                    or (best_access_elem.text or '')
                ).strip()
            open_access = 'open' in best_access.lower()

            language_elem = self._first_child(target, 'language')
            language = ''
            if language_elem is not None:
                language = (
                    language_elem.get('classname')
                    or language_elem.get('classid')
                    or (language_elem.text or '')
                ).strip()
                if language.lower() in {'und', 'undefined', 'unknown', 'undetermined'}:
                    language = ''

            result_type_elem = self._first_child(target, 'resulttype')
            result_type = ''
            if result_type_elem is not None:
                result_type = (
                    result_type_elem.get('classname')
                    or result_type_elem.get('classid')
                    or (result_type_elem.text or '')
                ).strip()

            resource_type_elem = self._first_child(target, 'resourcetype')
            resource_type = ''
            if resource_type_elem is not None:
                resource_type = (
                    resource_type_elem.get('classname')
                    or resource_type_elem.get('classid')
                    or (resource_type_elem.text or '')
                ).strip()

            publisher = ''
            publisher_candidates = self._direct_texts(target, 'publisher') + primary_rel['publishers']
            if publisher_candidates:
                publisher = publisher_candidates[0]

            journal = ''
            journal_candidates = self._direct_texts(target, 'journal')
            if journal_candidates:
                journal = journal_candidates[0]

            subject_candidates = self._direct_texts(target, 'subject')
            keywords = subject_candidates[:10]

            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url,
                source='openaire',
                keywords=keywords,
                extra={
                    'open_access': open_access,
                    'best_access_right': best_access,
                    'publisher': publisher,
                    'journal': journal,
                    'language': language,
                    'type': result_type,
                    'resource_type': resource_type,
                }
            )
        except Exception as exc:
            logger.warning("Error parsing OpenAIRE XML result: %s", exc)
            return None

    def _parse_openaire_result(self, result: Dict[str, Any]) -> Optional[Paper]:
        """Parse an OpenAIRE API result into a Paper object."""
        try:
            # Extract metadata from result
            metadata = result.get('metadata', {})
            if not metadata:
                return None

            # Extract title
            title_info = metadata.get('title', {})
            if isinstance(title_info, dict):
                title = title_info.get('value', '')
            elif isinstance(title_info, list) and len(title_info) > 0:
                title = title_info[0].get('value', '') if isinstance(title_info[0], dict) else str(title_info[0])
            else:
                title = str(title_info) if title_info else ''

            title = title.strip()
            if not title:
                return None

            # Extract authors
            authors = []
            creators = metadata.get('creator', [])
            if isinstance(creators, list):
                for creator in creators:
                    if isinstance(creator, dict):
                        author_name = creator.get('value', '')
                        if author_name:
                            authors.append(author_name)
                    elif isinstance(creator, str):
                        authors.append(creator)
            elif isinstance(creators, dict):
                author_name = creators.get('value', '')
                if author_name:
                    authors.append(author_name)

            # Extract abstract
            abstract = ''
            description = metadata.get('description', {})
            if isinstance(description, dict):
                abstract = description.get('value', '')
            elif isinstance(description, list) and len(description) > 0:
                abstract = description[0].get('value', '') if isinstance(description[0], dict) else str(description[0])
            else:
                abstract = str(description) if description else ''

            # Extract DOI
            doi = ''
            identifiers = metadata.get('identifier', [])
            if isinstance(identifiers, list):
                for identifier in identifiers:
                    if isinstance(identifier, dict):
                        id_value = identifier.get('value', '')
                        id_type = identifier.get('type', '').upper()
                        if id_type == 'DOI' and id_value:
                            doi = id_value.replace('doi:', '').replace('https://doi.org/', '')
                            break
            elif isinstance(identifiers, dict):
                id_value = identifiers.get('value', '')
                id_type = identifiers.get('type', '').upper()
                if id_type == 'DOI' and id_value:
                    doi = id_value.replace('doi:', '').replace('https://doi.org/', '')

            # If no DOI found in identifiers, try to extract from abstract
            if not doi and abstract:
                doi = extract_doi(abstract)

            # Extract publication date
            published_date = None
            dates = metadata.get('dateofacceptance', []) or metadata.get('publicationdate', [])
            if isinstance(dates, list) and len(dates) > 0:
                date_str = dates[0].get('value', '') if isinstance(dates[0], dict) else str(dates[0])
                if date_str:
                    try:
                        # Try to parse date (format may vary)
                        published_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    except ValueError:
                        try:
                            published_date = datetime.strptime(date_str[:10], '%Y-%m-%d')
                        except ValueError:
                            pass

            # Extract URLs
            url = ''
            links = result.get('header', {}).get('dri:objIdentifier', [])
            if isinstance(links, list) and len(links) > 0:
                url = links[0].get('value', '') if isinstance(links[0], dict) else str(links[0])
            elif isinstance(links, dict):
                url = links.get('value', '')

            # Construct paper ID
            paper_id = result.get('header', {}).get('dri:objIdentifier', '')
            if isinstance(paper_id, dict):
                paper_id = paper_id.get('value', '')
            elif isinstance(paper_id, list) and len(paper_id) > 0:
                paper_id = paper_id[0].get('value', '') if isinstance(paper_id[0], dict) else str(paper_id[0])

            if not paper_id:
                paper_id = f"openaire_{hash(title) & 0xffffffff:08x}"

            # Extract PDF URL
            pdf_url = ''
            files = metadata.get('bestaccessright', {}).get('classname', '')
            if 'OPEN' in str(files).upper():
                # Try to find PDF in related identifiers or files
                relations = metadata.get('relation', [])
                if isinstance(relations, list):
                    for relation in relations:
                        if isinstance(relation, dict):
                            rel_type = relation.get('type', '').upper()
                            rel_url = relation.get('value', '')
                            if rel_type == 'HASPAGE' and rel_url:
                                pdf_url = rel_url
                                break

            # If no PDF URL found, use DOI link
            if not pdf_url and doi:
                pdf_url = f"https://doi.org/{doi}"

            # Extract additional metadata
            publisher = metadata.get('publisher', {})
            if isinstance(publisher, dict):
                publisher_name = publisher.get('value', '')
            else:
                publisher_name = str(publisher) if publisher else ''

            journal = metadata.get('journal', {})
            if isinstance(journal, dict):
                journal_name = journal.get('value', '')
            else:
                journal_name = str(journal) if journal else ''

            # Extract keywords
            keywords = []
            subjects = metadata.get('subject', [])
            if isinstance(subjects, list):
                for subject in subjects:
                    if isinstance(subject, dict):
                        keyword = subject.get('value', '')
                        if keyword:
                            keywords.append(keyword)
                    elif isinstance(subject, str):
                        keywords.append(subject)

            # Create Paper object
            return Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                abstract=abstract,
                doi=doi,
                published_date=published_date,
                pdf_url=pdf_url,
                url=url if url else f"https://explore.openaire.eu/search/publication?articleId={paper_id}",
                source='openaire',
                keywords=keywords[:10],
                extra={
                    'publisher': publisher_name,
                    'journal': journal_name,
                    'open_access': 'OPEN' in str(metadata.get('bestaccessright', {})).upper(),
                    'project_id': metadata.get('projectid', ''),
                    'organization': metadata.get('organization', ''),
                    'language': metadata.get('language', ''),
                    'type': metadata.get('type', ''),
                }
            )

        except Exception as e:
            logger.warning(f"Error parsing OpenAIRE result data: {e}")
            return None

    def download_pdf(self, paper_id: str, save_path: str) -> str:
        """
        Download PDF for an OpenAIRE paper.

        Note: OpenAIRE provides links to open access versions when available.

        Args:
            paper_id: OpenAIRE paper identifier
            save_path: Directory to save the PDF

        Returns:
            Path to the saved PDF file

        Raises:
            Exception: If download fails or no PDF available
        """
        # Implementation would need to:
        # 1. Fetch paper details to get PDF URL
        # 2. Download the PDF if available
        # 3. Save to specified path

        raise NotImplementedError(
            f"{self.__class__.__name__} PDF download not implemented yet. "
            "Use the pdf_url field from search results for direct access."
        )

    def read_paper(self, paper_id: str, save_path: str = "./downloads") -> str:
        """
        Download and extract text from an OpenAIRE paper.

        Args:
            paper_id: OpenAIRE paper identifier
            save_path: Directory where PDF is/will be saved

        Returns:
            Extracted text content of the paper

        Raises:
            NotImplementedError: If paper reading is not supported
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support direct paper reading."
        )


# For testing
if __name__ == "__main__":
    import sys

    searcher = OpenAiresearcher()

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = "climate change"

    print(f"Searching OpenAIRE for: {query}")
    papers = searcher.search(query, max_results=5)

    print(f"Found {len(papers)} papers:")
    for i, paper in enumerate(papers):
        print(f"\n{i+1}. {paper.title}")
        print(f"   Authors: {', '.join(paper.authors[:3])}{'...' if len(paper.authors) > 3 else ''}")
        print(f"   DOI: {paper.doi}")
        print(f"   Year: {paper.published_date.year if paper.published_date else 'N/A'}")
        print(f"   Open Access: {paper.extra.get('open_access', 'N/A')}")
        print(f"   Publisher: {paper.extra.get('publisher', 'N/A')}")
        print(f"   URL: {paper.url}")