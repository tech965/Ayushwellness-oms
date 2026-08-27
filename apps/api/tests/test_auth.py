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


# Round 6 — production incident: `POST /auth/login` 500'd for every
# syntactically valid request (existing or nonexistent user alike),
# because `UserRepository.get_by_email`'s `select(User)` unconditionally
# selects every mapped column, including `users.team_leader_id` (added by
# migration 3d3a46f414e3), and that migration had not been applied to
# the production database — while the deployed code already expected the
# column to exist. Reproduced exactly against a real Postgres dev DB by
# downgrading one migration and re-running this same request: identical
# `UndefinedColumnError`. Not a code bug — no code change was made; these
# tests lock in the parts that *are* code-testable, since this specific
# failure mode (schema behind code) can't be reproduced in the test
# suite's own DB, which always runs every migration first.
async def test_login_nonexistent_user_rejected_with_401_not_500(client: AsyncClient) -> None:
    """The `user is None or not verify_password(...)` short-circuit in
    `AuthService.login` means a nonexistent email still runs the same
    `get_by_email` query as a real one — this is what actually broke in
    production for every login attempt, not just wrong passwords for
    real users.
    """
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody-at-all@example.com", "password": "whatever123"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"


async def test_login_malformed_request_returns_422(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": "admin@example.com"})
    assert response.status_code == 422

    response = await client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": "x"}
    )
    assert response.status_code == 422


async def test_login_succeeds_for_a_user_with_roles_and_permissions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Exercises the full `get_by_email` eager-load chain
    (`user_roles -> role -> role_permissions -> permission`) that a
    role-less `_seed_user()` never touches — this is the closest a
    pytest test can get to the production shape (real admin users have
    real roles) without being able to reproduce a missing-migration
    schema mismatch inside a test DB that always runs every migration.
    """
    from tests.conftest import _create_user_with_permissions

    await _create_user_with_permissions(
        db_session,
        email="roled-admin@example.com",
        permission_codes=["orders.read", "orders.write"],
        role_name="ops-admin",
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "roled-admin@example.com", "password": "Test1234!"},
    )

    assert response.status_code == 200
    assert "access_token" in response.json()["data"]


async def test_login_succeeds_for_a_user_with_a_team_leader_id_set(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """`team_leader_id` is the exact column that didn't exist in
    production — a Telecaller (non-NULL `team_leader_id`) logging in
    must work exactly the same as an Admin (NULL `team_leader_id`,
    already covered by `_seed_user`-based tests above).
    """
    from tests.conftest import _create_user_with_permissions

    leader = await _create_user_with_permissions(
        db_session, email="leader@example.com", permission_codes=[]
    )
    telecaller = await _create_user_with_permissions(
        db_session,
        email="telecaller@example.com",
        permission_codes=[],
        team_leader_id=leader.id,
    )
    assert telecaller.team_leader_id == leader.id

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "telecaller@example.com", "password": "Test1234!"},
    )

    assert response.status_code == 200
    assert "access_token" in response.json()["data"]


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
