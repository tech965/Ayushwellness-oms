from __future__ import annotations

import pytest
from app.repositories.product import ProductRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_product_crud(db_session: AsyncSession, make_authenticated_client) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["products.read", "products.update"]
    ) as auth_client:
        created = await auth_client.post(
            "/api/v1/products", json={"title": "Ashwagandha Capsules", "vendor": "AyushWellness"}
        )
        assert created.status_code == 201
        product_id = created.json()["data"]["id"]

        detail = await auth_client.get(f"/api/v1/products/{product_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["variants"] == []

        updated = await auth_client.patch(
            f"/api/v1/products/{product_id}", json={"status": "archived"}
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["status"] == "archived"


async def test_duplicate_external_product_id_upserts(db_session: AsyncSession) -> None:
    repo = ProductRepository(db_session)

    first, created_first = await repo.upsert_by_external_id(
        source_system="shopify", external_id="prod_1", title="Original Title"
    )
    await db_session.commit()

    second, created_second = await repo.upsert_by_external_id(
        source_system="shopify", external_id="prod_1", title="Renamed Title"
    )
    await db_session.commit()

    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    assert second.title == "Renamed Title"
