"""Removes everything created by `scripts/qa_seed_volume.py`. Local dev DB only.

Run with: python scripts/qa_seed_volume_cleanup.py
"""

from __future__ import annotations

import asyncio

from app.db.session import AsyncSessionLocal
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from sqlalchemy import delete


async def cleanup() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Payment).where(Payment.source_system == "qa_seed_volume"))
        await session.execute(delete(OrderItem).where(OrderItem.source_system == "qa_seed_volume"))
        result = await session.execute(
            delete(Order).where(Order.source_system == "qa_seed_volume")
        )
        await session.commit()
        print(f"Removed {result.rowcount} qa_seed_volume orders.")


if __name__ == "__main__":
    asyncio.run(cleanup())
