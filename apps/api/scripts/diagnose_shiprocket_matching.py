"""TEMPORARY diagnostic script — NOT part of the app, no code changes.

Prints, for one Shopify/OMS order number, everything `entity_sync.
_upsert_shipment`/`NDRService.upsert_synced_ndr` actually compare when
trying to match a Shiprocket shipment/NDR back to it: the OMS `Order`
row, any OMS `Shipment` row(s) already linked to it, the most recent
cached `SyncError` for any Shiprocket shipment (so an "already confirmed
unmatched" skip can be read verbatim), and -- if a `shiprocket_order_id`
is supplied -- the LIVE `GET /orders/show/{id}` response Shiprocket
itself returns, via the exact same `ShiprocketAdapter.get_order` call
`_upsert_shipment` makes. Everything here is read-only.

Run in a Render Shell (API or worker service -- both have DB + Shiprocket
credentials):
    python scripts/diagnose_shiprocket_matching.py "#AWL93382"
    python scripts/diagnose_shiprocket_matching.py "#AWL93382" --shiprocket-order-id 123456789
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.exceptions import IntegrationError  # noqa: E402
from app.db.session import AsyncSessionLocal, run_with_cleanup  # noqa: E402
from app.integrations.shiprocket.adapter import ShiprocketAdapter  # noqa: E402
from app.models.integration import SyncError  # noqa: E402
from app.repositories.order import OrderRepository  # noqa: E402
from app.repositories.shipment import ShipmentRepository  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def _run(*, order_number: str, shiprocket_order_id: str | None) -> None:
    async with AsyncSessionLocal() as session:
        orders = OrderRepository(session)
        candidates = [order_number]
        if order_number.startswith("#"):
            candidates.append(order_number[1:])
        else:
            candidates.append(f"#{order_number}")

        order = None
        for candidate in candidates:
            order = await orders.get_by_order_number(candidate)
            if order is not None:
                break

        print(f"--- OMS Order lookup for {order_number!r} (also tried {candidates}) ---")
        if order is None:
            print(
                "NOT FOUND in `orders`. If Shiprocket's shipment genuinely refers to "
                "this order, matching can never succeed until the order is synced "
                "from Shopify first -- this is a data-availability gap, not a "
                "matching-logic bug."
            )
            return

        print(f"id={order.id}")
        print(f"order_number={order.order_number!r}  (this is what matching compares against)")
        print(f"source_system={order.source_system!r} external_id={order.external_id!r}")
        print(f"order_datetime={order.order_datetime}")

        print("\n--- OMS Shipment row(s) already linked to this order ---")
        shipments = ShipmentRepository(session)
        existing_shipments = await shipments.list_for_order(order.id)
        if not existing_shipments:
            print(
                "None. Either no Shiprocket shipment has matched this order yet, or "
                "this order has genuinely never been shipped via Shiprocket."
            )
        for shipment in existing_shipments:
            print(
                f"  id={shipment.id} source_system={shipment.source_system!r} "
                f"external_id={shipment.external_id!r} "
                f"shiprocket_shipment_id={shipment.shiprocket_shipment_id!r} "
                f"awb={shipment.awb!r} current_status={shipment.current_status}"
            )

        print("\n--- Most recent cached SyncError for entity_type='shipments' ---")
        result = await session.execute(
            select(SyncError)
            .where(SyncError.entity_type == "shipments")
            .order_by(SyncError.created_at.desc())
            .limit(20)
        )
        recent_shipment_errors = [
            e for e in result.scalars().all() if order_number.lstrip("#") in (e.error_message or "")
        ]
        if not recent_shipment_errors:
            print(
                "No recent SyncError mentions this order number in its message "
                "(checked the 20 most recent 'shipments' errors)."
            )
        for error in recent_shipment_errors:
            print(f"  created_at={error.created_at} error_type={error.error_type!r}")
            print(f"  external_id={error.external_id!r}")
            print(f"  message={error.error_message!r}")

    if shiprocket_order_id:
        print(f"\n--- LIVE GET /orders/show/{shiprocket_order_id} (via adapter.get_order) ---")
        adapter = ShiprocketAdapter()
        try:
            detail = await adapter.get_order(shiprocket_order_id)
        except IntegrationError as exc:
            print(f"IntegrationError: {exc.message} (error_type={exc.details.get('error_type')})")
            return
        body = (detail.get("data") if isinstance(detail, dict) else None) or detail
        raw_channel_order_id = body.get("channel_order_id") if isinstance(body, dict) else None
        value_type = type(raw_channel_order_id).__name__
        print(f"raw channel_order_id={raw_channel_order_id!r} (type={value_type})")
        print(
            "Compare this exact value against order_number above -- this is what "
            "`_resolve_order_by_channel_order_id` tries (as-is, and with a leading "
            "'#' if it doesn't already have one)."
        )
        if isinstance(body, dict):
            print(f"full response keys: {sorted(body.keys())}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("order_number", help='e.g. "#AWL93382" or "AWL93382"')
    parser.add_argument(
        "--shiprocket-order-id",
        default=None,
        help=(
            "Shiprocket's own numeric order id (from a `/shipments` record's "
            "`order_id` field) -- if given, also runs the live /orders/show check."
        ),
    )
    args = parser.parse_args()
    asyncio.run(
        run_with_cleanup(
            _run(order_number=args.order_number, shiprocket_order_id=args.shiprocket_order_id)
        )
    )


if __name__ == "__main__":
    main()
