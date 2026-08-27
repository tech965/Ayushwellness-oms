"""TEMPORARY QA-only local dev seed script — NOT part of the app.

Creates realistic order VOLUME (tens of orders per day, randomized
amounts/times) across the last 10 IST calendar days, so timeseries
bucketing bugs (which only show up with real multi-order-per-day volume,
not a handful of hand-placed fixtures) can be reproduced and the exact
per-day totals cross-checked against a hand-computed ground truth.
Tagged `source_system="qa_seed_volume"` — remove with
`qa_seed_volume_cleanup.py`. Local dev DB only.

Run with: python scripts/qa_seed_volume.py
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.logging import configure_logging, get_logger
from app.db.session import AsyncSessionLocal
from app.models.enums import (
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
    PaymentType,
)
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from sqlalchemy import select

configure_logging()
logger = get_logger(__name__)

IST = timedelta(hours=5, minutes=30)
random.seed(42)


async def seed() -> None:
    now_utc = datetime.now(UTC)
    today_ist_date = (now_utc + IST).date()

    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(Order).where(Order.source_system == "qa_seed_volume")
        )
        if existing:
            print("qa_seed_volume data already present — run qa_seed_volume_cleanup.py first.")
            return

        expected_per_day: dict[str, dict[str, float]] = {}
        counter = 0

        for day_offset in range(9, -1, -1):  # 9 days ago .. today
            ist_day = today_ist_date - timedelta(days=day_offset)
            n_orders = random.randint(15, 60)
            day_total = Decimal("0")

            for _ in range(n_orders):
                counter += 1
                hour = random.randint(0, 23)
                minute = random.randint(0, 59)
                second = random.randint(0, 59)
                # Build the IST wall-clock moment, then convert to the
                # equivalent UTC instant for storage (AwareDateTime columns
                # are UTC) — this is the realistic equivalent of what a
                # real Shopify order timestamp looks like.
                ist_dt = datetime(
                    ist_day.year, ist_day.month, ist_day.day, hour, minute, second, tzinfo=UTC
                )
                order_datetime = ist_dt - IST

                unit_price = Decimal(random.choice(["199.00", "349.00", "499.00", "899.00"]))
                quantity = random.randint(1, 3)
                total = unit_price * quantity
                day_total += total

                order_number = f"QAVOL-{ist_day.isoformat()}-{counter}"
                order = Order(
                    order_number=order_number,
                    source_system="qa_seed_volume",
                    external_id=order_number,
                    order_datetime=order_datetime,
                    currency="INR",
                    subtotal=total,
                    discount_amount=Decimal("0"),
                    tax_amount=Decimal("0"),
                    shipping_charge=Decimal("0"),
                    total_amount=total,
                    payment_type=random.choice([PaymentType.COD, PaymentType.PREPAID]),
                    payment_status=random.choice(
                        [PaymentStatus.PAID, PaymentStatus.PENDING, PaymentStatus.PAID]
                    ),
                    status=OrderStatus.CONFIRMED,
                    fulfillment_status=FulfillmentStatus.UNFULFILLED,
                    created_at=order_datetime,
                    updated_at=order_datetime,
                )
                session.add(order)
                await session.flush()

                session.add(
                    OrderItem(
                        order_id=order.id,
                        sku="QAVOL-SKU",
                        product_name="QA Volume Product",
                        quantity=quantity,
                        unit_price=unit_price,
                        discount_amount=Decimal("0"),
                        tax_amount=Decimal("0"),
                        total_amount=total,
                        source_system="qa_seed_volume",
                        external_id=f"{order_number}-item",
                    )
                )
                session.add(
                    Payment(
                        order_id=order.id,
                        payment_type=order.payment_type,
                        status=order.payment_status,
                        amount=total,
                        currency="INR",
                        provider="qa_seed_volume",
                        source_system="qa_seed_volume",
                        external_id=f"{order_number}-pay",
                    )
                )

            expected_per_day[ist_day.isoformat()] = {
                "order_count": n_orders,
                "revenue": float(day_total),
            }

        await session.commit()

        print(f"Seeded {counter} orders across {len(expected_per_day)} IST days.")
        print("EXPECTED_PER_DAY_JSON:")
        import json

        print(json.dumps(expected_per_day, indent=2))


if __name__ == "__main__":
    asyncio.run(seed())
