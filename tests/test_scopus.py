"""Offline Scopus/ScienceDirect fixtures; no live entitlement claims."""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
import requests

from paper_search_mcp import cli, config, server
from paper_search_mcp.academic_platforms.institutional import (
    CredentialsRequiredError, IdentityMismatchError, PermissionDeniedError,
    ProviderResponseError, ProviderTimeoutError, RateLimitError,
)
from paper_search_mcp.academic_platforms.scopus import ScopusSearcher


def response(payload=None, status=200, content=b''):
    result = Mock(status_code=status, headers={}, content=content)
    result.json.return_value = payload
    return result


def record(number=1):
    return {'dc:identifier': f'SCOPUS_ID:{number}', 'dc:title': f'Title {number}',
            'dc:creator': 'Alice', 'prism:doi': f'10.1234/{number}',
            'prism:coverDate': '2024-01-02', 'citedby-count': '12',
            'link': [{'@ref': 'scopus', '@href': f'https://www.scopus.com/record/{number}'}]}


def search_payload(entries, total):
    return {'search-results': {'entry': entries, 'opensearch:totalResults': str(total)}}


def details(number=1, doi='10.1234/1', pii='S0123456789012345', abstract='An abstract'):
    return {'abstracts-retrieval-response': {'coredata': {
        'dc:identifier': f'SCOPUS_ID:{number}', 'dc:title': 'Target title',
        'prism:doi': doi, 'pii': pii, 'dc:description': abstract}}}


def article(doi='10.1234/1', pii='S0123456789012345', body='<body><section>Article body text</section></body>'):
    return f'''<full-text-retrieval-response xmlns="http://www.elsevier.com/xml/svapi/article/dtd"
      xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
      <coredata><prism:doi>{doi}</prism:doi><pii>{pii}</pii></coredata>
      <originalText><doc><article>{body}</article></doc></originalText>
    </full-text-retrieval-response>'''.encode()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(config, '_ENV_LOADED', True)
    monkeypatch.setattr(cli, 'SEARCHERS', {})
    for name in ('SCOPUS_API_KEY', 'SCOPUS_INST_TOKEN'):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv('PAPER_SEARCH_MCP_' + name, raising=False)


@pytest.fixture
def searcher():
    result = ScopusSearcher('fixture-key')
    result.session = Mock()
    return result


def test_metadata_search_default_standard(searcher):
    searcher.session.get.return_value = response(search_payload([record()], 1))
    paper, = searcher.search('TITLE(test)')
    assert paper.title == 'Title 1'
    assert paper.authors == ['Alice']
    assert paper.citations == 12 and paper.doi == '10.1234/1'
    assert paper.published_date.year == 2024
    assert paper.pdf_url == '' and paper.source == 'scopus'
    call = searcher.session.get.call_args
    assert call.kwargs['params'] == {'query': 'TITLE(test)', 'view': 'STANDARD', 'sort': 'relevancy', 'count': 10, 'start': 0}
    assert call.kwargs['headers']['X-ELS-APIKey'] == 'fixture-key'
    assert 'apiKey' not in call.kwargs['params']


def test_richer_author_normalization(searcher):
    item = record()
    item['author'] = {'authname': 'Bob'}
    searcher.session.get.return_value = response(search_payload(item, 1))
    assert searcher.search('test')[0].authors == ['Bob']


def test_pagination_is_bounded_and_deduplicated(searcher):
    searcher.session.get.side_effect = [response(search_payload([record(i) for i in range(j * 25, (j + 1) * 25)], 500))
                                       for j in range(4)]
    assert len(searcher.search('test', 100, view='COMPLETE')) == 100
    assert [call.kwargs['params']['start'] for call in searcher.session.get.call_args_list] == [0, 25, 50, 75]
    assert all(call.kwargs['params']['count'] == 25 for call in searcher.session.get.call_args_list)


def test_repeated_page_does_not_trigger_unbounded_retry(searcher):
    searcher.session.get.return_value = response(search_payload([record(i) for i in range(25)], 500))
    assert len(searcher.search('test', 100)) == 25
    assert searcher.session.get.call_count == 4


@pytest.mark.parametrize('entries', [[], [{'error': 'Result set was empty'}], {'error': 'Result set was empty'}])
def test_true_empty_search(searcher, entries):
    searcher.session.get.return_value = response(search_payload(entries, 0))
    assert searcher.search('nonsense') == []


@pytest.mark.parametrize('payload', [{}, {'search-results': []}, {'search-results': {}},
    search_payload([{'error': 'AUTHORIZATION_ERROR'}], 0), search_payload([record()], 0),
    search_payload([{}], 1), search_payload([], 2), search_payload('bad', 2)])
def test_malformed_or_error_payload_is_not_empty(searcher, payload):
    searcher.session.get.return_value = response(payload)
    with pytest.raises(ProviderResponseError):
        searcher.search('test')


@pytest.mark.parametrize('date,expected', [('2024','2024'), ('2020-2024','2020-2024'),
    ('-2020','1788-2020'), ('2024-', f'2024-{datetime.now(timezone.utc).year + 1}')])
def test_date_normalization(searcher, date, expected):
    searcher.session.get.return_value = response(search_payload([], 0))
    searcher.search('test', date=date)
    assert searcher.session.get.call_args.kwargs['params']['date'] == expected


@pytest.mark.parametrize('options', [{'max_results': 101}, {'max_results': -1}, {'view': 'FULL'},
    {'sort': 'unknown'}, {'field': 'bad'}, {'date': '2024-2020'}, {'date': 'bad'}, {'date': '1600'}])
def test_invalid_options_do_not_request(searcher, options):
    with pytest.raises(ValueError):
        searcher.search('test', **options)
    searcher.session.get.assert_not_called()


def test_missing_key_and_prefixed_precedence(monkeypatch):
    searcher = ScopusSearcher()
    searcher.session = Mock()
    monkeypatch.setenv('SCOPUS_API_KEY', 'legacy')
    monkeypatch.setenv('PAPER_SEARCH_MCP_SCOPUS_API_KEY', ' ')
    with pytest.raises(CredentialsRequiredError):
        searcher.search('test')
    searcher.session.get.assert_not_called()
    monkeypatch.setenv('PAPER_SEARCH_MCP_SCOPUS_API_KEY', 'preferred')
    monkeypatch.setenv('PAPER_SEARCH_MCP_SCOPUS_INST_TOKEN', 'existing-token')
    searcher.session.get.return_value = response(search_payload([], 0))
    searcher.search('test')
    assert searcher.session.get.call_args.kwargs['headers']['X-ELS-APIKey'] == 'preferred'
    assert searcher.session.get.call_args.kwargs['headers']['X-ELS-Insttoken'] == 'existing-token'


@pytest.mark.parametrize('key', ['', 'fixture-key'])
def test_explicit_source_only_even_with_key(key, monkeypatch):
    monkeypatch.setenv('PAPER_SEARCH_MCP_SCOPUS_API_KEY', key)
    assert server._parse_sources('scopus') == cli._parse_sources('scopus') == ['scopus']
    assert server._invalid_sources('scopus') == []
    assert 'scopus' not in server._parse_sources('all')
    for preset in ('all', 'fast', 'fastest'):
        assert 'scopus' not in cli._parse_sources(preset)


def test_read_defaults_to_abstract_without_article_request(searcher):
    searcher.session.get.return_value = response(details())
    result = searcher.read_paper('1')
    assert result['status'] == 'abstract_only'
    assert result['full_text'] == '' and result['abstract'] == 'An abstract'
    assert not result['full_text_requested']
    assert searcher.session.get.call_count == 1


@pytest.mark.parametrize('paper_id', ['../1', '1?key=x', '1/2', 'https://evil.test', '１２３', ''])
def test_bad_ids_never_reach_network(searcher, paper_id):
    with pytest.raises(ValueError):
        searcher.read_paper(paper_id, full_text=True)
    searcher.session.get.assert_not_called()


@pytest.mark.parametrize('payload', [details(number=2), {'abstracts-retrieval-response': {'coredata': {}}}])
def test_abstract_identity_must_match_before_article_request(searcher, payload):
    searcher.session.get.return_value = response(payload)
    with pytest.raises(IdentityMismatchError):
        searcher.read_paper('1', full_text=True)
    assert searcher.session.get.call_count == 1


def test_direct_doi_identity_verified_full_text(searcher):
    searcher.session.get.side_effect = [response(details(doi='https://doi.org/10.1234/1')), response(content=article())]
    result = searcher.read_paper('SCOPUS_ID:1', full_text=True)
    assert result['status'] == 'full_text'
    assert result['full_text'] == 'Article body text'
    calls = searcher.session.get.call_args_list
    assert calls[1].args[0] == ScopusSearcher.ARTICLE_URL + 'doi/10.1234%2F1'
    assert calls[1].kwargs['params'] == {'view': 'FULL'}
    assert all('/search/sciencedirect' not in call.args[0] for call in calls)


def test_direct_pii_route_when_no_doi(searcher):
    searcher.session.get.side_effect = [response(details(doi='')), response(content=article(doi=''))]
    assert searcher.read_paper('1', full_text=True)['status'] == 'full_text'
    assert '/pii/S0123456789012345' in searcher.session.get.call_args.args[0]


def test_no_identifiers_never_searches_by_title(searcher):
    searcher.session.get.return_value = response(details(doi='', pii=''))
    result = searcher.read_paper('1', full_text=True)
    assert result['status'] == 'abstract_only' and result['reason'] == 'no_verified_doi_or_pii'
    assert searcher.session.get.call_count == 1


@pytest.mark.parametrize('content', [article(doi='10.1234/wrong'), article(pii='S9999999999999999'),
    article(doi='', pii=''), article(doi='10.1234/wrong', body='<body>The target DOI is 10.1234/1</body>')])
def test_conflicting_or_missing_article_identity_never_returns_content(searcher, content):
    searcher.session.get.side_effect = [response(details()), response(content=content)]
    with pytest.raises(IdentityMismatchError):
        searcher.read_paper('1', full_text=True)


@pytest.mark.parametrize('body', ['', '<abstract>This is only an abstract.</abstract>', 'Plain originalText'])
def test_abstract_or_raw_originaltext_is_never_fulltext(searcher, body):
    searcher.session.get.side_effect = [response(details()), response(content=article(body=body))]
    result = searcher.read_paper('1', full_text=True)
    assert result['status'] == 'abstract_only'
    assert result['full_text'] == '' and result['reason'] == 'article_body_unavailable'


@pytest.mark.parametrize('status', [401, 403, 404])
def test_article_unavailable_does_not_impersonate_success(searcher, status):
    searcher.session.get.side_effect = [response(details()), response(status=status)]
    result = searcher.read_paper('1', full_text=True)
    assert result['status'] == 'abstract_only' and result['full_text'] == ''
    assert result['full_text_error']['status_code'] == status
    assert result['reason'] == ('not_found' if status == 404 else 'permission_denied')


@pytest.mark.parametrize('failure,error', [(response(status=429), RateLimitError),
    (requests.Timeout('secret'), ProviderTimeoutError)])
def test_fulltext_rate_or_timeout_is_a_typed_failure(searcher, failure, error):
    searcher.session.get.side_effect = [response(details()), failure]
    with pytest.raises(error):
        searcher.read_paper('1', full_text=True)


@pytest.mark.parametrize('content', [b'<html>login</html>', b'plain text',
    b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]>' + article()])
def test_unsafe_or_non_article_xml_rejected(searcher, content):
    searcher.session.get.side_effect = [response(details()), response(content=content)]
    with pytest.raises(ProviderResponseError):
        searcher.read_paper('1', full_text=True)


def test_no_abstract_or_body_is_unavailable(searcher):
    searcher.session.get.return_value = response(details(abstract=''))
    assert searcher.read_paper('1')['status'] == 'unavailable'


def test_cli_and_mcp_search_options_parity(searcher, monkeypatch, capsys):
    searcher.session.get.return_value = response(search_payload([record()], 1))
    monkeypatch.setattr(cli, 'SEARCHERS', {'scopus': searcher})
    args = cli.build_parser().parse_args(['search', 'test', '-s', 'scopus', '-n', '4',
        '--scopus-view', 'COMPLETE', '--scopus-sort', 'citedby-count', '--scopus-field', 'TITLE', '--scopus-date', '2024'])
    asyncio.run(cli.cmd_search(args))
    cli_result = json.loads(capsys.readouterr().out)
    cli_params = searcher.session.get.call_args.kwargs['params']
    monkeypatch.setattr('paper_search_mcp.academic_platforms.scopus.ScopusSearcher', lambda: searcher)
    mcp_result = asyncio.run(server.search_scopus('test', 4, 'COMPLETE', 'citedby-count', 'TITLE', '2024'))
    assert cli_result['papers'] == mcp_result
    assert cli_params == searcher.session.get.call_args.kwargs['params']


@pytest.mark.parametrize('status,request_full_text,code', [('full_text', True, 0), ('abstract_only', True, 2),
    ('abstract_only', False, 0), ('unavailable', False, 1), ('unavailable', True, 1)])
def test_cli_read_status_and_exit_code(status, request_full_text, code, monkeypatch, capsys):
    searcher = Mock()
    searcher.read_paper.return_value = {'status': status}
    monkeypatch.setattr(cli, 'SEARCHERS', {'scopus': searcher})
    args = cli.build_parser().parse_args(['read', 'scopus', '1'] + (['--full-text'] if request_full_text else []))
    assert asyncio.run(cli.cmd_read(args)) == code
    assert json.loads(capsys.readouterr().out)['status'] == status
    searcher.read_paper.assert_called_once_with('1', './downloads', full_text=request_full_text)


def test_missing_credentials_exposed_in_both_aggregates(capsys):
    result = asyncio.run(server.search_papers('test', sources='scopus'))
    assert '[credentials_required]' in result['errors']['scopus']
    args = cli.build_parser().parse_args(['search', 'test', '-s', 'scopus'])
    asyncio.run(cli.cmd_search(args))
    assert '[credentials_required]' in json.loads(capsys.readouterr().out)['errors']['scopus']
