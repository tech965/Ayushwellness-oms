"""Shared pytest fixtures.

Uses an in-memory SQLite database via aiosqlite for fast, isolated test
runs — production/staging always use PostgreSQL. `app/db/base.py`'s GUID
and JSONType are cross-dialect specifically so the same models work
against both.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.auth import User
from app.models.rbac import Permission, Role, RolePermission, UserRole
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSessionLocal = async_sessionmaker(bind=_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with _TestSessionLocal() as session:
        yield session

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolate_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never depend on whatever real credentials happen to be
    in the developer's local `.env` (pydantic-settings loads it
    unconditionally, test runs included). Every test starts from a
    credentials-absent baseline; a test that wants "configured" behavior
    already overrides these itself (e.g. `monkeypatch.setattr(settings,
    "SHOPIFY_ACCESS_TOKEN", ...)` in test_shopify_sync.py), which still
    wins since it runs later, in the same test's monkeypatch stack.
    """
    for attr in (
        "SHOPIFY_ACCESS_TOKEN",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
        "SHIPROCKET_EMAIL",
        "SHIPROCKET_PASSWORD",
        "SHIPROCKET_PICKUP_LOCATION",
    ):
        monkeypatch.setattr(settings, attr, None)


async def _create_user_with_permissions(
    session: AsyncSession,
    *,
    email: str,
    permission_codes: list[str],
    is_superuser: bool = False,
    role_name: str | None = None,
    team_leader_id=None,  # noqa: ANN001
) -> User:
    user = User(
        name="Test User",
        email=email,
        password_hash=hash_password("Test1234!"),
        is_active=True,
        is_superuser=is_superuser,
        team_leader_id=team_leader_id,
    )
    session.add(user)
    await session.flush()

    if permission_codes:
        role = Role(name=role_name or f"role-{email}", description="Test role")
        session.add(role)
        await session.flush()

        for code in permission_codes:
            existing = await session.execute(select(Permission).where(Permission.code == code))
            permission = existing.scalar_one_or_none()
            if permission is None:
                permission = Permission(
                    code=code, module=code.split(".")[0], action=code.split(".")[-1]
                )
                session.add(permission)
                await session.flush()
            session.add(RolePermission(role_id=role.id, permission_id=permission.id))

        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.flush()

    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def make_authenticated_client():
    """Factory fixture: `await make_authenticated_client(session, permissions=[...])`
    returns an `AsyncClient` whose bearer token belongs to a freshly
    created user with exactly those permissions.
    """

    async def _factory(
        db_session: AsyncSession,
        *,
        permission_codes: list[str] | None = None,
        is_superuser: bool = False,
        email: str = "user@example.com",
        role_name: str | None = None,
        team_leader_id=None,  # noqa: ANN001
    ) -> AsyncClient:
        from app.core.security import create_access_token

        user = await _create_user_with_permissions(
            db_session,
            email=email,
            permission_codes=permission_codes or [],
            is_superuser=is_superuser,
            role_name=role_name,
            team_leader_id=team_leader_id,
        )
        token = create_access_token(subject=str(user.id))

        async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app.dependency_overrides[get_db] = _override_get_db
        transport = ASGITransport(app=app)
        return AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        )

    return _factory
