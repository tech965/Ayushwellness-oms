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

`--full-dump` additionally walks the ENTIRE live response (not just
`channel_order_id`) and prints every non-PII key/value pair (PII keys --
matched via the same `_is_pii_key` denylist `adapter.py`'s own diagnostic
logging already uses -- print as `<redacted>`, never their value), plus
an explicit search for one or more `--find` values (e.g. the OMS order's
`external_id`, or the digits from its `order_number`) appearing ANYWHERE
in the response, under any field name, at any nesting depth -- added
specifically to answer "does Shiprocket's response contain the real
Shopify order id under some other field name" without guessing.

Run in a Render Shell (API or worker service -- both have DB + Shiprocket
credentials):
    python scripts/diagnose_shiprocket_matching.py "#AWL93382"
    python scripts/diagnose_shiprocket_matching.py "#AWL93382" --shiprocket-order-id 123456789
    python scripts/diagnose_shiprocket_matching.py "#AWL91738" --shiprocket-order-id 1089477745 \\
        --full-dump --find 6766710554813 --find 91738
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.exceptions import IntegrationError  # noqa: E402
from app.db.session import AsyncSessionLocal, run_with_cleanup  # noqa: E402
from app.integrations.shiprocket.adapter import ShiprocketAdapter, _is_pii_key  # noqa: E402
from app.models.integration import SyncError  # noqa: E402
from app.repositories.order import OrderRepository  # noqa: E402
from app.repositories.shipment import ShipmentRepository  # noqa: E402
from sqlalchemy import select  # noqa: E402

# Only the first this many items of any list (e.g. `shipments`,
# `package_list`) are dumped -- one real record is enough to see the
# shape; a large `product_details`-style array shouldn't flood the shell.
_MAX_LIST_ITEMS_SHOWN = 3
_MAX_DUMP_DEPTH = 4


def _dump_scalar(
    key: str, value: Any, *, indent: str, targets: list[str], found: list[str]
) -> None:
    marker = ""
    text = str(value)
    for target in targets:
        if target and target in text:
            marker = f"   <-- MATCHES --find {target!r}"
            found.append(f"{key} = {value!r} (matched {target!r})")
    print(f"{indent}{key} = {value!r}{marker}")


def _dump_value(
    key: str, value: Any, *, indent: str, targets: list[str], found: list[str], depth: int
) -> None:
    if _is_pii_key(key):
        print(f"{indent}{key} = <redacted, PII key>")
        return

    if isinstance(value, dict):
        if depth >= _MAX_DUMP_DEPTH:
            print(f"{indent}{key}: {{...}}  # max depth reached")
            return
        print(f"{indent}{key}: {{")
        _dump_object(value, indent=indent + "  ", targets=targets, found=found, depth=depth + 1)
        print(f"{indent}}}")
        return

    if isinstance(value, list):
        print(f"{indent}{key}: [  # {len(value)} item(s), showing up to {_MAX_LIST_ITEMS_SHOWN}")
        for item in value[:_MAX_LIST_ITEMS_SHOWN]:
            if isinstance(item, dict) and depth < _MAX_DUMP_DEPTH:
                print(f"{indent}  {{")
                _dump_object(
                    item, indent=indent + "    ", targets=targets, found=found, depth=depth + 1
                )
                print(f"{indent}  }}")
            elif isinstance(item, dict):
                print(f"{indent}  {{...}}  # max depth reached")
            else:
                _dump_scalar(f"{key}[]", item, indent=indent + "  ", targets=targets, found=found)
        print(f"{indent}]")
        return

    _dump_scalar(key, value, indent=indent, targets=targets, found=found)


def _dump_object(
    obj: dict[str, Any], *, indent: str, targets: list[str], found: list[str], depth: int = 0
) -> None:
    for key, value in obj.items():
        _dump_value(key, value, indent=indent, targets=targets, found=found, depth=depth)


async def _run(
    *,
    order_number: str,
    shiprocket_order_id: str | None,
    full_dump: bool,
    find: list[str],
) -> None:
    targets = list(find)

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

        # Auto-search for the two obvious real identifiers even if `--find`
        # wasn't passed: the Shopify order's own internal id (what this OMS
        # already stores as `external_id`), and the plain digits from the
        # order number (in case some other field holds those digits without
        # the "#AWL" formatting).
        if order.external_id and order.external_id not in targets:
            targets.append(order.external_id)
        digits_match = re.search(r"\d+", order.order_number or "")
        digits_only = digits_match.group() if digits_match else ""
        if digits_only and digits_only not in targets:
            targets.append(digits_only)

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

        if full_dump and isinstance(body, dict):
            print(f"\n--- Full non-PII field dump (searching for {targets!r}) ---")
            found: list[str] = []
            _dump_object(body, indent="  ", targets=targets, found=found)
            print("\n--- Search results ---")
            if found:
                for line in found:
                    print(f"  FOUND: {line}")
            else:
                print(f"  None of {targets!r} appear anywhere in this response.")


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
    parser.add_argument(
        "--full-dump",
        action="store_true",
        help=(
            "Also print every non-PII field in the live /orders/show response "
            "(requires --shiprocket-order-id) and search it for the OMS order's "
            "own external_id / order_number digits, plus any --find values."
        ),
    )
    parser.add_argument(
        "--find",
        action="append",
        default=[],
        help="Additional value to search for anywhere in the response (repeatable).",
    )
    args = parser.parse_args()
    asyncio.run(
        run_with_cleanup(
            _run(
                order_number=args.order_number,
                shiprocket_order_id=args.shiprocket_order_id,
                full_dump=args.full_dump,
                find=args.find,
            )
        )
    )


if __name__ == "__main__":
    main()
