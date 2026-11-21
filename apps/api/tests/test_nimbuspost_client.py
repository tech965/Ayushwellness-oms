"""NimbusPostClient: login/token caching, HTTP transport, error
classification, retry/backoff. Uses `httpx.MockTransport` — no network
call, no real NimbusPost account. Mirrors `test_shiprocket_client.py`.
"""

from __future__ import annotations

import httpx
import pytest
from app.integrations.nimbuspost.client import NimbusPostClient
from app.integrations.nimbuspost.config import NimbusPostConfig
from app.integrations.nimbuspost.errors import NimbusPostApiError
from app.integrations.retry import RetryPolicy

pytestmark = pytest.mark.asyncio

_CONFIG = NimbusPostConfig(
    email="ops@example.com",
    password="super-secret-password",
    api_base_url="https://api.nimbuspost.com/v1/",
)
_FAST_RETRY = RetryPolicy(max_retries=2, base_delay_seconds=0.01, max_delay_seconds=0.02)


def _client_with(handler) -> NimbusPostClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return NimbusPostClient(_CONFIG, http_client=http_client, retry_policy=_FAST_RETRY)


async def test_client_logs_in_and_sends_bearer_token() -> None:
    seen = {"login_calls": 0, "auth_header": None, "login_body": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/login"):
            seen["login_calls"] += 1
            seen["login_body"] = request.content
            return httpx.Response(200, json={"status": True, "message": "ok", "data": "tok_abc123"})
        seen["auth_header"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"status": True, "data": []})

    client = _client_with(handler)
    data = await client.request("GET", "orders")

    assert data == {"status": True, "data": []}
    assert seen["login_calls"] == 1
    assert seen["auth_header"] == "Bearer tok_abc123"
    assert b"ops@example.com" in seen["login_body"]
    assert b"super-secret-password" in seen["login_body"]


async def test_client_reuses_cached_token_across_requests() -> None:
    seen = {"login_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/login"):
            seen["login_calls"] += 1
            return httpx.Response(200, json={"status": True, "data": "tok_cached"})
        return httpx.Response(200, json={"status": True})

    client = _client_with(handler)
    await client.request("GET", "orders")
    await client.request("GET", "orders")
    await client.request("GET", "orders")

    assert seen["login_calls"] == 1


async def test_client_relogs_in_on_401_and_retries_once() -> None:
    calls = {"login": 0, "orders": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/login"):
            calls["login"] += 1
            return httpx.Response(200, json={"status": True, "data": f"tok_{calls['login']}"})
        calls["orders"] += 1
        if calls["orders"] == 1:
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"status": True})

    client = _client_with(handler)
    data = await client.request("GET", "orders")

    assert data == {"status": True}
    assert calls["login"] == 2  # initial login + forced re-login after 401
    assert calls["orders"] == 2


async def test_client_login_missing_token_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": True})  # no "data" token

    client = _client_with(handler)
    with pytest.raises(NimbusPostApiError) as exc_info:
        await client.ensure_authenticated()
    assert exc_info.value.error_type == "authentication_error"


async def test_client_login_application_level_failure_raises_authentication_error() -> None:
    """A 200 response can still be an application-level failure (`status:
    false`), distinct from an HTTP error status -- confirmed shape from
    NimbusPost's official SDK source."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": False, "message": "Invalid credentials"})

    client = _client_with(handler)
    with pytest.raises(NimbusPostApiError) as exc_info:
        await client.ensure_authenticated()
    assert exc_info.value.error_type == "authentication_error"
    assert "invalid credentials" in exc_info.value.message.lower()


async def test_client_login_http_401_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status": False, "message": "bad creds"})

    client = _client_with(handler)
    with pytest.raises(NimbusPostApiError) as exc_info:
        await client.ensure_authenticated()
    assert exc_info.value.error_type == "authentication_error"


async def test_client_retries_transient_5xx_then_succeeds() -> None:
    calls = {"orders": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": True, "data": "tok"})
        calls["orders"] += 1
        if calls["orders"] < 2:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"status": True})

    client = _client_with(handler)
    data = await client.request("GET", "orders")
    assert data == {"status": True}
    assert calls["orders"] == 2


async def test_client_gives_up_after_max_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": True, "data": "tok"})
        return httpx.Response(500, json={})

    client = _client_with(handler)
    with pytest.raises(NimbusPostApiError) as exc_info:
        await client.request("GET", "orders")
    assert exc_info.value.error_type.startswith("http_5")


async def test_client_never_retries_validation_error() -> None:
    calls = {"orders": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/login"):
            return httpx.Response(200, json={"status": True, "data": "tok"})
        calls["orders"] += 1
        return httpx.Response(422, json={})

    client = _client_with(handler)
    with pytest.raises(NimbusPostApiError) as exc_info:
        await client.request("POST", "shipments", json={})
    assert exc_info.value.error_type == "validation_error"
    assert calls["orders"] == 1  # no retry attempted
