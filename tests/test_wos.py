"""Synthetic fixtures match the official Starter schema; no credentials/network."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from paper_search_mcp import cli, config, server
from paper_search_mcp.academic_platforms.institutional import CredentialsRequiredError, ProviderResponseError
from paper_search_mcp.academic_platforms.wos import WebOfScienceSearcher


def hit(number=1):
    return {'uid': f'WOS:{number}', 'title': f'Title {number}',
            'source': {'publishYear': 2024, 'sourceTitle': 'Journal'},
            'names': {'authors': [{'displayName': 'Alice'}, {'displayName': 'Bob'}]},
            'identifiers': {'doi': f'10.1234/{number}'},
            'links': {'record': f'https://www.webofscience.com/wos/woscc/full-record/WOS:{number}'},
            'citations': [{'db': 'WOS', 'count': 0}, {'db': 'MEDLINE', 'count': 99}]}


def response(hits, total):
    result = Mock(status_code=200, headers={})
    result.json.return_value = {'metadata': {'total': total}, 'hits': hits}
    return result


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(config, '_ENV_LOADED', True)
    monkeypatch.setattr(cli, 'SEARCHERS', {})
    monkeypatch.delenv('PAPER_SEARCH_MCP_WOS_API_KEY', raising=False)
    monkeypatch.delenv('WOS_API_KEY', raising=False)


def test_no_credentials_no_network():
    searcher = WebOfScienceSearcher()
    searcher.session = Mock()
    assert not searcher.is_configured()
    with pytest.raises(CredentialsRequiredError):
        searcher.search('TI=(test)')
    searcher.session.get.assert_not_called()


def test_prefixed_key_precedence_and_runtime_reload(monkeypatch):
    searcher = WebOfScienceSearcher()
    monkeypatch.setenv('WOS_API_KEY', 'legacy')
    assert searcher.is_configured()
    monkeypatch.setenv('PAPER_SEARCH_MCP_WOS_API_KEY', ' ')
    assert not searcher.is_configured()
    monkeypatch.setenv('PAPER_SEARCH_MCP_WOS_API_KEY', 'preferred')
    searcher.session = Mock()
    searcher.session.get.return_value = response([], 0)
    assert searcher.search('TI=(test)') == []
    assert searcher.session.get.call_args.kwargs['headers']['X-ApiKey'] == 'preferred'


def test_normalized_metadata():
    searcher = WebOfScienceSearcher('fixture-key')
    searcher.session = Mock()
    searcher.session.get.return_value = response([hit()], 1)
    paper, = searcher.search('TI=(test)')
    assert paper.paper_id == 'WOS:1'
    assert paper.authors == ['Alice', 'Bob']
    assert paper.published_date.year == 2024
    assert paper.citations == 0
    assert paper.extra['citation_count_available'] is True
    assert paper.abstract == paper.pdf_url == ''
    assert paper.doi == '10.1234/1'


def test_bounded_pagination_keeps_page_size_fixed():
    searcher = WebOfScienceSearcher('fixture-key')
    searcher.session = Mock()
    searcher.session.get.side_effect = [response([hit(i) for i in range(50)], 100),
                                        response([hit(i) for i in range(50, 100)], 100)]
    papers = searcher.search('TI=(test)', max_results=60)
    assert len(papers) == 60
    assert [call.kwargs['params'] for call in searcher.session.get.call_args_list] == [
        {'q': 'TI=(test)', 'db': 'WOS', 'page': 1, 'limit': 50},
        {'q': 'TI=(test)', 'db': 'WOS', 'page': 2, 'limit': 50}]


@pytest.mark.parametrize('payload', [{}, {'hits': {}}, {'hits': [{}]},
                                    {'hits': [], 'metadata': {'total': 5}}])
def test_malformed_response_does_not_look_like_zero_hits(payload):
    searcher = WebOfScienceSearcher('fixture-key')
    searcher.session = Mock()
    searcher.session.get.return_value = Mock(status_code=200, headers={})
    searcher.session.get.return_value.json.return_value = payload
    with pytest.raises(ProviderResponseError):
        searcher.search('TI=(test)')


@pytest.mark.parametrize('limit', [-1, 101, True, 1.5])
def test_limits_rejected_before_request(limit):
    searcher = WebOfScienceSearcher('fixture-key')
    searcher.session = Mock()
    with pytest.raises(ValueError):
        searcher.search('query', limit)
    searcher.session.get.assert_not_called()


@pytest.mark.parametrize('key', ['', 'fixture-key'])
def test_explicit_cli_mcp_parity_and_no_default_quota_use(key, monkeypatch):
    monkeypatch.setenv('PAPER_SEARCH_MCP_WOS_API_KEY', key)
    assert cli._parse_sources('wos') == server._parse_sources('wos') == ['wos']
    assert server._invalid_sources('wos') == []
    assert 'wos' not in server._parse_sources('all')
    for preset in ('all', 'fast', 'fastest'):
        assert 'wos' not in cli._parse_sources(preset)
    assert isinstance(cli._get_searcher('wos'), WebOfScienceSearcher)


def test_aggregate_errors_expose_configuration_failure(capsys):
    result = asyncio.run(server.search_papers('TI=(test)', sources='wos'))
    assert result['sources_used'] == ['wos']
    assert '[credentials_required]' in result['errors']['wos']
    args = cli.build_parser().parse_args(['search', 'TI=(test)', '-s', 'wos'])
    asyncio.run(cli.cmd_search(args))
    result = json.loads(capsys.readouterr().out)
    assert '[credentials_required]' in result['errors']['wos']


def test_only_honest_mcp_capabilities_registered():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    assert 'search_wos' in tools
    assert 'download_wos' not in tools
    assert 'read_wos_paper' not in tools
    assert tools['search_wos'].annotations.readOnlyHint is True


@pytest.mark.parametrize('method', ['download_pdf', 'read_paper'])
def test_metadata_only_raises_without_network(method):
    searcher = WebOfScienceSearcher('fixture-key')
    searcher.session = Mock()
    with pytest.raises(NotImplementedError, match='metadata-only'):
        getattr(searcher, method)('WOS:1')
    searcher.session.get.assert_not_called()


def test_cli_and_mcp_database_option_parity(monkeypatch, capsys):
    searcher = WebOfScienceSearcher('fixture-key')
    searcher.session = Mock()
    searcher.session.get.return_value = response([hit()], 1)
    monkeypatch.setattr(cli, 'SEARCHERS', {'wos': searcher})
    args = cli.build_parser().parse_args(['search', 'TI=(test)', '-s', 'wos', '-n', '4', '--wos-db', 'MEDLINE'])
    asyncio.run(cli.cmd_search(args))
    cli_result = json.loads(capsys.readouterr().out)
    cli_params = searcher.session.get.call_args.kwargs['params']
    monkeypatch.setattr('paper_search_mcp.academic_platforms.wos.WebOfScienceSearcher', lambda: searcher)
    mcp_result = asyncio.run(server.search_wos('TI=(test)', 4, 'MEDLINE'))
    assert cli_result['papers'] == mcp_result
    assert cli_params == searcher.session.get.call_args.kwargs['params']
