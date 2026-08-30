"""CashfreeClient: base URL, required headers, create/get order, get
payment(s), error classification, retry/backoff, timeout handling. Uses
`httpx.MockTransport` — no network call, no real Cashfree account.
"""

from __future__ import annotations

import httpx
import pytest
from app.integrations.cashfree.client import CashfreeClient
from app.integrations.cashfree.config import CashfreeConfig
from app.integrations.cashfree.errors import CashfreeApiError
from app.integrations.retry import RetryPolicy

pytestmark = pytest.mark.asyncio

_CONFIG = CashfreeConfig(
    client_id="test-client-id",
    client_secret="test-client-secret",
    api_version="2025-01-01",
    base_url="https://sandbox.cashfree.com/pg",
    webhook_secret=None,
    return_url=None,
)
_FAST_RETRY = RetryPolicy(max_retries=2, base_delay_seconds=0.01, max_delay_seconds=0.02)


def _client_with(handler, *, config: CashfreeConfig = _CONFIG) -> CashfreeClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return CashfreeClient(config, http_client=http_client, retry_policy=_FAST_RETRY)


# A. correct base URL / headers
async def test_request_hits_configured_base_url_and_path() -> None:
    seen = {"url": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    await client.request("GET", "/orders/abc")

    assert seen["url"] == "https://sandbox.cashfree.com/pg/orders/abc"


async def test_request_sends_required_auth_and_version_headers() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["x-client-id"] = request.headers.get("x-client-id")
        seen["x-client-secret"] = request.headers.get("x-client-secret")
        seen["x-api-version"] = request.headers.get("x-api-version")
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    await client.request("GET", "/orders/abc")

    assert seen == {
        "x-client-id": "test-client-id",
        "x-client-secret": "test-client-secret",
        "x-api-version": "2025-01-01",
    }


async def test_production_base_url_is_used_when_configured() -> None:
    prod_config = CashfreeConfig(
        client_id="prod-id",
        client_secret="prod-secret",
        api_version="2025-01-01",
        base_url="https://api.cashfree.com/pg",
        webhook_secret=None,
        return_url=None,
    )
    seen = {"url": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler, config=prod_config)
    await client.request("GET", "/orders/abc")

    assert seen["url"].startswith("https://api.cashfree.com/pg")


# create order
async def test_create_order_posts_to_orders_and_returns_session_info() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/pg/orders"
        return httpx.Response(
            200,
            json={
                "cf_order_id": "cf_123",
                "order_id": "AWL92268",
                "order_status": "ACTIVE",
                "payment_session_id": "session_abc",
            },
        )

    client = _client_with(handler)
    result = await client.create_order(
        {
            "order_id": "AWL92268",
            "order_amount": 500.0,
            "order_currency": "INR",
            "customer_details": {"customer_id": "c1", "customer_phone": "9999999999"},
        }
    )

    assert result["payment_session_id"] == "session_abc"
    assert result["cf_order_id"] == "cf_123"


# get order
async def test_get_order_hits_correct_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/pg/orders/AWL92268"
        return httpx.Response(200, json={"order_id": "AWL92268", "order_status": "PAID"})

    client = _client_with(handler)
    result = await client.get_order("AWL92268")

    assert result["order_status"] == "PAID"


# get payments for order / get payment
async def test_get_payments_for_order_returns_a_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/pg/orders/AWL92268/payments"
        return httpx.Response(
            200,
            json=[
                {"cf_payment_id": "pay_1", "payment_status": "FAILED"},
                {"cf_payment_id": "pay_2", "payment_status": "SUCCESS"},
            ],
        )

    client = _client_with(handler)
    result = await client.get_payments_for_order("AWL92268")

    assert len(result) == 2
    assert result[1]["cf_payment_id"] == "pay_2"


async def test_get_payments_for_order_tolerates_a_non_list_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": "no payments yet"})

    client = _client_with(handler)
    result = await client.get_payments_for_order("AWL92268")

    assert result == []


async def test_get_payment_by_id_hits_correct_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/pg/orders/AWL92268/payments/pay_2"
        return httpx.Response(200, json={"cf_payment_id": "pay_2", "payment_status": "SUCCESS"})

    client = _client_with(handler)
    result = await client.get_payment("AWL92268", "pay_2")

    assert result["payment_status"] == "SUCCESS"


# API failure handling
async def test_create_order_raises_on_authentication_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid credentials"})

    client = _client_with(handler)
    with pytest.raises(CashfreeApiError) as exc_info:
        await client.create_order({"order_id": "X"})
    assert exc_info.value.error_type == "authentication_error"


async def test_get_order_raises_not_found_for_unknown_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "order not found"})

    client = _client_with(handler)
    with pytest.raises(CashfreeApiError) as exc_info:
        await client.get_order("does-not-exist")
    assert exc_info.value.error_type == "not_found"


async def test_create_order_raises_validation_error_on_400() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "order_amount is required"})

    client = _client_with(handler)
    with pytest.raises(CashfreeApiError) as exc_info:
        await client.create_order({})
    assert exc_info.value.error_type == "validation_error"


async def test_error_response_message_is_included_in_error_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "order_id already exists"})

    client = _client_with(handler)
    with pytest.raises(CashfreeApiError) as exc_info:
        await client.create_order({"order_id": "DUP"})
    assert "order_id already exists" in exc_info.value.message
    assert exc_info.value.error_type == "conflict"


# retry / backoff
async def test_client_retries_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    data = await client.request("GET", "/orders/abc")

    assert data == {"ok": True}
    assert calls["n"] == 3


async def test_client_gives_up_after_max_retries_exhausted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    client = _client_with(handler)
    with pytest.raises(CashfreeApiError) as exc_info:
        await client.request("GET", "/orders/abc")
    assert exc_info.value.error_type == "http_503"


async def test_client_does_not_retry_a_validation_error() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(422, json={})

    client = _client_with(handler)
    with pytest.raises(CashfreeApiError):
        await client.request("GET", "/orders/abc")
    assert calls["n"] == 1


# timeout handling
async def test_client_classifies_timeout_and_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, json={"ok": True})

    client = _client_with(handler)
    data = await client.request("GET", "/orders/abc")

    assert data == {"ok": True}
    assert calls["n"] == 2


async def test_client_raises_timeout_error_after_retries_exhausted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    client = _client_with(handler)
    with pytest.raises(CashfreeApiError) as exc_info:
        await client.request("GET", "/orders/abc")
    assert exc_info.value.error_type == "timeout"


async def test_config_from_settings_returns_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", None)
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", None)
    assert CashfreeConfig.from_settings() is None


async def test_config_from_settings_defaults_to_sandbox_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", "id")
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", "secret")
    config = CashfreeConfig.from_settings()
    assert config is not None
    assert config.base_url == "https://sandbox.cashfree.com/pg"
