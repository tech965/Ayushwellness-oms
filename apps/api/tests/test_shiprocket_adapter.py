"""ShiprocketAdapter: authenticate, health_check, NDR pagination,
normalize dispatch, and the no-webhook-contract decision. Uses a stub
client (duck-typed — only `.request()` is called) instead of a real
Shiprocket account.
"""

from __future__ import annotations

import pytest
from app.core.exceptions import IntegrationError
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.errors import ShiprocketApiError

pytestmark = pytest.mark.asyncio


class _StubClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict:
        self.calls.append((method, path, params))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def ensure_authenticated(self) -> None:
        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, Exception):
            raise response


def _ndr_page(*, ids: list[str], total_pages: int, page: int) -> dict:
    return {
        "data": [
            {"id": i, "awb": f"AWB{i}", "order_id": f"ord_{i}", "reason": "Customer unavailable"}
            for i in ids
        ],
        "meta": {"pagination": {"total_pages": total_pages}},
    }


# 1. Shiprocket authentication
async def test_authenticate_succeeds() -> None:
    adapter = ShiprocketAdapter(client=_StubClient([{}]))
    await adapter.authenticate()  # no exception


async def test_authenticate_raises_integration_error_on_failure() -> None:
    adapter = ShiprocketAdapter(
        client=_StubClient([ShiprocketApiError("bad creds", error_type="authentication_error")])
    )
    with pytest.raises(IntegrationError):
        await adapter.authenticate()


# 2. Health check
async def test_health_check_reports_not_configured_with_no_client_or_settings() -> None:
    adapter = ShiprocketAdapter()
    result = await adapter.health_check()
    assert result.connected is False
    assert "not configured" in result.error_message.lower()


async def test_health_check_reports_connected() -> None:
    adapter = ShiprocketAdapter(client=_StubClient([{}]))
    result = await adapter.health_check()
    assert result.connected is True
    assert result.response_time_ms is not None


async def test_health_check_reports_authentication_failure_reason() -> None:
    adapter = ShiprocketAdapter(
        client=_StubClient(
            [
                ShiprocketApiError(
                    "Shiprocket rejected the credentials.", error_type="authentication_error"
                )
            ]
        )
    )
    result = await adapter.health_check()
    assert result.connected is False
    assert "rejected" in result.error_message.lower()


async def test_health_check_never_raises() -> None:
    adapter = ShiprocketAdapter(
        client=_StubClient([ShiprocketApiError("Network error.", error_type="network_error")])
    )
    result = await adapter.health_check()
    assert result.connected is False


# 17. Pagination
async def test_fetch_ndr_paginates() -> None:
    client = _StubClient([_ndr_page(ids=["1", "2"], total_pages=2, page=1)])
    adapter = ShiprocketAdapter(client=client)

    page = await adapter.fetch("ndr", cursor=None, limit=50)

    assert page.has_more is True
    assert page.next_cursor == "2"
    assert len(page.nodes) == 2
    assert client.calls[0] == ("GET", "/ndr/all", {"page": 1, "per_page": 50})


async def test_fetch_ndr_last_page_has_no_next_cursor() -> None:
    client = _StubClient([_ndr_page(ids=["3"], total_pages=1, page=1)])
    adapter = ShiprocketAdapter(client=client)

    page = await adapter.fetch("ndr", cursor="1", limit=50)

    assert page.has_more is False
    assert page.next_cursor is None


async def test_fetch_unsupported_entity_type_raises() -> None:
    adapter = ShiprocketAdapter(client=_StubClient([]))
    with pytest.raises(IntegrationError):
        await adapter.fetch("customers")


async def test_fetch_incremental_degrades_to_full_pull() -> None:
    from datetime import UTC, datetime

    client = _StubClient([_ndr_page(ids=["1"], total_pages=1, page=1)])
    adapter = ShiprocketAdapter(client=client)
    page = await adapter.fetch_incremental("ndr", since=datetime(2026, 1, 1, tzinfo=UTC))
    assert len(page.nodes) == 1


def _shipments_page(*, ids: list[str], total_pages: int) -> dict:
    return {
        "data": [
            {"id": i, "channel_order_id": f"AWL{i}", "awb": f"AWB{i}", "status": "In Transit"}
            for i in ids
        ],
        "meta": {"pagination": {"total_pages": total_pages}},
    }


async def test_fetch_shipments_paginates() -> None:
    client = _StubClient([_shipments_page(ids=["1", "2"], total_pages=2)])
    adapter = ShiprocketAdapter(client=client)

    page = await adapter.fetch("shipments", cursor=None, limit=50)

    assert page.has_more is True
    assert page.next_cursor == "2"
    assert len(page.nodes) == 2
    assert client.calls[0] == ("GET", "/shipments", {"page": 1, "per_page": 50})


async def test_fetch_shipments_last_page_has_no_next_cursor() -> None:
    client = _StubClient([_shipments_page(ids=["3"], total_pages=1)])
    adapter = ShiprocketAdapter(client=client)

    page = await adapter.fetch("shipments", cursor="1", limit=50)

    assert page.has_more is False
    assert page.next_cursor is None


# normalize dispatch
async def test_normalize_dispatches_ndr() -> None:
    adapter = ShiprocketAdapter(client=_StubClient([]))
    raw = {"id": "1", "awb": "AWB1", "order_id": "ord_1", "reason": "Door locked"}
    normalized = adapter.normalize("ndr", raw)
    assert normalized["awb"] == "AWB1"


async def test_normalize_dispatches_shipments() -> None:
    adapter = ShiprocketAdapter(client=_StubClient([]))
    raw = {"id": "1", "channel_order_id": "AWL1", "awb": "AWB1", "status": "In Transit"}
    normalized = adapter.normalize("shipments", raw)
    assert normalized["awb"] == "AWB1"
    assert normalized["channel_order_id"] == "AWL1"


async def test_normalize_unsupported_entity_type_raises() -> None:
    adapter = ShiprocketAdapter(client=_StubClient([]))
    with pytest.raises(IntegrationError):
        adapter.normalize("orders", {})


# 23. Webhook/callback verification — no contract implemented (spec §19/§20:
# do not invent one). `process_webhook` exists only to satisfy the
# interface and always returns a safe no-op.
async def test_process_webhook_is_a_documented_no_op() -> None:
    adapter = ShiprocketAdapter(client=_StubClient([]))
    result = await adapter.process_webhook("tracking-updated", {"awb": "AWB1"})
    assert result == {"entity_type": None, "normalized": None}
