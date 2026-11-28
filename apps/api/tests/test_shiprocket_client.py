"""ShiprocketClient: login/token caching, HTTP transport, error
classification, retry/backoff. Uses `httpx.MockTransport` — no network
call, no real Shiprocket account.
"""

from __future__ import annotations

import httpx
import pytest
from app.integrations.retry import RetryPolicy
from app.integrations.shiprocket.client import ShiprocketClient
from app.integrations.shiprocket.config import ShiprocketConfig
from app.integrations.shiprocket.errors import ShiprocketApiError

pytestmark = pytest.mark.asyncio

_CONFIG = ShiprocketConfig(
    email="ops@example.com",
    password="super-secret-password",
    api_base_url="https://apiv2.shiprocket.in/v1/external",
    pickup_location="Main Warehouse",
)
_FAST_RETRY = RetryPolicy(max_retries=2, base_delay_seconds=0.01, max_delay_seconds=0.02)


def _client_with(handler) -> ShiprocketClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return ShiprocketClient(_CONFIG, http_client=http_client, retry_policy=_FAST_RETRY)


# 3. API client / 4. Token handling
async def test_client_logs_in_and_sends_bearer_token() -> None:
    seen = {"login_calls": 0, "auth_header": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            seen["login_calls"] += 1
            return httpx.Response(200, json={"token": "tok_abc123"})
        seen["auth_header"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    data = await client.request("GET", "/orders")

    assert data == {"ok": True}
    assert seen["login_calls"] == 1
    assert seen["auth_header"] == "Bearer tok_abc123"


async def test_relative_path_still_joins_onto_api_base_url() -> None:
    """Every existing Shiprocket integration (orders, shipments, NDR,
    wallet, serviceability) passes a relative path and must keep using
    the shared `/v1/external` base unchanged by the COD remittance fix.
    """
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "tok"})
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    await client.request("GET", "/orders")

    assert seen_urls == ["https://apiv2.shiprocket.in/v1/external/orders"]


async def test_absolute_url_is_used_as_is_not_joined_onto_api_base_url() -> None:
    """Root-cause fix: COD remittance lives directly under `/v1/`, not
    under the shared `/v1/external` base -- passing an absolute
    `http(s)://` URL must bypass `api_base_url` entirely rather than
    being joined onto it (which previously produced the wrong, 404'ing
    `.../v1/external/cod/remittance`).
    """
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "tok"})
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"data": []})

    client = _client_with(handler)
    data = await client.request(
        "GET", "https://apiv2.shiprocket.in/v1/cod/remittance", params={"page": 1, "limit": 1}
    )

    assert data == {"data": []}
    assert seen_urls == ["https://apiv2.shiprocket.in/v1/cod/remittance?page=1&limit=1"]


async def test_client_reuses_cached_token_across_requests() -> None:
    seen = {"login_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            seen["login_calls"] += 1
            return httpx.Response(200, json={"token": "tok_cached"})
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    await client.request("GET", "/orders")
    await client.request("GET", "/orders")
    await client.request("GET", "/orders")

    assert seen["login_calls"] == 1


async def test_client_relogs_in_on_401_and_retries_once() -> None:
    calls = {"login": 0, "orders": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            calls["login"] += 1
            return httpx.Response(200, json={"token": f"tok_{calls['login']}"})
        calls["orders"] += 1
        if calls["orders"] == 1:
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    data = await client.request("GET", "/orders")

    assert data == {"ok": True}
    assert calls["login"] == 2  # initial login + forced re-login after 401
    assert calls["orders"] == 2


async def test_client_login_missing_token_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})  # no "token" key

    client = _client_with(handler)
    with pytest.raises(ShiprocketApiError) as exc_info:
        await client.request("GET", "/orders")
    assert exc_info.value.error_type == "authentication_error"


# 18. Rate limiting / 19. Retry
async def test_client_retries_429_then_succeeds() -> None:
    calls = {"login": 0, "orders": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            calls["login"] += 1
            return httpx.Response(200, json={"token": "tok"})
        calls["orders"] += 1
        if calls["orders"] < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    data = await client.request("GET", "/orders")

    assert data == {"ok": True}
    assert calls["orders"] == 3


async def test_client_gives_up_after_max_retries_exhausted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "tok"})
        return httpx.Response(503, json={})

    client = _client_with(handler)
    with pytest.raises(ShiprocketApiError) as exc_info:
        await client.request("GET", "/orders")
    assert exc_info.value.error_type == "http_503"


async def test_a_500_response_body_is_captured_as_a_safe_shape_never_raw_values() -> None:
    """Regression for the production remittance 500 investigation: the
    raised error must carry enough to diagnose the failure from logs
    alone (key names/types of the Shiprocket error body), while never
    exposing an actual value -- a secret, token, or customer field in
    that body must never surface in `.details`.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "tok"})
        return httpx.Response(
            500,
            json={
                "message": "Internal Server Error: something broke",
                "errors": {"remittance": ["could not be processed"]},
            },
        )

    client = _client_with(handler)
    with pytest.raises(ShiprocketApiError) as exc_info:
        await client.request("GET", "/cod/remittance", params={"limit": 1})

    error = exc_info.value
    assert error.error_type == "http_500"
    assert error.status_code == 500
    shape = error.details["response_shape"]
    assert shape == {"message": "str", "errors": {"remittance": ["list[1]", "str"]}}
    # The real message text and real list contents must never appear.
    assert "something broke" not in str(error.details)
    assert "could not be processed" not in str(error.details)


async def test_non_json_error_body_reports_length_only_not_raw_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "tok"})
        return httpx.Response(500, text="<html>Internal Server Error</html>")

    client = _client_with(handler)
    with pytest.raises(ShiprocketApiError) as exc_info:
        await client.request("GET", "/cod/remittance")

    details = exc_info.value.details
    assert details["response_body_type"] == "non_json"
    assert isinstance(details["response_body_length"], int) and details["response_body_length"] > 0
    assert "Internal Server Error" not in str(details)


# 20. Non-retryable errors
async def test_client_does_not_retry_a_validation_error() -> None:
    calls = {"orders": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "tok"})
        calls["orders"] += 1
        return httpx.Response(422, json={})

    client = _client_with(handler)
    with pytest.raises(ShiprocketApiError) as exc_info:
        await client.request("GET", "/orders")
    assert exc_info.value.error_type == "validation_error"
    assert calls["orders"] == 1
