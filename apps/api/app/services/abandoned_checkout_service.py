"""Idempotent sync-side upsert for `AbandonedCheckout` — the checkout
counterpart of `OrderService.upsert_synced_order`, called only from
`app.integrations.entity_sync` (never from a telecalling-facing endpoint;
this OMS never creates an abandoned checkout itself, only mirrors what
Shopify reports).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.abandoned_checkout import AbandonedCheckout
from app.repositories.abandoned_checkout import AbandonedCheckoutRepository
from app.repositories.customer import CustomerRepository


class AbandonedCheckoutService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.checkouts = AbandonedCheckoutRepository(session)
        self.customers = CustomerRepository(session)

    async def upsert_synced_checkout(self, **data) -> tuple[AbandonedCheckout, bool]:  # noqa: ANN003
        """Mirrors `OrderService.upsert_synced_order`'s two real fixes this
        codebase already needed for Shopify sync: resolving
        `customer_external_id` to an OMS `customer_id` (never trusting a
        client-supplied one — there is none here, this only ever runs from
        the sync pipeline), and dropping a delivery whose own
        `external_updated_at` is strictly older than what's already
        stored, so an out-of-order retry never overwrites newer data with
        stale data.
        """
        source_system = data.pop("source_system")
        external_id = data.pop("external_id")
        customer_external_id = data.pop("customer_external_id", None)

        customer_id = None
        if customer_external_id:
            customer = await self.customers.get_by_source_external_id(
                source_system=source_system, external_id=customer_external_id
            )
            customer_id = customer.id if customer else None

        existing = await self.checkouts.get_by_source_external_id(
            source_system=source_system, external_id=external_id
        )

        if existing is not None:
            incoming_updated_at = data.get("external_updated_at")
            if (
                incoming_updated_at is not None
                and existing.external_updated_at is not None
                and incoming_updated_at < existing.external_updated_at
            ):
                return existing, False
            checkout = await self.checkouts.update(
                existing, customer_id=customer_id or existing.customer_id, **data
            )
            return checkout, False

        checkout = await self.checkouts.create(
            source_system=source_system,
            external_id=external_id,
            customer_id=customer_id,
            **data,
        )
        return checkout, True
