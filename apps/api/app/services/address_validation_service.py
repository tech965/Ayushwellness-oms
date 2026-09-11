"""Orchestrates Shiprocket-style address-confidence validation for
`Order.shipping_address` -- the only caller of anything under
`app.integrations.address_validation`.

Deliberately NEVER called from a listing/read endpoint (`GET /orders`,
`GET /shipments/queue`) -- those only ever read the already-persisted
`Order.shipping_address_validation_*` columns, zero extra queries, zero
provider calls, so displaying validation status on a 50-row page never
costs more than the existing query already does. Validation itself is
triggered by `OrderService` at the few places `Order.shipping_address`
is actually written (`create_order`, `upsert_synced_order`,
`update_shipping_address`), and by the explicit single-order
`POST /orders/{id}/validate-address` action (for orders that predate
this feature, or whose stored result has gone stale) — never by a
background sweep in this first pass (see the engagement's final report
for why, and what a real external provider would change about that).

`needs_validation`/`is_stale` are hash- and timestamp-based so calling
`validate_order` on every order write is always safe and cheap: it's a
real no-op (no provider call, no DB write) whenever the stored result
already matches the current address and isn't stale.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.address_validation.config import (
    get_address_validation_provider,
    stale_after_days,
)
from app.integrations.address_validation.provider import AddressValidationResult
from app.models.enums import AddressValidationStatus
from app.models.order import Order
from app.repositories.order import OrderRepository

logger = get_logger(__name__)

_PROVIDER_FAILURE_REASON = "Address validation provider raised an unexpected error."


def _hash_address(address: dict | None) -> str | None:
    """A content fingerprint of `address`, stable regardless of key
    insertion order -- comparing this against a freshly computed hash of
    the CURRENT address is how staleness-from-an-address-change is
    detected without ever needing a second copy of the address itself
    (the existing `Order.shipping_address` stays the only source of
    truth for the address; only its hash is duplicated).
    """
    if not address:
        return None
    canonical = json.dumps(address, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AddressValidationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)

    def is_stale(self, order: Order) -> bool:
        if order.shipping_address_validated_at is None:
            return True
        age = datetime.now(UTC) - order.shipping_address_validated_at
        return age > timedelta(days=stale_after_days())

    def needs_validation(self, order: Order) -> bool:
        """`False` whenever there's genuinely nothing to do -- no address
        at all, or a stored result that still matches the current
        address and isn't stale. Never raises; safe to call on any
        `Order`, including ones that have never been validated.
        """
        if not order.shipping_address:
            return False
        if order.shipping_address_validation_status is None:
            return True
        if _hash_address(order.shipping_address) != order.shipping_address_validation_hash:
            return True
        return self.is_stale(order)

    async def _clear_validation_if_no_address(self, order: Order) -> bool:
        """Handles the address-removed case: if a previously-validated
        order's `shipping_address` has since been cleared to `None`, the
        stale validation result must not linger and be shown as if it
        still describes the (now-absent) address. Returns whether it
        cleared anything, so the caller can skip the redundant commit.
        """
        if order.shipping_address or order.shipping_address_validation_status is None:
            return False
        await self.orders.update(
            order,
            shipping_address_validation_status=None,
            shipping_address_validation_score=None,
            shipping_address_validation_reason=None,
            shipping_address_validated_at=None,
            shipping_address_validation_provider_ref=None,
            shipping_address_validation_hash=None,
        )
        return True

    async def validate_order(self, order: Order, *, force: bool = False) -> Order:
        """(Re)validates `order.shipping_address` if needed (or
        unconditionally with `force=True`, used by the explicit
        "Validate Address" action) and persists the result. A safe
        no-op — no provider call, no write — when the stored result
        already matches the current address and isn't stale.
        """
        if await self._clear_validation_if_no_address(order):
            await self.session.commit()
            return order
        if not order.shipping_address:
            return order
        if not force and not self.needs_validation(order):
            return order

        provider = get_address_validation_provider()
        try:
            result = await provider.validate(order.shipping_address)
        except Exception:  # noqa: BLE001 - a provider must never break the caller's own write
            logger.warning(
                "address_validation_provider_raised", order_id=str(order.id), exc_info=True
            )
            result = AddressValidationResult(
                status=AddressValidationStatus.UNKNOWN, score=0, reason=_PROVIDER_FAILURE_REASON
            )

        await self._persist(order, result)
        await self.session.commit()
        return order

    async def validate_orders(self, orders: list[Order], *, force: bool = False) -> list[Order]:
        """Bulk entry point for a future backfill/sweep — calls the
        provider's `validate_batch` ONCE for every order that actually
        needs (re)validation, never once per order, so a real
        batching-capable provider gets a single round trip for the whole
        set. Orders that don't need it are returned untouched. NOT
        called from any request handler in this first pass (see this
        module's docstring) — exposed for a future background job.
        """
        for order in orders:
            if await self._clear_validation_if_no_address(order):
                await self.session.commit()

        to_validate = [
            o for o in orders if o.shipping_address and (force or self.needs_validation(o))
        ]
        if not to_validate:
            return orders

        provider = get_address_validation_provider()
        addresses = [o.shipping_address for o in to_validate if o.shipping_address]
        try:
            results = await provider.validate_batch(addresses)
        except Exception:  # noqa: BLE001
            logger.warning(
                "address_validation_provider_raised_batch",
                order_count=len(to_validate),
                exc_info=True,
            )
            results = [
                AddressValidationResult(
                    status=AddressValidationStatus.UNKNOWN, score=0, reason=_PROVIDER_FAILURE_REASON
                )
                for _ in to_validate
            ]

        for order, result in zip(to_validate, results, strict=True):
            await self._persist(order, result)
        await self.session.commit()
        return orders

    async def _persist(self, order: Order, result: AddressValidationResult) -> None:
        await self.orders.update(
            order,
            shipping_address_validation_status=result.status,
            shipping_address_validation_score=result.score,
            shipping_address_validation_reason=result.reason,
            shipping_address_validated_at=datetime.now(UTC),
            shipping_address_validation_provider_ref=result.provider_ref,
            shipping_address_validation_hash=_hash_address(order.shipping_address),
        )
