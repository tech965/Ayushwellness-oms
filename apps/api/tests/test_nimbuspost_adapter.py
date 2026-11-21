"""NimbusPostAdapter: authenticate, health_check, and the not-yet-
supported fetch/normalize interface methods. Uses a stub client (duck-
typed — only `.request()`/`.ensure_authenticated()` are called) instead
of a real NimbusPost account. Mirrors `test_shiprocket_adapter.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.exceptions import IntegrationError
from app.integrations.nimbuspost.adapter import NimbusPostAdapter
from app.integrations.nimbuspost.errors import NimbusPostApiError

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


async def test_authenticate_succeeds() -> None:
    adapter = NimbusPostAdapter(client=_StubClient([{}]))
    await adapter.authenticate()  # no exception


async def test_authenticate_raises_integration_error_on_failure() -> None:
    adapter = NimbusPostAdapter(
        client=_StubClient([NimbusPostApiError("bad creds", error_type="authentication_error")])
    )
    with pytest.raises(IntegrationError):
        await adapter.authenticate()


async def test_health_check_reports_not_configured_with_no_client_or_settings() -> None:
    adapter = NimbusPostAdapter()
    result = await adapter.health_check()
    assert result.connected is False
    assert "not configured" in result.error_message.lower()


async def test_health_check_reports_connected() -> None:
    adapter = NimbusPostAdapter(client=_StubClient([{}]))
    result = await adapter.health_check()
    assert result.connected is True
    assert result.response_time_ms is not None


async def test_health_check_reports_authentication_failure_reason() -> None:
    adapter = NimbusPostAdapter(
        client=_StubClient(
            [
                NimbusPostApiError(
                    "NimbusPost rejected the credentials.", error_type="authentication_error"
                )
            ]
        )
    )
    result = await adapter.health_check()
    assert result.connected is False
    assert "rejected" in result.error_message.lower()


async def test_fetch_not_yet_supported() -> None:
    adapter = NimbusPostAdapter(client=_StubClient([]))
    with pytest.raises(IntegrationError):
        await adapter.fetch("orders")


async def test_fetch_incremental_not_yet_supported() -> None:
    adapter = NimbusPostAdapter(client=_StubClient([]))
    with pytest.raises(IntegrationError):
        await adapter.fetch_incremental("orders", since=datetime(2026, 1, 1, tzinfo=UTC))


async def test_normalize_not_yet_supported() -> None:
    adapter = NimbusPostAdapter(client=_StubClient([]))
    with pytest.raises(IntegrationError):
        adapter.normalize("orders", {})


async def test_process_webhook_returns_empty_result() -> None:
    adapter = NimbusPostAdapter(client=_StubClient([]))
    result = await adapter.process_webhook("any", {})
    assert result == {"entity_type": None, "normalized": None}
