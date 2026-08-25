from __future__ import annotations

import pytest
from app.core.security import hash_password
from app.models.auth import User
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed_user(
    db_session: AsyncSession, *, email: str = "admin@example.com", password: str = "Sup3rSecret!"
) -> User:
    user = User(
        name="Admin",
        email=email,
        password_hash=hash_password(password),
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_login_success_returns_tokens(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_user(db_session)

    response = await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "Sup3rSecret!"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "access_token" in body["data"]
    assert "refresh_token" in body["data"]


async def test_login_wrong_password_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_user(db_session)

    response = await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"


async def test_login_response_never_contains_password_hash(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_user(db_session)

    response = await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "Sup3rSecret!"}
    )

    assert "password_hash" not in response.text
    assert "password" not in response.json()["data"]


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"


async def test_me_returns_current_user_without_password_hash(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_user(db_session)
    login = await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "Sup3rSecret!"}
    )
    token = login.json()["data"]["access_token"]

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["data"]["email"] == "admin@example.com"
    assert "password_hash" not in response.text


async def test_refresh_then_logout_revokes_refresh_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_user(db_session)
    login = await client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "Sup3rSecret!"}
    )
    refresh_token = login.json()["data"]["refresh_token"]
    access_token = login.json()["data"]["access_token"]

    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    assert "access_token" in refreshed.json()["data"]

    logout = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout.status_code == 200

    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert reused.status_code == 401


async def test_invalid_bearer_token_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401
