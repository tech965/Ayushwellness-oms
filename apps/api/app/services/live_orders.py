"""Read-only hydration of live Shopify orders into transient `Order` objects.

In API-first mode (`SHOPIFY_LIVE_MODE`) an order's content lives only in
Shopify. Rather than rewrite every service and response builder that
consumes an `Order`, they are handed a *transient* `Order` (and
`OrderItem`/`Customer` children) built from a live fetch plus the
order's `order_workflow` state. These objects are never added to a
session:

* `OrderRepository.update` routes any write on one to `order_workflow`
  (OMS-owned fields only) and refuses everything else;
* a `before_flush` guard raises if one is ever added to a session, so a
  provider record cannot reach PostgreSQL through an unnoticed path.

Nothing here writes the fetched payload anywhere.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError, OMSError, ValidationError
from app.integrations.registry import get_adapter
from app.integrations.shopify.live import ShopifyLiveReader, require_live_reader
from app.models.auth import User
from app.models.customer import Customer
from app.models.enums import CancellationStatus, OrderStatus, ShopifySyncStatus
from app.models.integration import IntegrationCode
from app.models.order import Order, OrderItem
from app.models.order_workflow import OrderWorkflow
from app.services.order_ref import OrderRef, ensure_workflow, resolve_ref

LOCAL_PREFIX = "local:"

# Fields a live order may have written through `OrderRepository.update`;
# each maps 1:1 onto an `OrderWorkflow` column.
WORKFLOW_FIELDS = (
    "status",
    "notes",
    "confirmed_by_telecaller_id",
    "confirmed_at",
    "shopify_confirmation_sync_status",
    "shopify_confirmation_sync_error",
    "shopify_confirmation_synced_at",
)

_NAMESPACE = uuid.UUID("5b0f3a52-7c2e-4a39-9c53-0c5d1e3f8a11")


def get_reader() -> ShopifyLiveReader:
    return require_live_reader(get_adapter(IntegrationCode.SHOPIFY))


def hydrate_order(data: dict[str, Any], workflow: OrderWorkflow | None) -> Order:
    """Build a transient `Order` from a normalized live order dict."""
    external_id = data["external_id"]
    ref_id = workflow.id if workflow is not None else _encode(external_id)
    cancelled = bool(data.get("is_cancelled"))
    created = data.get("external_created_at") or data["order_datetime"]
    updated = data.get("external_updated_at") or created

    address = data.get("shipping_address") or data.get("billing_address") or {}
    customer = Customer(
        id=uuid.uuid5(_NAMESPACE, f"customer:{data.get('customer_external_id') or external_id}"),
        full_name=address.get("contact_name"),
        phone=address.get("contact_phone"),
        email=data.get("customer_email"),
        first_name=None,
        last_name=None,
        is_active=True,
        created_at=created,
        updated_at=updated,
    )

    order = Order(
        id=ref_id,
        order_number=data["order_number"],
        shopify_order_id=external_id,
        customer_id=customer.id,
        order_datetime=data["order_datetime"],
        currency=data["currency"],
        subtotal=data["subtotal"],
        discount_amount=data["discount_amount"],
        tax_amount=data["tax_amount"],
        shipping_charge=data["shipping_charge"],
        total_amount=data["total_amount"],
        payment_type=data["payment_type"],
        payment_status=data["payment_status"],
        status=(
            OrderStatus.CANCELLED
            if cancelled
            else (workflow.status if workflow is not None else OrderStatus.PENDING)
        ),
        fulfillment_status=data["fulfillment_status"],
        cancellation_status=(
            CancellationStatus.CANCELLED if cancelled else CancellationStatus.NONE
        ),
        notes=workflow.notes if workflow is not None else None,
        shopify_tags=data.get("shopify_tags"),
        shopify_order_note=data.get("shopify_order_note"),
        shopify_shipment_status=data.get("shopify_shipment_status"),
        shipping_address=data.get("shipping_address"),
        billing_address=data.get("billing_address"),
        shipping_address_sync_status=ShopifySyncStatus.NOT_APPLICABLE,
        source_system="shopify",
        external_id=external_id,
        confirmed_by_telecaller_id=(workflow.confirmed_by_telecaller_id if workflow else None),
        confirmed_at=workflow.confirmed_at if workflow else None,
        shopify_confirmation_sync_status=(
            workflow.shopify_confirmation_sync_status
            if workflow
            else ShopifySyncStatus.NOT_APPLICABLE
        ),
        shopify_confirmation_sync_error=(
            workflow.shopify_confirmation_sync_error if workflow else None
        ),
        created_at=created,
        updated_at=updated,
    )
    order.customer = customer
    order.items = [
        OrderItem(
            id=uuid.uuid5(_NAMESPACE, f"item:{external_id}:{i}:{item.get('external_id')}"),
            order_id=ref_id,
            product_variant_id=None,
            sku=item["sku"],
            product_name=item["product_name"],
            quantity=item["quantity"],
            unit_price=item["unit_price"],
            discount_amount=item["discount_amount"],
            tax_amount=item["tax_amount"],
            total_amount=item["total_amount"],
            created_at=created,
            updated_at=updated,
        )
        for i, item in enumerate(data["items"])
    ]
    order.shipments = []
    order.payments = []

    for obj in (order, customer, *order.items):
        obj._live_transient = True  # type: ignore[attr-defined]  # noqa: SLF001
    order._live_external_id = external_id  # type: ignore[attr-defined]  # noqa: SLF001
    return order


def _encode(external_id: str) -> uuid.UUID:
    from app.services.order_ref import encode_shopify_order_ref

    return encode_shopify_order_ref(external_id)


async def load_order(session: Any, order_id: uuid.UUID) -> Order | None:
    """Live-mode order lookup.

    Returns a hydrated transient `Order` for a Shopify order, or `None`
    when the id is unknown to both the workflow table and the encoding
    (the caller then falls back to its local lookup). Raises
    `NotFoundError` for a Shopify order that Shopify itself no longer has
    and that has no local historical row to fall back on.
    """
    ref: OrderRef | None = await resolve_ref(session, order_id)
    if ref is None or ref.external_order_id.startswith(LOCAL_PREFIX):
        return None
    live = await get_reader().get_order(ref.external_order_id)
    if live is None:
        return None
    order = hydrate_order(live, ref.workflow)
    if ref.workflow is not None and ref.workflow.confirmed_by_telecaller_id:
        order.confirmed_by_telecaller = await session.get(
            User, ref.workflow.confirmed_by_telecaller_id
        )
    return order


async def apply_live_update(session: Any, order: Order, fields: dict[str, Any]) -> Order:
    """Route a write on a transient order to `order_workflow`."""
    unsupported = [name for name in fields if name not in WORKFLOW_FIELDS]
    if unsupported:
        raise ValidationError(
            "These fields belong to Shopify and are not stored in live-data mode: "
            + ", ".join(sorted(unsupported))
        )
    workflow = await ensure_workflow(session, order._live_external_id)  # type: ignore[attr-defined]  # noqa: SLF001
    for name, value in fields.items():
        setattr(workflow, name, value)
        setattr(order, name, value)
    await session.flush()
    return order


def is_live_transient(obj: object) -> bool:
    return bool(getattr(obj, "_live_transient", False))


@event.listens_for(Session, "before_flush")
def _block_transient_live_objects(session: Session, flush_context, instances) -> None:  # noqa: ANN001
    for obj in list(session.new) + list(session.dirty):
        if is_live_transient(obj):
            raise OMSError("A live Shopify record cannot be persisted (SHOPIFY_LIVE_MODE).")


def live_mode_enabled() -> bool:
    return bool(settings.SHOPIFY_LIVE_MODE)


__all__ = [
    "LOCAL_PREFIX",
    "NotFoundError",
    "apply_live_update",
    "hydrate_order",
    "is_live_transient",
    "live_mode_enabled",
    "load_order",
]
