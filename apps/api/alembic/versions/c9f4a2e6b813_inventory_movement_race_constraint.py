"""add uniqueness constraint to inventory_movements (concurrency safety)

Revision ID: c9f4a2e6b813
Revises: b7d2e5a91c3f
Create Date: 2026-09-08 00:00:00.000000

Prevents two concurrent transactions from both recording a DISPATCH or
RTO_RESTOCK movement for the same (variant, order) -- the existing
`exists_for_order` check is only a snapshot-time read and is not by
itself safe against a race between, e.g., a webhook delivery and an
overlapping pull-sync re-scan for the same shipment. `order_id` is
always NULL for MANUAL_ADJUSTMENT/INITIAL_STOCK rows; NULL never
collides with itself in a SQL unique constraint, so this never restricts
those movement types (multiple manual adjustments for the same variant
remain unrestricted, as intended).

Before this fix: if duplicate (product_variant_id, order_id,
movement_type) rows already exist (from running against this exact
race, before this constraint could ever be applied), a plain
`ADD CONSTRAINT` fails outright. `upgrade()` below now resolves that
itself -- see `_dedupe_inventory_movements()` -- so this migration is
safe to run directly against a database that has been live under the
race for a while, and remains a no-op cleanup (then a plain constraint
add) against a fresh database that never had the race.

Dedup algorithm, per (product_variant_id, order_id, movement_type)
group with more than one row:

  1. Canonical row = earliest by (created_at, id) -- the first
     legitimate write; every other row in the group is a race
     duplicate.
  2. If every row in the group shares the same `quantity_after`: this
     is a "lost update" -- both racing transactions read the same
     pre-write `available_quantity` and wrote the same absolute new
     value (see `InventoryService.apply_dispatch`/`apply_rto_restock`,
     which write an absolute -- not relative -- `available_quantity`).
     The ledger has a phantom duplicate row but the actual stock
     quantity was only ever moved once: no quantity correction is
     needed or applied. Only the extra ledger row(s) are removed.
  3. Otherwise the group is a "true double deduction/restock" -- later
     racers each read the previous racer's already-committed write and
     moved stock again on top of it. The group's latest row (by
     created_at) is the cascade's terminal effect. Only when the
     variant's CURRENT `available_quantity` exactly equals that
     terminal row's `quantity_after` is it provable that nothing else
     has touched this variant since -- only then is it safe to correct
     `available_quantity` back to the canonical row's `quantity_after`
     (the value after exactly one legitimate move). If the current
     quantity does not match, some other movement has happened since
     and auto-correcting could clobber real history; the quantity is
     left untouched and the group is logged as needing manual review.
  4. In every case, all non-canonical rows in the group are deleted so
     the unique constraint can be created; the canonical row is always
     kept, so the ledger's earliest record of the event is preserved.

Fully idempotent: a second run finds no duplicate groups (the first
run already resolved them), so `_dedupe_inventory_movements()` is a
no-op and only the constraint creation runs -- which by then already
exists, so Alembic would only ever re-attempt this migration if
`alembic_version` were manually rolled back, which is exactly what we
were told not to do. Wrapped by `env.py`'s transactional DDL: any
failure anywhere in `upgrade()` rolls back every dedup write and the
constraint creation together, leaving the database exactly as it was
before this migration ran.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "c9f4a2e6b813"
down_revision: str | None = "b7d2e5a91c3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _dedupe_inventory_movements() -> None:
    """Resolve any pre-existing duplicate (product_variant_id, order_id,
    movement_type) groups before the unique constraint below is created.
    See the migration module docstring for the full algorithm and its
    safety reasoning. No-op (zero groups found) on a fresh database.
    """
    bind = op.get_bind()

    groups = bind.execute(
        text(
            """
            SELECT product_variant_id, order_id, movement_type, count(*) AS n
            FROM inventory_movements
            WHERE order_id IS NOT NULL
            GROUP BY product_variant_id, order_id, movement_type
            HAVING count(*) > 1
            """
        )
    ).fetchall()

    if not groups:
        print("[c9f4a2e6b813] No duplicate inventory_movements groups found.")
        return

    print(f"[c9f4a2e6b813] Found {len(groups)} duplicate inventory_movements group(s).")

    lost_update_groups = 0
    corrected_groups = 0
    manual_review_groups = 0
    rows_deleted = 0

    for group in groups:
        variant_id: uuid.UUID = group.product_variant_id
        order_id: uuid.UUID = group.order_id
        movement_type: str = group.movement_type

        rows = bind.execute(
            text(
                """
                SELECT id, quantity_delta, quantity_after, created_at
                FROM inventory_movements
                WHERE product_variant_id = :variant_id
                  AND order_id = :order_id
                  AND movement_type = :movement_type
                ORDER BY created_at ASC, id ASC
                """
            ),
            {"variant_id": variant_id, "order_id": order_id, "movement_type": movement_type},
        ).fetchall()

        canonical = rows[0]
        latest = rows[-1]
        duplicates = rows[1:]
        distinct_quantity_after = {row.quantity_after for row in rows}

        print(
            f"[c9f4a2e6b813]   group variant={variant_id} order={order_id} "
            f"type={movement_type} rows={len(rows)} "
            f"canonical_id={canonical.id} "
            f"quantity_before={canonical.quantity_after - canonical.quantity_delta} "
            f"canonical_quantity_after={canonical.quantity_after} "
            f"latest_quantity_after={latest.quantity_after}"
        )

        if len(distinct_quantity_after) == 1:
            lost_update_groups += 1
            print(
                f"[c9f4a2e6b813]     -> lost update (identical quantity_after across all "
                f"{len(rows)} rows): removing {len(duplicates)} duplicate row(s), "
                "no quantity correction needed."
            )
        else:
            current_quantity = bind.execute(
                text("SELECT available_quantity FROM product_variants WHERE id = :variant_id"),
                {"variant_id": variant_id},
            ).scalar_one()

            if current_quantity == latest.quantity_after:
                corrected_groups += 1
                print(
                    f"[c9f4a2e6b813]     -> true double deduction/restock, PROVABLY safe to "
                    f"correct: current available_quantity ({current_quantity}) matches the "
                    f"race cascade's terminal value. Correcting available_quantity "
                    f"{current_quantity} -> {canonical.quantity_after} (canonical value) and "
                    f"removing {len(duplicates)} duplicate row(s)."
                )
                bind.execute(
                    text(
                        "UPDATE product_variants SET available_quantity = :qty, "
                        "updated_at = now() WHERE id = :variant_id"
                    ),
                    {"qty": canonical.quantity_after, "variant_id": variant_id},
                )
            else:
                manual_review_groups += 1
                print(
                    f"[c9f4a2e6b813]     -> true double deduction/restock, NOT provably safe "
                    f"to auto-correct: current available_quantity ({current_quantity}) does "
                    "not match this group's cascade terminal value "
                    f"({latest.quantity_after}) -- other movements have touched this variant "
                    "since. Leaving available_quantity untouched; FLAGGED FOR MANUAL REVIEW. "
                    f"Removing {len(duplicates)} duplicate ledger row(s) only "
                    f"(canonical_id={canonical.id} kept)."
                )

        duplicate_ids = [row.id for row in duplicates]
        bind.execute(
            text("DELETE FROM inventory_movements WHERE id = ANY(:ids)"),
            {"ids": duplicate_ids},
        )
        rows_deleted += len(duplicate_ids)

    print(
        f"[c9f4a2e6b813] Dedup summary: {len(groups)} group(s) resolved -- "
        f"{lost_update_groups} lost-update (no correction), "
        f"{corrected_groups} corrected (provably safe), "
        f"{manual_review_groups} flagged for manual review (quantity left untouched), "
        f"{rows_deleted} duplicate row(s) deleted."
    )
    if manual_review_groups:
        print(
            f"[c9f4a2e6b813] ACTION REQUIRED: {manual_review_groups} group(s) need manual "
            "review -- see the '-> true double deduction/restock, NOT provably safe' lines "
            "above for the affected product_variant_id/order_id and current vs. expected "
            "quantity."
        )


def upgrade() -> None:
    _dedupe_inventory_movements()
    op.create_unique_constraint(
        "uq_inventory_movements_variant_order_type",
        "inventory_movements",
        ["product_variant_id", "order_id", "movement_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_inventory_movements_variant_order_type",
        "inventory_movements",
        type_="unique",
    )
