"""Tests for the TEMPORARY diagnostic endpoint
`app.api.v1.endpoints.shiprocket_diagnostics` -- see that module's
docstring for what it is and why it exists (the #AWL95498 "AWB assigned
but absent from Shiprocket's Ready to Ship page" investigation).

No real Shiprocket account; the stub client returns hand-built REST
response shapes, matching the pattern used across every other Shiprocket
test file in this suite.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.errors import ShiprocketApiError
from app.main import app
from app.models.enums import OrderStatus, ShipmentStatus
from app.models.shipment import Shipment

from tests.telecalling_test_utils import (
    bearer_client,
    make_customer,
    make_order,
    make_role,
    make_user,
)

pytestmark = pytest.mark.asyncio


class _StubClient:
    """Mirrors the stub client used across every other Shiprocket test
    file -- hand-queued responses, no real HTTP.
    """

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict:
        self.calls.append((method, path, json))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def ensure_authenticated(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    yield
    clear_adapters()


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


def _tracking_response(*, status: str = "IN TRANSIT") -> dict:
    return {
        "tracking_data": {
            "track_status": 1,
            "shipment_status": status,
            "shipment_track_activities": [
                {
                    "date": "2026-09-01 10:00:00",
                    "status": status,
                    "activity": f"Shipment {status.lower()}",
                    "location": "Mumbai Hub",
                }
            ],
        }
    }


def _shipments_page(*, records: list[dict], total_pages: int = 1) -> dict:
    return {"data": records, "meta": {"pagination": {"total_pages": total_pages}}}


async def _admin_user(db_session):
    return await make_user(db_session, email="admin@diagnostics.example.com", is_superuser=True)


async def _non_admin_user(db_session):
    role = await make_role(db_session, name="OPS_DIAG", permission_codes=["shipments.read"])
    return await make_user(db_session, email="ops@diagnostics.example.com", role=role)


async def _order_with_shipment(
    db_session,
    *,
    order_number: str,
    shiprocket_shipment_id: str,
    shiprocket_order_id: str,
    awb: str | None,
):
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number=order_number, customer=customer, status=OrderStatus.CONFIRMED
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        source_system="shiprocket",
        external_id=shiprocket_shipment_id,
        shiprocket_shipment_id=shiprocket_shipment_id,
        raw_external_payload={
            "order_id": shiprocket_order_id,
            "shipment_id": shiprocket_shipment_id,
        },
        awb=awb,
    )
    db_session.add(shipment)
    await db_session.commit()
    await db_session.refresh(order)
    await db_session.refresh(shipment)
    return order, shipment


async def test_requires_superuser_not_just_a_regular_permission(db_session) -> None:
    user = await _non_admin_user(db_session)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status", params={"order_number": "AWL-DIAG-1"}
        )
        assert response.status_code == 403


async def test_requires_exactly_one_of_order_number_or_shipment_id(db_session) -> None:
    admin = await _admin_user(db_session)

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        neither = await client.get("/api/v1/diagnostics/shiprocket/shipment-status")
        assert neither.status_code == 422

        both = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status",
            params={
                "order_number": "AWL-DIAG-1",
                "shipment_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert both.status_code == 422


async def test_unknown_order_number_returns_404(db_session) -> None:
    admin = await _admin_user(db_session)
    register_adapter(ShiprocketAdapter(client=_StubClient([])))

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status", params={"order_number": "AWL-NOPE"}
        )
        assert response.status_code == 404


async def test_order_without_a_shipment_row_returns_404(db_session) -> None:
    admin = await _admin_user(db_session)
    customer = await make_customer(db_session)
    await make_order(
        db_session, order_number="AWL-NOSHIP-1", customer=customer, status=OrderStatus.CONFIRMED
    )
    register_adapter(ShiprocketAdapter(client=_StubClient([])))

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status",
            params={"order_number": "AWL-NOSHIP-1"},
        )
        assert response.status_code == 404


async def test_reports_a_clear_per_call_error_when_shiprocket_credentials_are_missing(
    db_session,
) -> None:
    """No `register_adapter()` call here -- `get_adapter()` self-heals by
    auto-registering a real (uninjected) `ShiprocketAdapter` the first
    time it's asked for, exactly the real production state when
    `SHIPROCKET_EMAIL`/`SHIPROCKET_PASSWORD` are unset: every live call
    still gets *attempted* (`called` is `True`), each one just fails with
    the adapter's own "not configured" `IntegrationError` -- never a
    silent skip, never a guess.
    """
    admin = await _admin_user(db_session)
    await _order_with_shipment(
        db_session,
        order_number="AWL-DIAG-NC",
        shiprocket_shipment_id="9001",
        shiprocket_order_id="1900009001",
        awb="AWBDIAGNC",
    )
    # No register_adapter() call -- exactly the real production state
    # when SHIPROCKET_EMAIL/PICKUP_LOCATION are unset.

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status", params={"order_number": "AWL-DIAG-NC"}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["tracking"]["called"] is True
        assert "not configured" in data["tracking"]["error"].lower()
        assert data["shipments_scan"]["called"] is True
        assert "not configured" in data["shipments_scan"]["error"].lower()
        # Neither read succeeded -- correctly NOT_FOUND, never a guessed
        # bucket (see `_classify`'s docstring).
        assert data["classification"] == "NOT_FOUND"
        assert data["classification_basis"].startswith("Neither")


async def test_leading_hash_on_order_number_resolves_the_same_order(db_session) -> None:
    admin = await _admin_user(db_session)
    await _order_with_shipment(
        db_session,
        order_number="#AWL-DIAG-HASH",
        shiprocket_shipment_id="9002",
        shiprocket_order_id="1900009002",
        awb="AWBDIAGHASH",
    )
    register_adapter(
        ShiprocketAdapter(
            client=_StubClient(
                [
                    _tracking_response(status="New"),
                    _shipments_page(records=[]),
                    ShiprocketApiError("not found", error_type="not_found"),
                ]
            )
        )
    )

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        # Called WITHOUT the leading '#' -- must still resolve.
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status",
            params={"order_number": "AWL-DIAG-HASH"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["oms"]["order_number"] == "#AWL-DIAG-HASH"


async def test_reports_full_identifiers_and_in_transit_classification(db_session) -> None:
    admin = await _admin_user(db_session)
    order, shipment = await _order_with_shipment(
        db_session,
        order_number="AWL95498",
        shiprocket_shipment_id="7001",
        shiprocket_order_id="1900007001",
        awb="14112365406136",
    )
    client_stub = _StubClient(
        [
            _tracking_response(status="IN TRANSIT"),
            _shipments_page(
                records=[
                    {
                        "id": 7001,
                        "status": "IN TRANSIT",
                        "courier_name": "Delhivery",
                        "awb": "14112365406136",
                    }
                ]
            ),
            {"data": {"status": "IN TRANSIT", "channel_order_id": "AWL95498"}},
        ]
    )
    register_adapter(ShiprocketAdapter(client=client_stub))

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status", params={"order_number": "AWL95498"}
        )
        assert response.status_code == 200
        data = response.json()["data"]

        oms = data["oms"]
        assert oms["order_number"] == "AWL95498"
        assert oms["shipment_id"] == str(shipment.id)
        assert oms["shiprocket_shipment_id"] == "7001"
        assert oms["shiprocket_order_id"] == "1900007001"
        assert oms["awb"] == "14112365406136"

        assert data["tracking"]["called"] is True
        assert data["tracking"]["raw_status_text"] == "IN TRANSIT"

        assert data["shipments_scan"]["called"] is True
        assert data["shipments_scan"]["found"] is True
        assert data["shipments_scan"]["raw_courier_name"] == "Delhivery"

        assert data["order_show"]["called"] is True
        assert data["order_show"]["found"] is True

        assert data["classification"] == "IN_TRANSIT"

    # Never calls order-creation, ever, regardless of outcome.
    assert all(path != "/orders/create/adhoc" for _method, path, _json in client_stub.calls)
    # Never calls assign/awb -- purely read-only.
    assert all(path != "/courier/assign/awb" for _method, path, _json in client_stub.calls)
    # The exact endpoint the investigation asked for.
    assert ("GET", "/courier/track/awb/14112365406136", None) in client_stub.calls


@pytest.mark.parametrize(
    ("raw_status", "expected_bucket"),
    [
        ("Delivered", "DELIVERED"),
        ("RTO Initiated", "RTO"),
        ("Cancelled", "CANCELLED"),
        ("Pickup Scheduled", "PICKUP_SCHEDULED"),
        ("Manifest Generated", "MANIFESTED"),
        ("New", "READY_TO_SHIP"),
    ],
)
async def test_classification_buckets_match_the_raw_tracking_status(
    db_session, raw_status: str, expected_bucket: str
) -> None:
    admin = await _admin_user(db_session)
    await _order_with_shipment(
        db_session,
        order_number=f"AWL-DIAG-{expected_bucket}",
        shiprocket_shipment_id=f"70{hash(expected_bucket) % 100:02d}",
        shiprocket_order_id="1900000000",
        awb=f"AWB-{expected_bucket}",
    )
    register_adapter(
        ShiprocketAdapter(
            client=_StubClient(
                [
                    _tracking_response(status=raw_status),
                    _shipments_page(records=[]),
                    ShiprocketApiError("not found", error_type="not_found"),
                ]
            )
        )
    )

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status",
            params={"order_number": f"AWL-DIAG-{expected_bucket}"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["classification"] == expected_bucket


async def test_shipment_id_lookup_works_without_an_order_number(db_session) -> None:
    admin = await _admin_user(db_session)
    _order, shipment = await _order_with_shipment(
        db_session,
        order_number="AWL-DIAG-BYID",
        shiprocket_shipment_id="7099",
        shiprocket_order_id="1900007099",
        awb="AWBBYID",
    )
    register_adapter(
        ShiprocketAdapter(
            client=_StubClient(
                [
                    _tracking_response(status="DELIVERED"),
                    _shipments_page(records=[]),
                    ShiprocketApiError("not found", error_type="not_found"),
                ]
            )
        )
    )

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status",
            params={"shipment_id": str(shipment.id)},
        )
        assert response.status_code == 200
        assert response.json()["data"]["oms"]["order_number"] == "AWL-DIAG-BYID"


async def test_no_awb_on_file_never_calls_tracking_and_reports_it_plainly(db_session) -> None:
    admin = await _admin_user(db_session)
    await _order_with_shipment(
        db_session,
        order_number="AWL-DIAG-NOAWB",
        shiprocket_shipment_id="7100",
        shiprocket_order_id="1900007100",
        awb=None,
    )
    register_adapter(
        ShiprocketAdapter(
            client=_StubClient(
                [
                    _shipments_page(records=[]),
                    ShiprocketApiError("not found", error_type="not_found"),
                ]
            )
        )
    )

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        response = await client.get(
            "/api/v1/diagnostics/shiprocket/shipment-status",
            params={"order_number": "AWL-DIAG-NOAWB"},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["tracking"]["called"] is False
        assert "no awb" in data["tracking"]["error"].lower()
