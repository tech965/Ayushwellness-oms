"""ShopifyClient: HTTP transport, error classification, retry/backoff.

Uses `httpx.MockTransport` so requests go through real `httpx` logic
(status handling, raised exceptions) without any network call or real
Shopify account.
"""

from __future__ import annotations

import httpx
import pytest
from app.integrations.retry import RetryPolicy
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.config import ShopifyConfig
from app.integrations.shopify.errors import ShopifyApiError

pytestmark = pytest.mark.asyncio

_CONFIG = ShopifyConfig(
    shop_domain="test-shop.myshopify.com", access_token="tok_123", api_version="2026-01"
)
_FAST_RETRY = RetryPolicy(max_retries=2, base_delay_seconds=0.01, max_delay_seconds=0.02)


def _client_with(handler) -> ShopifyClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return ShopifyClient(_CONFIG, http_client=http_client, retry_policy=_FAST_RETRY)


# 3. API client
async def test_client_sends_access_token_header_and_returns_data() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("X-Shopify-Access-Token")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": {"shop": {"name": "Test Shop"}}})

    client = _client_with(handler)
    data = await client.execute("query { shop { name } }")

    assert data["shop"]["name"] == "Test Shop"
    assert seen["token"] == "tok_123"
    assert "2026-01" in seen["url"]


async def test_client_raises_shopify_api_error_never_a_raw_httpx_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    client = _client_with(handler)
    with pytest.raises(ShopifyApiError):
        await client.execute("query {}")


async def test_client_classifies_401_as_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    client = _client_with(handler)
    with pytest.raises(ShopifyApiError) as exc_info:
        await client.execute("query {}")
    assert exc_info.value.error_type == "authentication_error"


async def test_client_classifies_403_as_authorization_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    client = _client_with(handler)
    with pytest.raises(ShopifyApiError) as exc_info:
        await client.execute("query {}")
    assert exc_info.value.error_type == "authorization_error"


async def test_client_classifies_graphql_throttled_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]},
        )

    client = _client_with(handler)
    with pytest.raises(ShopifyApiError) as exc_info:
        await client.execute("query {}")
    assert exc_info.value.error_type == "http_429"


# 17. Rate-limit handling / 18. Retry behavior
async def test_client_retries_429_then_succeeds() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"data": {"ok": True}})

    client = _client_with(handler)
    data = await client.execute("query {}")

    assert data == {"ok": True}
    assert calls["count"] == 3


async def test_client_gives_up_after_max_retries_exhausted() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, json={})

    client = _client_with(handler)
    with pytest.raises(ShopifyApiError) as exc_info:
        await client.execute("query {}")

    assert exc_info.value.error_type == "http_503"
    assert calls["count"] == _FAST_RETRY.max_retries + 1


async def test_client_does_not_retry_a_non_retryable_error() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(401, json={})

    client = _client_with(handler)
    with pytest.raises(ShopifyApiError):
        await client.execute("query {}")

    assert calls["count"] == 1
