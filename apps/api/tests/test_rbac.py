from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_protected_endpoint_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/orders")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"


async def test_authenticated_without_permission_returns_403(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["products.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/orders")
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "authorization_error"


async def test_authenticated_with_permission_succeeds(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["orders.read"]
    ) as auth_client:
        response = await auth_client.get("/api/v1/orders")
        assert response.status_code == 200
        assert response.json()["success"] is True


async def test_superuser_bypasses_permission_checks(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(db_session, is_superuser=True) as auth_client:
        response = await auth_client.get("/api/v1/orders")
        assert response.status_code == 200
