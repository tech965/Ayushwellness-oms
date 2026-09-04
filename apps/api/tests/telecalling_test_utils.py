"""Shared helpers for the Team Leader / Telecaller test files.

Builds real `User`/`Role`/`Permission` rows directly (same idiom as
`conftest.py`'s `_create_user_with_permissions`) with full control over
`team_leader_id` linkage and returned `User.id`s, which the RBAC/
assignment/E2E tests need for cross-referencing (e.g. "create these two
telecallers under this team leader, then assert telecaller A cannot see
telecaller B's order").
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.security import create_access_token, hash_password
from app.models.abandoned_checkout import AbandonedCheckout
from app.models.auth import User
from app.models.customer import Customer
from app.models.enums import FulfillmentStatus, OrderStatus, PaymentStatus, PaymentType
from app.models.order import Order
from app.models.rbac import Permission, Role, RolePermission, UserRole
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def make_role(session: AsyncSession, *, name: str, permission_codes: list[str]) -> Role:
    """Idempotent by `name` — a test that sets up several users under the
    same role (e.g. several telecallers) can call this once per user
    without tripping `roles.name`'s unique constraint.
    """
    role = (await session.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        role = Role(name=name, description=f"{name} test role")
        session.add(role)
        await session.flush()

    for code in permission_codes:
        existing = (
            await session.execute(select(Permission).where(Permission.code == code))
        ).scalar_one_or_none()
        if existing is None:
            existing = Permission(code=code, module=code.split(".")[0], action=code.split(".")[-1])
            session.add(existing)
            await session.flush()
        already_linked = (
            await session.execute(
                select(RolePermission).where(
                    RolePermission.role_id == role.id, RolePermission.permission_id == existing.id
                )
            )
        ).scalar_one_or_none()
        if already_linked is None:
            session.add(RolePermission(role_id=role.id, permission_id=existing.id))
    await session.flush()
    return role


async def make_user(
    session: AsyncSession,
    *,
    email: str,
    role: Role | None = None,
    is_superuser: bool = False,
    team_leader_id: uuid.UUID | None = None,
    name: str = "Test User",
) -> User:
    user = User(
        name=name,
        email=email,
        password_hash=hash_password("Test1234!"),
        is_active=True,
        is_superuser=is_superuser,
        team_leader_id=team_leader_id,
    )
    session.add(user)
    await session.flush()
    if role is not None:
        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.flush()
    await session.commit()
    await session.refresh(user)
    return user


def bearer_client(
    app, get_db, db_session: AsyncSession, user_id: uuid.UUID
) -> AsyncClient:  # noqa: ANN001
    """A fresh authenticated `AsyncClient` for `user_id`, sharing
    `db_session` with every other client the test creates (so writes from
    one "user's" client are visible to another in the same test, exactly
    like they'd be in the real single-database app).
    """

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    token = create_access_token(subject=str(user_id))
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport, base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )


async def make_order(
    session: AsyncSession,
    *,
    order_number: str,
    customer: Customer | None = None,
    fulfillment_status: FulfillmentStatus = FulfillmentStatus.UNFULFILLED,
    payment_type: PaymentType = PaymentType.PREPAID,
    total_amount: Decimal = Decimal("999.00"),
    order_datetime: datetime | None = None,
) -> Order:
    order = Order(
        order_number=order_number,
        customer_id=customer.id if customer else None,
        order_datetime=order_datetime or (datetime.now(UTC) - timedelta(days=1)),
        currency="INR",
        subtotal=total_amount,
        total_amount=total_amount,
        payment_type=payment_type,
        payment_status=(
            PaymentStatus.PENDING if payment_type == PaymentType.COD else PaymentStatus.PAID
        ),
        status=OrderStatus.CONFIRMED,
        fulfillment_status=fulfillment_status,
    )
    session.add(order)
    await session.flush()
    return order


async def make_customer(session: AsyncSession, *, phone: str = "9999999999") -> Customer:
    customer = Customer(full_name="Test Customer", phone=phone, email="customer@example.com")
    session.add(customer)
    await session.flush()
    return customer


async def make_abandoned_checkout(
    session: AsyncSession,
    *,
    external_id: str,
    customer_phone: str | None = "8888888888",
    customer_email: str | None = "lead@example.com",
    is_recovered: bool = False,
    total_amount: Decimal = Decimal("499.00"),
    checkout_created_at: datetime | None = None,
) -> AbandonedCheckout:
    checkout = AbandonedCheckout(
        source_system="shopify",
        external_id=external_id,
        shopify_checkout_id=external_id,
        customer_name="Lead Customer",
        customer_phone=customer_phone,
        customer_email=customer_email,
        total_amount=total_amount,
        subtotal_amount=total_amount,
        is_recovered=is_recovered,
        checkout_created_at=checkout_created_at or (datetime.now(UTC) - timedelta(hours=2)),
    )
    session.add(checkout)
    await session.flush()
    return checkout
