"""Fixtures shared by the chat tool/service/endpoint tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest_asyncio
from app.models.enums import OrderStatus, PaymentType
from app.schemas.order import OrderItemCreateRequest
from app.services.order_service import OrderService
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import _create_user_with_permissions

# Fixed reference instant: 2026-09-02 10:00 IST == 2026-09-02 04:30 UTC.
NOW = datetime(2026, 9, 2, 4, 30, tzinfo=UTC)


async def make_order(
    session: AsyncSession,
    actor,
    *,
    number: str,
    when: datetime,
    payment_type: PaymentType = PaymentType.PREPAID,
    unit_price: str = "649.00",
    product_name: str = "Ashwagandha 60ct",
    sku: str = "AW-ASH-60",
    quantity: int = 1,
    cancel: bool = False,
):
    order = await OrderService(session).create_order(
        actor=actor,
        order_number=number,
        customer_id=None,
        order_datetime=when,
        currency="INR",
        payment_type=payment_type,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(
                sku=sku,
                product_name=product_name,
                quantity=quantity,
                unit_price=Decimal(unit_price),
            )
        ],
    )
    if cancel:
        order = await OrderService(session).transition_status(
            order.id, new_status=OrderStatus.CANCELLED, actor=actor, description="test"
        )
    return order


@pytest_asyncio.fixture
async def chat_user(db_session: AsyncSession):
    """A superuser — every tool is authorized."""
    return await _create_user_with_permissions(
        db_session, email="chat-admin@example.com", permission_codes=[], is_superuser=True
    )


@pytest_asyncio.fixture
async def seeded_orders(db_session: AsyncSession, chat_user):
    """3 orders today (2 COD, 1 prepaid; 1 of the COD ones cancelled),
    2 orders yesterday (both prepaid). Times chosen to sit inside the IST
    day regardless of the UTC offset.
    """
    # "today" = 2026-09-02 IST -> use 11:30 IST (06:00 UTC).
    today = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)
    # "yesterday" = 2026-09-01 IST.
    yesterday = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)

    await make_order(
        db_session,
        chat_user,
        number="T-1",
        when=today,
        payment_type=PaymentType.COD,
        unit_price="1000.00",
    )
    await make_order(
        db_session,
        chat_user,
        number="T-2",
        when=today,
        payment_type=PaymentType.COD,
        unit_price="500.00",
        cancel=True,
    )
    await make_order(
        db_session,
        chat_user,
        number="T-3",
        when=today,
        payment_type=PaymentType.PREPAID,
        unit_price="2000.00",
    )
    await make_order(
        db_session,
        chat_user,
        number="Y-1",
        when=yesterday,
        payment_type=PaymentType.PREPAID,
        unit_price="1500.00",
    )
    await make_order(
        db_session,
        chat_user,
        number="Y-2",
        when=yesterday,
        payment_type=PaymentType.PREPAID,
        unit_price="1500.00",
    )
    return {"today_count": 3, "yesterday_count": 2}
