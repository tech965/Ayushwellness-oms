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
