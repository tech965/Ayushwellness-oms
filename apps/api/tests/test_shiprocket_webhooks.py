"""Shiprocket tracking-webhook endpoint: token verification, idempotent
ingestion via the shared `WebhookService`, matching
(`ShiprocketWebhookService`), status normalization, and error handling.

Payload shapes used here are the commonly-cited, UNVERIFIED aliases
documented in `app.integrations.shiprocket.normalizer`/`webhooks` and
`docs/integrations/shiprocket.md`'s Webhooks section — no live Shiprocket
delivery was available to capture a confirmed shape (see that doc's
"Webhook research" note). Field-name variants are exercised explicitly
(see the "variants" tests) precisely because the real shape isn't
confirmed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.config import settings
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.webhooks import verify_webhook_token
from app.models.enums import IntegrationStatus, IntegrationType, PaymentType, ShipmentStatus
from app.models.integration import Integration, IntegrationCode, WebhookEvent
from app.models.shipment import Shipment, ShipmentEvent
from app.repositories.integration import IntegrationRepository
from app.repositories.shipment import ShipmentRepository
from app.services.order_service import OrderService
from app.services.shipment_service import ShipmentService
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SECRET = "test-shiprocket-webhook-secret"
_URL = "/api/v1/webhooks/shiprocket/tracking"


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHIPROCKET_WEBHOOK_SECRET", _SECRET)
    yield
    clear_adapters()


async def _make_shiprocket_integration(session: AsyncSession) -> Integration:
    integration = await IntegrationRepository(session).create(
        name="Shiprocket",
        code=IntegrationCode.SHIPROCKET,
        type=IntegrationType.COURIER,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


async def _make_order(session: AsyncSession, *, order_number: str):
    return await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=0,
        notes=None,
        items=[],
    )


async def _make_shipment(
    session: AsyncSession,
    *,
    order_number: str,
    shiprocket_shipment_id: str,
    awb: str | None = None,
    current_status: ShipmentStatus = ShipmentStatus.PENDING,
):
    order = await _make_order(session, order_number=order_number)
    shipment, _ = await ShipmentService(session).upsert_synced_shipment(
        source_system="shiprocket",
        external_id=shiprocket_shipment_id,
        order_id=order.id,
        shiprocket_shipment_id=shiprocket_shipment_id,
        awb=awb,
        current_status=current_status,
    )
    return order, shipment


def _headers(token: str | None = _SECRET) -> dict:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Api-Key"] = token
    return headers


# --- Token verification (unit) -----------------------------------------


async def test_verify_webhook_token_accepts_matching_header() -> None:
    assert verify_webhook_token(header_token=_SECRET, body_token=None, secret=_SECRET) is True


async def test_verify_webhook_token_accepts_matching_body_token() -> None:
    assert verify_webhook_token(header_token=None, body_token=_SECRET, secret=_SECRET) is True


async def test_verify_webhook_token_rejects_when_secret_not_configured() -> None:
    assert verify_webhook_token(header_token=_SECRET, body_token=_SECRET, secret="") is False


async def test_verify_webhook_token_rejects_mismatch() -> None:
    assert verify_webhook_token(header_token="wrong", body_token=None, secret=_SECRET) is False


# --- 10. Authentication/security token failure --------------------------


async def test_invalid_token_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_shiprocket_integration(db_session)
    response = await client.post(
        _URL, json={"awb": "AWB1", "current_status": "Delivered"}, headers=_headers("wrong-token")
    )
    assert response.status_code == 401

    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 0


async def test_missing_token_is_rejected(db_session: AsyncSession, client: AsyncClient) -> None:
    await _make_shiprocket_integration(db_session)
    response = await client.post(
        _URL, json={"awb": "AWB1", "current_status": "Delivered"}, headers=_headers(None)
    )
    assert response.status_code == 401


async def test_unconfigured_secret_rejects_every_request(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SHIPROCKET_WEBHOOK_SECRET", None)
    await _make_shiprocket_integration(db_session)
    response = await client.post(
        _URL, json={"awb": "AWB1", "current_status": "Delivered"}, headers=_headers(_SECRET)
    )
    assert response.status_code == 401


async def test_body_token_variant_is_accepted(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Variant transport (spec: tolerant of common/documented variants) —
    the secret arrives as a `token` field inside the JSON body instead of
    the `X-Api-Key` header.
    """
    _, shipment = await _make_shipment(
        db_session, order_number="#BODY-TOKEN-1", shiprocket_shipment_id="1", awb="AWB-BODY-1"
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={"awb": "AWB-BODY-1", "current_status": "Delivered", "token": _SECRET},
        headers=_headers(None),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.DELIVERED


# --- 11. Malformed payload ------------------------------------------------


async def test_malformed_json_payload_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shiprocket_integration(db_session)
    response = await client.post(
        _URL, content=b"{not valid json", headers=_headers(_SECRET)
    )
    assert response.status_code == 400

    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 0


async def test_non_object_json_payload_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shiprocket_integration(db_session)
    response = await client.post(_URL, content=b"[1, 2, 3]", headers=_headers(_SECRET))
    assert response.status_code == 400


# --- 1/2. Valid webhook + AWB matching -----------------------------------


async def test_valid_webhook_updates_shipment_matched_by_awb(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    _, shipment = await _make_shipment(
        db_session,
        order_number="#AWL1",
        shiprocket_shipment_id="1001",
        awb="AWBMATCH1",
        current_status=ShipmentStatus.IN_TRANSIT,
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={
            "awb": "AWBMATCH1",
            "current_status": "Delivered",
            "courier_name": "Delhivery",
            "current_timestamp": "2026-01-10 12:00:00",
        },
        headers=_headers(),
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.DELIVERED

    events = (
        await db_session.execute(
            select(ShipmentEvent).where(ShipmentEvent.shipment_id == shipment.id)
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].courier_name == "Delhivery"
    assert events[0].source == "shiprocket_webhook"

    event_row = await db_session.scalar(select(WebhookEvent))
    assert event_row.status == "processed"


# --- 3. Shiprocket shipment ID matching -----------------------------------


async def test_webhook_matches_by_shiprocket_shipment_id_when_awb_absent(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    _, shipment = await _make_shipment(
        db_session,
        order_number="#AWL2",
        shiprocket_shipment_id="2002",
        awb=None,
        current_status=ShipmentStatus.PENDING,
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={"shipment_id": "2002", "awb": "AWB-NEW-2", "current_status": "In Transit"},
        headers=_headers(),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.IN_TRANSIT
    # AWB newly supplied by the webhook is filled in, never left blank.
    assert refreshed.awb == "AWB-NEW-2"


# --- 4. Channel/order ID matching ------------------------------------------


async def test_webhook_matches_by_channel_order_id_and_creates_shipment_when_none_exists(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    order = await _make_order(db_session, order_number="#AWL-CHANNEL-1")
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={
            "awb": "AWB-CHANNEL-1",
            "shipment_id": "3003",
            "channel_order_id": "AWL-CHANNEL-1",
            "current_status": "Picked Up",
        },
        headers=_headers(),
    )
    assert response.status_code == 200

    shipment = await ShipmentRepository(db_session).get_by_awb("AWB-CHANNEL-1")
    assert shipment is not None
    assert shipment.order_id == order.id
    assert shipment.shiprocket_shipment_id == "3003"


# --- Real production payload shape (captured from live Shiprocket
# webhook deliveries): `order_id` is the channel order number, not
# Shiprocket's internal id -- `sr_order_id` is. No `channel_order_id`
# key is ever actually present. -----------------------------------------


async def test_real_shape_order_id_resolves_as_channel_order_and_creates_shipment(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Reproduces the exact production scenario that was silently
    dropping every real webhook: Order exists, no Shipment exists yet
    (Shiprocket's native Shopify channel created it before OMS pull-sync
    caught up), and the payload has `order_id` + `awb` + `sr_order_id`
    but no `channel_order_id` key at all.
    """
    order = await _make_order(db_session, order_number="#AWL91738")
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={
            "awb": "SF3897621360KR",
            "order_id": "AWL91738",
            "sr_order_id": 1542454019,
            "courier_name": "Shree Maruti",
            "current_status": "In Transit",
        },
        headers=_headers(),
    )
    assert response.status_code == 200

    shipment = await ShipmentRepository(db_session).get_by_awb("SF3897621360KR")
    assert shipment is not None
    assert shipment.order_id == order.id
    assert shipment.current_status == ShipmentStatus.IN_TRANSIT

    event_row = await db_session.scalar(select(WebhookEvent))
    assert event_row.status == "processed"


async def test_real_shape_updates_existing_shipment_matched_by_awb(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    _, shipment = await _make_shipment(
        db_session,
        order_number="#AWL90497",
        shiprocket_shipment_id="1529493721",
        awb="77924691596",
        current_status=ShipmentStatus.IN_TRANSIT,
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={
            "awb": "77924691596",
            "order_id": "AWL90497",
            "sr_order_id": 1529493721,
            "current_status": "Delivered",
        },
        headers=_headers(),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.DELIVERED

    total_shipments = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total_shipments.scalar_one() == 1


async def test_real_shape_unknown_order_remains_ignored_without_fabricating_shipment(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={
            "awb": "UNKNOWN-AWB-1",
            "order_id": "NO-SUCH-ORDER",
            "sr_order_id": 999999999,
            "current_status": "In Transit",
        },
        headers=_headers(),
    )
    assert response.status_code == 200

    total_shipments = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total_shipments.scalar_one() == 0

    event = await db_session.scalar(select(WebhookEvent))
    assert event.status == "ignored"
    assert "no_matching_shipment" in event.error_message


async def test_real_shape_duplicate_delivery_creates_shipment_only_once(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_order(db_session, order_number="#AWL91738")
    await _make_shiprocket_integration(db_session)
    body = {
        "awb": "SF3897621360KR",
        "order_id": "AWL91738",
        "sr_order_id": 1542454019,
        "current_status": "In Transit",
    }

    first = await client.post(_URL, json=body, headers=_headers())
    second = await client.post(_URL, json=body, headers=_headers())

    assert first.status_code == 200
    assert second.status_code == 200

    total_shipments = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total_shipments.scalar_one() == 1

    total_events = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total_events.scalar_one() == 1


# --- Shiprocket order id matching (live orders/show fallback, reused
# from the pull-sync path) --------------------------------------------


async def test_webhook_matches_by_shiprocket_order_id_via_orders_show_fallback(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    class _StubClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def request(self, method, path, *, json=None, params=None):
            self.calls.append(path)
            assert path == "/orders/show/9009"
            return {"data": {"channel_order_id": "#AWL-ORDERID-1"}}

        async def ensure_authenticated(self) -> None:
            pass

    order = await _make_order(db_session, order_number="#AWL-ORDERID-1")
    register_adapter(ShiprocketAdapter(client=_StubClient()))
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={
            "awb": "AWB-ORDERID-1",
            "shipment_id": "9999",
            # Confirmed live: `sr_order_id` (not `order_id`) is Shiprocket's
            # own internal numeric order id -- the only field valid for the
            # live orders/show lookup this test exercises.
            "sr_order_id": "9009",
            "current_status": "Delivered",
        },
        headers=_headers(),
    )
    assert response.status_code == 200

    shipment = await ShipmentRepository(db_session).get_by_awb("AWB-ORDERID-1")
    assert shipment is not None
    assert shipment.order_id == order.id


# --- 5. Unknown shipment must not fabricate a match ------------------------


async def test_unknown_shipment_does_not_fabricate_a_match(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={"awb": "AWB-NOBODY-KNOWS", "current_status": "Delivered"},
        headers=_headers(),
    )
    # Still acked so Shiprocket doesn't hammer the endpoint.
    assert response.status_code == 200

    total_shipments = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total_shipments.scalar_one() == 0

    event = await db_session.scalar(select(WebhookEvent))
    assert event is not None
    assert event.status == "ignored"
    assert "no_matching_shipment" in event.error_message


# --- 6. Duplicate webhook idempotency --------------------------------------


async def test_duplicate_webhook_delivery_is_idempotent(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    _, shipment = await _make_shipment(
        db_session, order_number="#AWL-DUP", shiprocket_shipment_id="4004", awb="AWB-DUP-1"
    )
    await _make_shiprocket_integration(db_session)
    body = {
        "awb": "AWB-DUP-1",
        "current_status": "In Transit",
        "current_timestamp": "2026-02-01 09:00:00",
    }

    first = await client.post(_URL, json=body, headers=_headers())
    second = await client.post(_URL, json=body, headers=_headers())

    assert first.status_code == 200
    assert second.status_code == 200

    total_events = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total_events.scalar_one() == 1

    tracking_events = await db_session.execute(
        select(func.count())
        .select_from(ShipmentEvent)
        .where(ShipmentEvent.shipment_id == shipment.id)
    )
    assert tracking_events.scalar_one() == 1


# --- 7. Missing/null fields must not erase existing data -------------------


async def test_missing_optional_fields_do_not_erase_existing_shipment_data(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    _, shipment = await _make_shipment(
        db_session,
        order_number="#AWL-PRESERVE",
        shiprocket_shipment_id="5005",
        awb="AWB-PRESERVE-1",
        current_status=ShipmentStatus.IN_TRANSIT,
    )
    await _make_shiprocket_integration(db_session)

    # No `awb` field at all in this delivery -- only the shipment id and a
    # status update. The existing AWB must survive untouched.
    response = await client.post(
        _URL,
        json={"shipment_id": "5005", "current_status": "Out for Delivery", "awb": None},
        headers=_headers(),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.awb == "AWB-PRESERVE-1"
    assert refreshed.current_status == ShipmentStatus.OUT_FOR_DELIVERY


# --- 8. Status normalization ------------------------------------------------


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("New", ShipmentStatus.PENDING),
        ("Pickup Scheduled", ShipmentStatus.PENDING),
        ("Picked Up", ShipmentStatus.PICKED_UP),
        ("In Transit", ShipmentStatus.IN_TRANSIT),
        ("Out For Delivery", ShipmentStatus.OUT_FOR_DELIVERY),
        ("Delivered", ShipmentStatus.DELIVERED),
        ("Undelivered", ShipmentStatus.NDR),
        ("Cancelled", ShipmentStatus.CANCELLED),
        ("RTO Initiated", ShipmentStatus.RTO_INITIATED),
        ("RTO Delivered", ShipmentStatus.RTO_DELIVERED),
    ],
)
async def test_status_normalization_maps_raw_shiprocket_status(
    db_session: AsyncSession, client: AsyncClient, raw_status: str, expected: ShipmentStatus
) -> None:
    _, shipment = await _make_shipment(
        db_session,
        order_number=f"#AWL-STATUS-{raw_status}",
        shiprocket_shipment_id=f"st-{raw_status}",
        awb=f"AWB-STATUS-{raw_status}",
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={"awb": f"AWB-STATUS-{raw_status}", "current_status": raw_status},
        headers=_headers(),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == expected


async def test_unmapped_status_is_preserved_as_free_text_without_crashing(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """An unrecognized Shiprocket status string is stored as free text on
    `ShipmentEvent.status` but never guessed onto `Shipment.current_status`
    (spec: never invent a status mapping).
    """
    _, shipment = await _make_shipment(
        db_session,
        order_number="#AWL-UNMAPPED",
        shiprocket_shipment_id="6006",
        awb="AWB-UNMAPPED-1",
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={"awb": "AWB-UNMAPPED-1", "current_status": "Some Brand New Courier Status"},
        headers=_headers(),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.PENDING  # unchanged from setup

    event = await db_session.scalar(
        select(ShipmentEvent).where(ShipmentEvent.shipment_id == shipment.id)
    )
    assert event.status == "Some Brand New Courier Status"


# --- 9. Courier name extraction --------------------------------------------


async def test_courier_name_is_extracted_onto_the_tracking_event(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    _, shipment = await _make_shipment(
        db_session, order_number="#AWL-COURIER", shiprocket_shipment_id="7007", awb="AWB-COURIER-1"
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={
            "awb": "AWB-COURIER-1",
            "current_status": "In Transit",
            "courier": "Bluedart Surface",
        },
        headers=_headers(),
    )
    assert response.status_code == 200

    event = await db_session.scalar(
        select(ShipmentEvent).where(ShipmentEvent.shipment_id == shipment.id)
    )
    assert event.courier_name == "Bluedart Surface"


# --- 12. Database/update failure handling -----------------------------------


async def test_processing_failure_returns_5xx_and_marks_event_failed(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _make_shipment(
        db_session, order_number="#AWL-FAIL", shiprocket_shipment_id="8008", awb="AWB-FAIL-1"
    )
    await _make_shiprocket_integration(db_session)

    from app.services.shiprocket_webhook_service import ShiprocketWebhookService

    async def _boom(self, payload):  # noqa: ANN001, ARG001
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(ShiprocketWebhookService, "apply_tracking_webhook", _boom)

    response = await client.post(
        _URL, json={"awb": "AWB-FAIL-1", "current_status": "Delivered"}, headers=_headers()
    )
    assert response.status_code == 500

    event = await db_session.scalar(select(WebhookEvent))
    assert event is not None
    assert event.status == "failed"
    assert "simulated database failure" in event.error_message


# --- 13. Payload variants ---------------------------------------------------


async def test_awb_code_field_variant_is_accepted(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    _, shipment = await _make_shipment(
        db_session,
        order_number="#AWL-VARIANT-1",
        shiprocket_shipment_id="9101",
        awb="AWB-VARIANT-1",
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={"awb_code": "AWB-VARIANT-1", "status": "Delivered"},
        headers=_headers(),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.DELIVERED


async def test_nested_scan_activities_payload_variant_is_accepted(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Some webhook configurations may send the same
    `shipment_track_activities`/`scans` shape the tracking-poll endpoint
    returns rather than a flat body -- the existing `TRACKING_NORMALIZER`
    handles this unchanged (spec: don't duplicate existing functionality).
    """
    _, shipment = await _make_shipment(
        db_session,
        order_number="#AWL-VARIANT-2",
        shiprocket_shipment_id="9202",
        awb="AWB-VARIANT-2",
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        _URL,
        json={
            "awb": "AWB-VARIANT-2",
            "tracking_data": {
                "shipment_track_activities": [
                    {
                        "id": 1,
                        "status": "Delivered",
                        "date": "2026-02-05 10:00:00",
                        "activity": "Delivered",
                        "location": "Mumbai",
                    }
                ]
            },
        },
        headers=_headers(),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.DELIVERED
    event = await db_session.scalar(
        select(ShipmentEvent).where(ShipmentEvent.shipment_id == shipment.id)
    )
    assert event.location == "Mumbai"


async def test_shiprocket_integration_not_configured_returns_404(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    response = await client.post(
        _URL, json={"awb": "AWB-NONE", "current_status": "Delivered"}, headers=_headers()
    )
    assert response.status_code == 404


# --- Keyword-safe URL alias -------------------------------------------------


async def test_shipment_updates_alias_url_behaves_identically_to_the_shiprocket_path(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Confirmed live: Shiprocket's own "Webhooks" dashboard page rejects a
    URL containing "shiprocket" ("Please refrain from using keywords like
    shiprocket, kartrocket, sr, or kr in the webhook url"), which the
    original `/webhooks/shiprocket/tracking` path violates. `router.py`
    mounts the exact same router a second time under
    `/webhooks/shipment-updates` -- this is the path to actually enter into
    Shiprocket's dashboard. Same handler, so this only needs to prove the
    alias reaches it and behaves identically, not re-prove matching logic
    already covered above.
    """
    _, shipment = await _make_shipment(
        db_session,
        order_number="#AWL-ALIAS-1",
        shiprocket_shipment_id="1234",
        awb="AWB-ALIAS-1",
        current_status=ShipmentStatus.IN_TRANSIT,
    )
    await _make_shiprocket_integration(db_session)

    response = await client.post(
        "/api/v1/webhooks/shipment-updates/tracking",
        json={"awb": "AWB-ALIAS-1", "current_status": "Delivered"},
        headers=_headers(),
    )
    assert response.status_code == 200

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.DELIVERED


async def test_shipment_updates_alias_url_also_rejects_an_invalid_token(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shiprocket_integration(db_session)
    response = await client.post(
        "/api/v1/webhooks/shipment-updates/tracking",
        json={"awb": "AWB1", "current_status": "Delivered"},
        headers=_headers("wrong-token"),
    )
    assert response.status_code == 401
