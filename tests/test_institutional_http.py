"""Offline safety/error contracts shared by institutional connectors."""
from unittest.mock import Mock

import pytest
import requests

from paper_search_mcp.academic_platforms.institutional import (
    PermissionDeniedError, ProviderConnectionError, ProviderResponseError,
    ProviderTimeoutError, RateLimitError, RecordNotFoundError, request_json,
)


@pytest.mark.parametrize('status,error', [(401, PermissionDeniedError), (403, PermissionDeniedError),
    (429, RateLimitError), (404, RecordNotFoundError), (500, ProviderResponseError),
    (302, ProviderResponseError)])
def test_http_failure_is_not_empty_and_does_not_echo_credentials(status, error):
    session = Mock()
    response = Mock(status_code=status, headers={'Retry-After': '600'})
    response.json.return_value = {'error': 'secret-key'}
    session.get.return_value = response
    with pytest.raises(error) as failure:
        request_json(session, 'test', 'https://example.test/api', headers={'X-ApiKey': 'secret-key'})
    assert 'secret-key' not in str(failure.value)
    assert failure.value.status_code == status
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs['allow_redirects'] is False
    assert session.get.call_args.kwargs['timeout'] == (5, 10)


@pytest.mark.parametrize('failure,error', [(requests.Timeout('secret-key'), ProviderTimeoutError),
                                         (requests.ConnectionError('secret-key'), ProviderConnectionError)])
def test_network_failure_is_typed_and_redacted(failure, error):
    session = Mock()
    session.get.side_effect = failure
    with pytest.raises(error) as result:
        request_json(session, 'test', 'https://example.test', headers={})
    assert 'secret-key' not in str(result.value)


@pytest.mark.parametrize('payload', [[], {'error': 'secret-key'}, {'service-error': {}}])
def test_malformed_json_or_error_envelope_is_not_an_empty_result(payload):
    session = Mock()
    session.get.return_value = Mock(status_code=200, headers={})
    session.get.return_value.json.return_value = payload
    with pytest.raises(ProviderResponseError):
        request_json(session, 'test', 'https://example.test', headers={})
