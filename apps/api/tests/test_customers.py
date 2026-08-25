from __future__ import annotations

import pytest
from app.repositories.customer import CustomerRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_customer_crud(db_session: AsyncSession, make_authenticated_client) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["customers.read", "customers.update"]
    ) as auth_client:
        create = await auth_client.post(
            "/api/v1/customers",
            json={
                "first_name": "Asha",
                "last_name": "Rao",
                "email": "asha@example.com",
                "phone": "9990001111",
            },
        )
        assert create.status_code == 201
        customer_id = create.json()["data"]["id"]
        assert create.json()["data"]["full_name"] == "Asha Rao"

        get_response = await auth_client.get(f"/api/v1/customers/{customer_id}")
        assert get_response.status_code == 200
        assert get_response.json()["data"]["email"] == "asha@example.com"

        update = await auth_client.patch(f"/api/v1/customers/{customer_id}", json={"notes": "VIP"})
        assert update.status_code == 200
        assert update.json()["data"]["notes"] == "VIP"

        listing = await auth_client.get("/api/v1/customers", params={"q": "Asha"})
        assert listing.status_code == 200
        assert listing.json()["meta"]["total_items"] == 1


async def test_duplicate_external_customer_id_upserts_instead_of_duplicating(
    db_session: AsyncSession,
) -> None:
    """Spec §49 case 1: replaying the same external customer twice must
    update the existing row, not create a second one.
    """
    repo = CustomerRepository(db_session)

    first, created_first = await repo.upsert_by_external_id(
        source_system="shopify", external_id="cust_123", email="a@example.com", full_name="A"
    )
    await db_session.commit()
    assert created_first is True

    second, created_second = await repo.upsert_by_external_id(
        source_system="shopify",
        external_id="cust_123",
        email="a@example.com",
        full_name="A Updated",
    )
    await db_session.commit()

    assert created_second is False
    assert second.id == first.id
    assert second.full_name == "A Updated"

    from app.models.customer import Customer
    from sqlalchemy import func, select

    total = await db_session.scalar(
        select(func.count()).select_from(
            select(Customer)
            .where(Customer.source_system == "shopify", Customer.external_id == "cust_123")
            .subquery()
        )
    )
    assert total == 1
