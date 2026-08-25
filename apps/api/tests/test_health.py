"""Health endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_health_liveness(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "service" in body


async def test_health_readiness_reports_dependency_checks(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code in (200, 503)
    body = response.json()
    assert set(body["checks"].keys()) == {"database", "redis"}
