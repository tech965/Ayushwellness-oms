"""Removes everything created by `scripts/qa_seed.py` (source_system ==
"qa_seed"). Local dev DB only.

Run with: python scripts/qa_seed_cleanup.py
"""

from __future__ import annotations

import asyncio

from app.db.session import AsyncSessionLocal
from app.models.customer import Customer
from app.models.ndr import NDR
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.rto import RTO
from app.models.shipment import Shipment
from sqlalchemy import delete


async def cleanup() -> None:
    async with AsyncSessionLocal() as session:
        for model in (NDR, RTO, Payment, OrderItem, Shipment):
            await session.execute(delete(model).where(model.source_system == "qa_seed"))
        result = await session.execute(delete(Order).where(Order.source_system == "qa_seed"))
        await session.execute(delete(Customer).where(Customer.source_system == "qa_seed"))
        await session.commit()
        print(f"Removed {result.rowcount} QA-seeded orders and related records.")


if __name__ == "__main__":
    asyncio.run(cleanup())
