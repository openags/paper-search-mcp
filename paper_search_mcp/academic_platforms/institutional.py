"""Bounded, credential-safe HTTP support for explicit institutional connectors."""
from __future__ import annotations

from typing import Any

import requests


class InstitutionalAPIError(RuntimeError):
    """Stable error code preserved in CLI/MCP aggregate error strings."""

    code = "provider_error"

    def __init__(self, provider: str, message: str, *, status_code: int | None = None,
                 retry_after: str = "") -> None:
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"[{self.code}] {provider}: {message}")


class CredentialsRequiredError(InstitutionalAPIError):
    code = "credentials_required"


class PermissionDeniedError(InstitutionalAPIError):
    code = "permission_denied"


class RateLimitError(InstitutionalAPIError):
    code = "rate_limited"


class ProviderTimeoutError(InstitutionalAPIError):
    code = "timeout"


class ProviderConnectionError(InstitutionalAPIError):
    code = "connection_error"


class ProviderResponseError(InstitutionalAPIError):
    code = "invalid_response"


class RecordNotFoundError(InstitutionalAPIError):
    code = "not_found"


class IdentityMismatchError(InstitutionalAPIError):
    code = "identity_mismatch"


def request(session: requests.Session, provider: str, url: str, *, headers: dict,
            params: dict | None = None) -> requests.Response:
    """One bounded request; never follow redirects or retry against quota limits.

    Do not include URLs, request/response bodies, or exception messages in errors:
    upstream servers and requests exceptions can echo credentials or query data.
    """
    try:
        response = session.get(url, headers=headers, params=params,
                               timeout=(5, 10), allow_redirects=False)
    except requests.Timeout as exc:
        raise ProviderTimeoutError(provider, "Request timed out.") from exc
    except requests.RequestException as exc:
        raise ProviderConnectionError(provider, "Request failed.") from exc
    status = response.status_code
    if status in (401, 403):
        raise PermissionDeniedError(
            provider, "API key or institutional entitlement was rejected; check the key, "
            "API plan, and any institutional network requirements.", status_code=status)
    if status == 429:
        raise RateLimitError(provider, "Rate or quota limit reached; no automatic retry.",
                             status_code=status,
                             retry_after=response.headers.get("Retry-After", ""))
    if status == 404:
        raise RecordNotFoundError(provider, "Requested record was not found.", status_code=status)
    if not 200 <= status < 300:
        raise ProviderResponseError(provider, f"Unexpected HTTP status {status}.", status_code=status)
    return response


def request_json(session: requests.Session, provider: str, url: str, *, headers: dict,
                 params: dict | None = None) -> dict[str, Any]:
    response = request(session, provider, url, headers=headers, params=params)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderResponseError(provider, "Response is not JSON.") from exc
    if not isinstance(payload, dict):
        raise ProviderResponseError(provider, "Response must be a JSON object.")
    if any(key in payload for key in ("error", "service-error", "error-response")):
        raise ProviderResponseError(provider, "API returned an error envelope.")
    return payload


def result_limit(max_results: int) -> int:
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ValueError("max_results must be an integer from 0 to 100")
    if not 0 <= max_results <= 100:
        raise ValueError("max_results must be from 0 to 100; split larger searches explicitly")
    return max_results


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("displayName", "name", "value", "$", "content", "text"):
            if value.get(key):
                return text(value[key])
    return ""
