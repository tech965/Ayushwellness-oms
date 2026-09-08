"""TEMPORARY diagnostic script — NOT part of the app, no code changes.

READ-ONLY. Reports every duplicate (product_variant_id, order_id,
movement_type) group in `inventory_movements` -- the exact condition
the `c9f4a2e6b813` migration's dedup logic (see
`alembic/versions/c9f4a2e6b813_inventory_movement_race_constraint.py`)
resolves before creating `uq_inventory_movements_variant_order_type` --
so a human can inspect the real data and decide whether that logic's
verdicts are safe BEFORE the migration is run against production.

Safety, in order of redundancy (belt AND suspenders, not either/or):
  1. Every query in this file is a literal `SELECT` -- there is no
     UPDATE/DELETE/INSERT/DDL anywhere in this module, and no user input
     is ever interpolated into SQL (only bound parameters).
  2. The single database session this script opens issues
     `SET TRANSACTION READ ONLY` as its first statement -- Postgres
     itself then rejects any write for the rest of that transaction
     with `InFailedSqlTransactionError`/`ReadOnlySqlTransactionError`,
     so even a bug in this script could not modify data.
  3. The session is only ever rolled back, never committed.

Connects exactly the way the running application does -- via
`app.db.session.AsyncSessionLocal`, which is built from
`app.core.config.settings.DATABASE_URL` (the same environment variable
the API/worker actually run with). This script never reads, logs, or
prints that value, or any other credential.

Run wherever the real `DATABASE_URL` for the target environment is
already configured (e.g. a Render Shell on the API/worker service for
production; a local shell for the dev DB):

    python scripts/diagnose_inventory_duplicates.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db.session import AsyncSessionLocal, run_with_cleanup  # noqa: E402

_DUPLICATE_GROUPS_SQL = text(
    """
    SELECT product_variant_id, order_id, movement_type, count(*) AS n
    FROM inventory_movements
    WHERE order_id IS NOT NULL
    GROUP BY product_variant_id, order_id, movement_type
    HAVING count(*) > 1
    ORDER BY n DESC, product_variant_id, order_id
    """
)

_GROUP_ROWS_SQL = text(
    """
    SELECT
        id, product_variant_id, order_id, movement_type,
        quantity_delta, quantity_after,
        quantity_after - quantity_delta AS quantity_before,
        LAG(quantity_after) OVER (ORDER BY created_at, id) AS previous_quantity_after,
        shipment_id, rto_id, actor_user_id, reason, notes, created_at
    FROM inventory_movements
    WHERE product_variant_id = :variant_id
      AND order_id = :order_id
      AND movement_type = :movement_type
    ORDER BY created_at, id
    """
)

_VARIANT_FULL_HISTORY_SQL = text(
    """
    SELECT
        id, order_id, movement_type,
        quantity_delta, quantity_after,
        quantity_after - quantity_delta AS quantity_before,
        LAG(quantity_after) OVER (
            PARTITION BY product_variant_id ORDER BY created_at, id
        ) AS previous_quantity_after,
        shipment_id, rto_id, reason, created_at
    FROM inventory_movements
    WHERE product_variant_id = :variant_id
    ORDER BY created_at, id
    """
)

_VARIANT_CURRENT_STATE_SQL = text(
    """
    SELECT
        pv.id, pv.sku, pv.title, pv.available_quantity,
        p.title AS product_title,
        (SELECT count(*) FROM inventory_movements m WHERE m.product_variant_id = pv.id) AS movement_count,
        (SELECT COALESCE(SUM(m.quantity_delta), 0) FROM inventory_movements m
         WHERE m.product_variant_id = pv.id) AS ledger_delta_sum
    FROM product_variants pv
    JOIN products p ON p.id = pv.product_id
    WHERE pv.id = :variant_id
    """
)

_ORDER_INFO_SQL = text(
    """
    SELECT
        o.id, o.order_number, o.shopify_order_id, o.status, o.fulfillment_status,
        o.cancellation_status, o.payment_type, o.payment_status, o.total_amount,
        o.order_datetime, o.confirmed_by_telecaller_id, o.confirmed_at, o.created_at,
        c.full_name AS customer_name, c.phone AS customer_phone
    FROM orders o
    LEFT JOIN customers c ON c.id = o.customer_id
    WHERE o.id = :order_id
    """
)


def _fmt(value: object) -> str:
    return "—" if value is None else str(value)


async def _diagnose() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))

        try:
            groups = (await session.execute(_DUPLICATE_GROUPS_SQL)).fetchall()

            print("=" * 100)
            print("INVENTORY MOVEMENT DUPLICATE DIAGNOSTIC (read-only)")
            print("=" * 100)

            if not groups:
                print("\nNo duplicate (product_variant_id, order_id, movement_type) groups found.")
                print("The unique constraint can be created with no data changes required.")
                return

            print(f"\nFound {len(groups)} duplicate group(s).\n")

            # --- Section 1: duplicate groups ------------------------------
            print("-" * 100)
            print("SECTION 1 — Duplicate groups")
            print("-" * 100)
            for g in groups:
                print(
                    f"  variant={g.product_variant_id}  order={g.order_id}  "
                    f"type={g.movement_type}  rows={g.n}"
                )

            variant_ids = sorted({g.product_variant_id for g in groups})
            order_ids = sorted({g.order_id for g in groups})

            # --- Section 2: full rows per duplicate group + verdict -------
            print("\n" + "-" * 100)
            print("SECTION 2 — Full rows per duplicate group, with verdict")
            print("-" * 100)
            for g in groups:
                rows = (
                    await session.execute(
                        _GROUP_ROWS_SQL,
                        {
                            "variant_id": g.product_variant_id,
                            "order_id": g.order_id,
                            "movement_type": g.movement_type,
                        },
                    )
                ).fetchall()

                distinct_quantity_after = {r.quantity_after for r in rows}
                canonical = rows[0]
                latest = rows[-1]

                if len(distinct_quantity_after) == 1:
                    verdict = "LOST UPDATE — identical quantity_after across all rows"
                else:
                    verdict = "TRUE DOUBLE DEDUCTION/RESTOCK — quantity_after differs across rows"

                print(
                    f"\n  Group: variant={g.product_variant_id} order={g.order_id} "
                    f"type={g.movement_type}"
                )
                print(f"    Verdict: {verdict}")
                for r in rows:
                    is_canonical = " (CANONICAL - earliest)" if r.id == canonical.id else ""
                    print(
                        f"    - id={r.id} created_at={r.created_at} "
                        f"quantity_before={_fmt(r.quantity_before)} "
                        f"quantity_delta={r.quantity_delta} "
                        f"quantity_after={r.quantity_after} "
                        f"previous_quantity_after={_fmt(r.previous_quantity_after)} "
                        f"shipment_id={_fmt(r.shipment_id)} rto_id={_fmt(r.rto_id)} "
                        f"actor_user_id={_fmt(r.actor_user_id)} reason={_fmt(r.reason)}"
                        f"{is_canonical}"
                    )

                if len(distinct_quantity_after) > 1:
                    variant_state = (
                        await session.execute(
                            _VARIANT_CURRENT_STATE_SQL, {"variant_id": g.product_variant_id}
                        )
                    ).fetchone()
                    current_qty = variant_state.available_quantity if variant_state else None
                    provably_correctable = current_qty == latest.quantity_after
                    print(
                        f"    Current available_quantity={_fmt(current_qty)} vs. this group's "
                        f"cascade terminal value (latest row's quantity_after)={latest.quantity_after}"
                    )
                    print(
                        "    -> Migration would auto-correct available_quantity to "
                        f"{canonical.quantity_after} (provably safe)"
                        if provably_correctable
                        else "    -> Migration would NOT auto-correct (not provable) — "
                        "flagged for manual review, quantity left as-is"
                    )

            # --- Section 3: full movement history for affected variants ---
            print("\n" + "-" * 100)
            print("SECTION 3 — Full movement history for affected variants (all orders/types)")
            print("-" * 100)
            for variant_id in variant_ids:
                history = (
                    await session.execute(_VARIANT_FULL_HISTORY_SQL, {"variant_id": variant_id})
                ).fetchall()
                print(f"\n  Variant {variant_id} — {len(history)} total movement row(s):")
                for r in history:
                    print(
                        f"    - id={r.id} order_id={_fmt(r.order_id)} type={r.movement_type} "
                        f"created_at={r.created_at} "
                        f"quantity_before={_fmt(r.quantity_before)} "
                        f"quantity_delta={r.quantity_delta} "
                        f"quantity_after={r.quantity_after} "
                        f"previous_quantity_after={_fmt(r.previous_quantity_after)} "
                        f"reason={_fmt(r.reason)}"
                    )

            # --- Section 4: current state + running ledger sum ------------
            print("\n" + "-" * 100)
            print(
                "SECTION 4 — Current available_quantity vs. running sum of ledger deltas "
                "(sanity check independent of the duplicate-group heuristic)"
            )
            print("-" * 100)
            for variant_id in variant_ids:
                state = (
                    await session.execute(
                        _VARIANT_CURRENT_STATE_SQL, {"variant_id": variant_id}
                    )
                ).fetchone()
                if state is None:
                    print(f"\n  Variant {variant_id} — NOT FOUND")
                    continue
                mismatch = state.available_quantity != state.ledger_delta_sum
                print(
                    f"\n  Variant {variant_id} ({state.sku} — {state.product_title} / {state.title})"
                )
                print(f"    available_quantity      = {state.available_quantity}")
                print(f"    sum(quantity_delta)     = {state.ledger_delta_sum}")
                print(f"    movement row count      = {state.movement_count}")
                if mismatch:
                    print(
                        "    *** MISMATCH: available_quantity does not equal the ledger's own "
                        "running sum. Known, expected cause: `a4e9d3c7f158` (the migration that "
                        "introduced available_quantity) seeded it via "
                        "`UPDATE product_variants SET available_quantity = inventory_quantity` "
                        "-- a one-time bulk copy with NO corresponding INITIAL_STOCK ledger row. "
                        "Every variant's real starting balance is invisible to this ledger; "
                        "sum(quantity_delta) only reflects movements SINCE that seed point. "
                        "This mismatch alone is NOT evidence of corruption -- but it does mean "
                        "the ledger must never be treated as a complete history, and this is "
                        "exactly why the migration's dedup logic never uses available_quantity "
                        "to justify a quantity correction. ***"
                    )
                else:
                    print("    OK: available_quantity matches the ledger's running sum.")

            # --- Section 5: related order information ----------------------
            print("\n" + "-" * 100)
            print("SECTION 5 — Related order information")
            print("-" * 100)
            for order_id in order_ids:
                order = (
                    await session.execute(_ORDER_INFO_SQL, {"order_id": order_id})
                ).fetchone()
                if order is None:
                    print(f"\n  Order {order_id} — NOT FOUND")
                    continue
                print(f"\n  Order {order_id}")
                print(
                    f"    order_number={order.order_number} shopify_order_id={_fmt(order.shopify_order_id)}"
                )
                print(
                    f"    status={order.status} fulfillment_status={order.fulfillment_status} "
                    f"cancellation_status={order.cancellation_status}"
                )
                print(
                    f"    payment_type={order.payment_type} payment_status={order.payment_status} "
                    f"total_amount={order.total_amount}"
                )
                print(
                    f"    customer={_fmt(order.customer_name)} phone={_fmt(order.customer_phone)}"
                )
                print(
                    f"    confirmed_by_telecaller_id={_fmt(order.confirmed_by_telecaller_id)} "
                    f"confirmed_at={_fmt(order.confirmed_at)}"
                )
                print(f"    order_datetime={order.order_datetime} created_at={order.created_at}")

            print("\n" + "=" * 100)
            print("END OF REPORT — read-only, nothing was modified.")
            print("=" * 100)
        finally:
            await session.rollback()


async def main() -> None:
    await run_with_cleanup(_diagnose())


if __name__ == "__main__":
    asyncio.run(main())
