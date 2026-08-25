"""ShopifyAdapter: authenticate, health_check, pagination, normalize/
process_webhook dispatch. Uses a stub client (duck-typed — only
`.execute()` is called) instead of a real Shopify account.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.exceptions import IntegrationError
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.errors import ShopifyApiError

pytestmark = pytest.mark.asyncio


class _StubClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict | None] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append(variables)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _customer_page(customer_id: str, *, has_more: bool = False, cursor: str | None = None) -> dict:
    return {
        "customers": {
            "pageInfo": {"hasNextPage": has_more, "endCursor": cursor},
            "edges": [{"node": {"id": f"gid://shopify/Customer/{customer_id}"}}],
        }
    }


# 1. Shopify authentication
async def test_authenticate_succeeds_with_valid_response() -> None:
    adapter = ShopifyAdapter(client=_StubClient([{"shop": {"name": "Test Shop"}}]))
    await adapter.authenticate()  # no exception


async def test_authenticate_raises_integration_error_on_failure() -> None:
    adapter = ShopifyAdapter(
        client=_StubClient([ShopifyApiError("bad token", error_type="authentication_error")])
    )
    with pytest.raises(IntegrationError):
        await adapter.authenticate()


# 2. Health check
async def test_health_check_reports_not_configured_with_no_client_or_settings() -> None:
    adapter = ShopifyAdapter()  # no client injected, no env vars set in this test env
    result = await adapter.health_check()
    assert result.connected is False
    assert "not configured" in result.error_message.lower()


async def test_health_check_reports_connected() -> None:
    adapter = ShopifyAdapter(client=_StubClient([{"shop": {"name": "Test Shop"}}]))
    result = await adapter.health_check()
    assert result.connected is True
    assert result.response_time_ms is not None


async def test_health_check_reports_authentication_failure_reason() -> None:
    adapter = ShopifyAdapter(
        client=_StubClient(
            [
                ShopifyApiError(
                    "Shopify rejected the access token.", error_type="authentication_error"
                )
            ]
        )
    )
    result = await adapter.health_check()
    assert result.connected is False
    assert "rejected" in result.error_message.lower()


async def test_health_check_reports_rate_limited_reason() -> None:
    adapter = ShopifyAdapter(
        client=_StubClient([ShopifyApiError("Rate limit exceeded.", error_type="http_429")])
    )
    result = await adapter.health_check()
    assert result.connected is False
    assert "rate limit" in result.error_message.lower()


async def test_health_check_never_raises() -> None:
    adapter = ShopifyAdapter(
        client=_StubClient([ShopifyApiError("Network error.", error_type="network_error")])
    )
    result = await adapter.health_check()  # must not raise
    assert result.connected is False


# 16. Pagination
async def test_fetch_returns_page_with_cursor_and_has_more() -> None:
    client = _StubClient([_customer_page("1", has_more=True, cursor="cursor_1")])
    adapter = ShopifyAdapter(client=client)

    page = await adapter.fetch("customers", cursor=None, limit=50)

    assert page.has_more is True
    assert page.next_cursor == "cursor_1"
    assert len(page.nodes) == 1
    assert client.calls[0] == {"first": 50, "after": None, "query": None}


async def test_fetch_last_page_has_no_next_cursor() -> None:
    client = _StubClient([_customer_page("2", has_more=False, cursor=None)])
    adapter = ShopifyAdapter(client=client)

    page = await adapter.fetch("customers", cursor="prev_cursor", limit=50)

    assert page.has_more is False
    assert page.next_cursor is None
    assert client.calls[0]["after"] == "prev_cursor"


async def test_fetch_incremental_applies_updated_since_filter() -> None:
    client = _StubClient([_customer_page("3")])
    adapter = ShopifyAdapter(client=client)
    since = datetime(2026, 1, 1, tzinfo=UTC)

    await adapter.fetch_incremental("customers", since=since)

    assert "updated_at" in client.calls[0]["query"]
    assert "2026-01-01" in client.calls[0]["query"]


async def test_fetch_unsupported_entity_type_raises() -> None:
    adapter = ShopifyAdapter(client=_StubClient([]))
    with pytest.raises(IntegrationError):
        await adapter.fetch("unsupported_entity")


# normalize / process_webhook dispatch (feeds items 4/7/11 — the mapping
# logic itself is in test_shopify_normalizer.py)
async def test_normalize_dispatches_by_entity_type() -> None:
    adapter = ShopifyAdapter(client=_StubClient([]))
    raw = {"id": "gid://shopify/Customer/42", "firstName": "A", "email": "a@example.com"}
    normalized = adapter.normalize("customers", raw)
    assert normalized["external_id"] == "42"
    assert normalized["email"] == "a@example.com"


async def test_process_webhook_derives_entity_type_from_topic() -> None:
    adapter = ShopifyAdapter(client=_StubClient([]))
    result = await adapter.process_webhook(
        "customers/update", {"id": "gid://shopify/Customer/9", "email": "x@example.com"}
    )
    assert result["entity_type"] == "customers"
    assert result["normalized"]["email"] == "x@example.com"


async def test_process_webhook_unknown_topic_returns_no_handler() -> None:
    adapter = ShopifyAdapter(client=_StubClient([]))
    result = await adapter.process_webhook("inventory_levels/update", {"id": "1"})
    assert result["entity_type"] is None
    assert result["normalized"] is None
