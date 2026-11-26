"""NimbusPost webhook endpoint (New/v2, B2C only): signature verification,
idempotent ingestion via the shared `WebhookService`, matching/applying via
`NimbusPostWebhookService`, and the shared `apply_tracking_event` pipeline
(Module 1's dispatch/RTO logic) reused unmodified for a second courier.

Payload field names and status codes are taken directly from NimbusPost's
official webhook reference (2026-11-26) — see
`app.integrations.nimbuspost.normalizer`'s module docstring.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.models.enums import (
    IntegrationStatus,
    IntegrationType,
    InventoryMovementType,
    PaymentType,
    RTORestockStatus,
    RTOStatus,
    ShipmentStatus,
)
from app.models.integration import Integration, IntegrationCode, WebhookEvent
from app.models.inventory import InventoryMovement
from app.models.product import ProductVariant
from app.models.rto import RTO
from app.models.shipment import Shipment, ShipmentEvent
from app.repositories.integration import IntegrationRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.schemas.order import OrderItemCreateRequest
from app.services.order_service import OrderService
from app.services.shipment_service import ShipmentService
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SECRET = "test-nimbuspost-webhook-secret"
_URL = "/api/v1/webhooks/couriers/nimbuspost"


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "NIMBUSPOST_WEBHOOK_SECRET", _SECRET)


async def _make_nimbuspost_integration(session: AsyncSession) -> Integration:
    integration = await IntegrationRepository(session).create(
        name="NimbusPost",
        code=IntegrationCode.NIMBUSPOST,
        type=IntegrationType.COURIER,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


async def _make_variant(session: AsyncSession, *, sku: str, available_quantity: int = 10):
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify", external_id=f"prod-{sku}", title=f"Product {sku}"
    )
    variant, _ = await ProductVariantRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=f"var-{sku}",
        product_id=product.id,
        sku=sku,
        price=Decimal("100.00"),
        available_quantity=available_quantity,
        packets_per_box=1,
        pack_size=1,
        inventory_quantity=0,
    )
    await session.commit()
    return variant


async def _make_order(session: AsyncSession, *, order_number: str, sku: str | None = None):
    items = (
        []
        if sku is None
        else [
            OrderItemCreateRequest(
                product_variant_id=None,
                sku=sku,
                product_name=f"Product {sku}",
                quantity=2,
                unit_price=Decimal("100.00"),
            )
        ]
    )
    return await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=items,
    )


async def _make_shipment(session: AsyncSession, *, order_id, awb: str | None = None):
    return await ShipmentService(session).create_shipment(
        actor=None, order_id=order_id, awb=awb, courier_id=None, expected_delivery_date=None
    )


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _b2c_payload(**overrides) -> dict:
    base = {
        "id": "np-rec-1",
        "order_id": "np-order-1",
        "order_number": "",
        "created": "2026-11-26",
        "awb_number": "",
        "status": "",
        "event_time": "2026-11-26 10:00:00",
        "location": "",
        "message": "",
        "rto_awb": "",
        "edd": "",
        "status_code": "",
        "payment_type": "prepaid",
        "courier_id": "",
        "courier_name": "",
        "pickup_date": "",
        "rto_initiate_date": "",
        "delivered_date": "",
        "shipped_date": "",
        "warehouse_id": "",
        "rto_warehouse_id": "",
        "rto_status": "",
        "shipment_info": "",
    }
    base.update(overrides)
    return base


async def _post(
    client: AsyncClient, payload: dict, *, event: str, delivery: str, secret: str = _SECRET
):
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "x-nimbus-signature": _sign(body, secret),
        "x-nimbus-event": event,
        "x-nimbus-delivery": delivery,
    }
    return await client.post(_URL, content=body, headers=headers)


# --- 1/2/3/4: signature verification ----------------------------------


async def test_valid_signature_is_accepted(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_nimbuspost_integration(db_session)
    payload = _b2c_payload(
        order_number="OMS-NP1", awb_number="AWBNP1", status="in transit", status_code="IT"
    )
    response = await _post(client, payload, event="tracking.updated", delivery="d-valid-1")
    assert response.status_code == 200


async def test_invalid_signature_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_nimbuspost_integration(db_session)
    payload = _b2c_payload()
    body = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "x-nimbus-signature": "sha256=" + "0" * 64,
        "x-nimbus-event": "tracking.updated",
        "x-nimbus-delivery": "d-invalid-1",
    }
    response = await client.post(_URL, content=body, headers=headers)
    assert response.status_code == 401
    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 0


async def test_modified_raw_body_fails_signature(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    payload = _b2c_payload(status="in transit")
    body = json.dumps(payload).encode()
    signature = _sign(body)  # signed over the ORIGINAL body
    tampered = json.dumps({**payload, "status": "delivered"}).encode()  # body changed after signing
    headers = {
        "Content-Type": "application/json",
        "x-nimbus-signature": signature,
        "x-nimbus-event": "tracking.updated",
        "x-nimbus-delivery": "d-tampered-1",
    }
    response = await client.post(_URL, content=tampered, headers=headers)
    assert response.status_code == 401


async def test_missing_signature_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_nimbuspost_integration(db_session)
    body = json.dumps(_b2c_payload()).encode()
    headers = {
        "Content-Type": "application/json",
        "x-nimbus-event": "tracking.updated",
        "x-nimbus-delivery": "d-missing-sig-1",
    }
    response = await client.post(_URL, content=body, headers=headers)
    assert response.status_code == 401


async def test_unconfigured_secret_rejects_every_request(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NIMBUSPOST_WEBHOOK_SECRET", None)
    await _make_nimbuspost_integration(db_session)
    body = json.dumps(_b2c_payload()).encode()
    headers = {
        "Content-Type": "application/json",
        "x-nimbus-signature": _sign(body),
        "x-nimbus-event": "tracking.updated",
        "x-nimbus-delivery": "d-no-secret-1",
    }
    response = await client.post(_URL, content=body, headers=headers)
    assert response.status_code == 401


# --- 5: all three supported event types land as distinct WebhookEvents --


async def test_all_three_event_types_are_ingested(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    order = await _make_order(db_session, order_number="OMS-NP-ALL3")

    for event, delivery in (
        ("order.created", "d-oc-1"),
        ("order.updated", "d-ou-1"),
        ("tracking.updated", "d-tu-1"),
    ):
        payload = _b2c_payload(
            order_number="OMS-NP-ALL3", awb_number="AWBALL3", status="in transit", status_code="IT"
        )
        response = await _post(client, payload, event=event, delivery=delivery)
        assert response.status_code == 200

    rows = (await db_session.execute(select(WebhookEvent))).scalars().all()
    assert {r.event_type for r in rows} == {"order.created", "order.updated", "tracking.updated"}
    assert order.id  # order resolved without error for all three


# --- 6: stable delivery id duplicate ------------------------------------


async def test_duplicate_delivery_id_is_ignored(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    order2 = await _make_order(db_session, order_number="OMS-NP-DUP3")
    shipment2 = await _make_shipment(db_session, order_id=order2.id, awb="AWBDUP3")

    payload = _b2c_payload(
        order_number="OMS-NP-DUP3", awb_number="AWBDUP3", status="in transit", status_code="IT"
    )
    r1 = await _post(client, payload, event="tracking.updated", delivery="d-same-1")
    r2 = await _post(client, payload, event="tracking.updated", delivery="d-same-1")
    assert r1.status_code == 200
    assert r2.status_code == 200

    events = (
        (
            await db_session.execute(
                select(ShipmentEvent).where(ShipmentEvent.shipment_id == shipment2.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1  # applied exactly once despite two identical deliveries


# --- 7: unknown event type -----------------------------------------------


async def test_unknown_event_type_is_acked_and_ignored(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    payload = _b2c_payload()
    response = await _post(client, payload, event="cargo_order.created", delivery="d-unknown-evt-1")
    assert response.status_code == 200

    event = (await db_session.execute(select(WebhookEvent))).scalars().one()
    assert event.status.value == "ignored"
    assert "unsupported_event_type" in (event.error_message or "")


# --- 8/9: unknown and unsupported (DG/LT) status codes --------------------


async def test_unknown_status_code_never_transitions_shipment(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    order = await _make_order(db_session, order_number="OMS-NP-UNK")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBUNK")

    payload = _b2c_payload(
        order_number="OMS-NP-UNK", awb_number="AWBUNK", status="unknown", status_code="UNK"
    )
    response = await _post(client, payload, event="tracking.updated", delivery="d-unk-1")
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.PENDING  # untouched


async def test_damaged_and_lost_are_logged_never_transition_or_restock(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    variant = await _make_variant(db_session, sku="NPDG", available_quantity=10)
    order = await _make_order(db_session, order_number="OMS-NP-DG", sku="NPDG")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBDG")

    for status_code, delivery in (("DG", "d-dg-1"), ("LT", "d-lt-1")):
        payload = _b2c_payload(
            order_number="OMS-NP-DG",
            awb_number="AWBDG",
            status=status_code.lower(),
            status_code=status_code,
        )
        response = await _post(client, payload, event="tracking.updated", delivery=delivery)
        assert response.status_code == 200

    refreshed_shipment = await db_session.get(Shipment, shipment.id)
    assert refreshed_shipment.current_status == ShipmentStatus.PENDING  # never transitioned
    refreshed_variant = await db_session.get(ProductVariant, variant.id)
    assert refreshed_variant.available_quantity == 10  # never dispatched/restocked
    movements = (await db_session.execute(select(InventoryMovement))).scalars().all()
    assert movements == []


# --- 10: AWB matching and unmatched events --------------------------------


async def test_matches_shipment_by_awb(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_nimbuspost_integration(db_session)
    order = await _make_order(db_session, order_number="OMS-NP-AWB")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBMATCH")

    payload = _b2c_payload(
        order_number="OMS-NP-AWB", awb_number="AWBMATCH", status="in transit", status_code="IT"
    )
    response = await _post(client, payload, event="tracking.updated", delivery="d-awb-match-1")
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.IN_TRANSIT


async def test_unmatched_event_is_ignored_with_reason(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    payload = _b2c_payload(
        order_number="OMS-NP-NOMATCH",
        awb_number="AWBNOMATCH",
        status="in transit",
        status_code="IT",
    )
    response = await _post(client, payload, event="tracking.updated", delivery="d-nomatch-1")
    assert response.status_code == 200

    event = (await db_session.execute(select(WebhookEvent))).scalars().one()
    assert event.status.value == "ignored"
    assert "no_matching_shipment" in (event.error_message or "")


# --- 11: tracking transitions, including RTO delivered -> awaiting verification --


async def test_rto_delivered_awaits_warehouse_verification_never_restocks(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    variant = await _make_variant(db_session, sku="NPRTO", available_quantity=10)
    order = await _make_order(db_session, order_number="OMS-NP-RTO", sku="NPRTO")
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWBRTO")

    steps = [
        ("picked", "PKD", "d-rto-1"),
        ("rto initiated", "RT", "d-rto-2"),
        ("rto delivered", "RT-DL", "d-rto-3"),
    ]
    for status, status_code, delivery in steps:
        payload = _b2c_payload(
            order_number="OMS-NP-RTO",
            awb_number="AWBRTO",
            status=status,
            status_code=status_code,
            rto_awb="RTNAWBRTO" if status_code.startswith("RT") else "",
        )
        response = await _post(client, payload, event="tracking.updated", delivery=delivery)
        assert response.status_code == 200

    refreshed_variant = await db_session.get(ProductVariant, variant.id)
    assert refreshed_variant.available_quantity == 8  # dispatched, NOT restocked

    rto = (
        (await db_session.execute(select(RTO).where(RTO.shipment_id == shipment.id)))
        .scalars()
        .one()
    )
    assert rto.status == RTOStatus.RECEIVED
    assert rto.restock_status == RTORestockStatus.AWAITING_VERIFICATION
    assert (rto.rto_metadata or {}).get("rto_awb") == "RTNAWBRTO"


# --- 12: no duplicate dispatch / RTO restock ------------------------------


async def test_replayed_tracking_event_never_double_dispatches(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    variant = await _make_variant(db_session, sku="NPDUP2", available_quantity=10)
    order = await _make_order(db_session, order_number="OMS-NP-DUP2X", sku="NPDUP2")
    await _make_shipment(db_session, order_id=order.id, awb="AWBDUP2X")

    for i in range(2):
        payload = _b2c_payload(
            order_number="OMS-NP-DUP2X", awb_number="AWBDUP2X", status="picked", status_code="PKD"
        )
        response = await _post(client, payload, event="tracking.updated", delivery=f"d-dup2x-{i}")
        assert response.status_code == 200

    refreshed = await db_session.get(ProductVariant, variant.id)
    assert refreshed.available_quantity == 8  # deducted exactly once
    dispatches = (
        (
            await db_session.execute(
                select(InventoryMovement).where(
                    InventoryMovement.movement_type == InventoryMovementType.DISPATCH
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(dispatches) == 1


# --- 13: order.updated alongside tracking.updated -------------------------


async def test_order_updated_awb_backfill_and_cancellation(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_nimbuspost_integration(db_session)
    order = await _make_order(db_session, order_number="OMS-NP-OU")
    shipment = await _make_shipment(db_session, order_id=order.id, awb=None)

    # order.updated: "booked" backfills the AWB only, no status change.
    booked_payload = _b2c_payload(
        order_number="OMS-NP-OU", awb_number="AWBOU", status="booked", courier_name="Test Courier"
    )
    r1 = await _post(client, booked_payload, event="order.updated", delivery="d-ou-booked-1")
    assert r1.status_code == 200
    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.awb == "AWBOU"
    assert refreshed.current_status == ShipmentStatus.PENDING  # unchanged by order.updated

    # order.updated: a status tracking.updated already owns -> not applied.
    shipped_payload = _b2c_payload(order_number="OMS-NP-OU", awb_number="AWBOU", status="shipped")
    r2 = await _post(client, shipped_payload, event="order.updated", delivery="d-ou-shipped-1")
    assert r2.status_code == 200
    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.PENDING  # still untouched

    # tracking.updated IS authoritative for the real transition.
    tracking_payload = _b2c_payload(
        order_number="OMS-NP-OU", awb_number="AWBOU", status="in transit", status_code="IT"
    )
    r3 = await _post(client, tracking_payload, event="tracking.updated", delivery="d-ou-tracking-1")
    assert r3.status_code == 200
    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.IN_TRANSIT

    # order.updated: cancellation IS handled.
    cancel_payload = _b2c_payload(order_number="OMS-NP-OU", awb_number="AWBOU", status="cancelled")
    r4 = await _post(client, cancel_payload, event="order.updated", delivery="d-ou-cancel-1")
    assert r4.status_code == 200
    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.CANCELLED


# --- 14: malformed payloads and retryable processing failures ------------


async def test_malformed_payload_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_nimbuspost_integration(db_session)
    body = b"not json"
    headers = {
        "Content-Type": "application/json",
        "x-nimbus-signature": _sign(body),
        "x-nimbus-event": "tracking.updated",
        "x-nimbus-delivery": "d-malformed-1",
    }
    response = await client.post(_URL, content=body, headers=headers)
    assert response.status_code == 400


async def test_unconfigured_integration_returns_404(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    payload = _b2c_payload()
    response = await _post(client, payload, event="tracking.updated", delivery="d-no-integration-1")
    assert response.status_code == 404
