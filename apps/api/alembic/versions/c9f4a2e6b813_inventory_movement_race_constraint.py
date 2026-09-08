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

============================================================================
WHY THIS MIGRATION NEVER TOUCHES product_variants.available_quantity
============================================================================
An earlier version of this migration attempted to auto-correct
`available_quantity` for a "true double deduction" duplicate group when
the variant's CURRENT quantity happened to exactly match the group's own
race-cascade terminal value. That was withdrawn after inspecting real
production data: `available_quantity` does NOT reconcile with
`sum(inventory_movements.quantity_delta)` for the affected variants, so
the ledger cannot be assumed to be a complete history of every quantity
change -- proving that comparing "current quantity" against "what this
duplicate group implies" is not a safe basis for ANY automatic write to
`available_quantity`, ever, regardless of how clean an individual
group's own numbers look in isolation.

The root cause of that non-reconciliation is understood, not mysterious:
`a4e9d3c7f158` (the migration that introduced `available_quantity`)
seeded it with `UPDATE product_variants SET available_quantity =
inventory_quantity` -- a one-time bulk copy from the passive Shopify
mirror column, executed OUTSIDE the `InventoryMovement` ledger (no
`INITIAL_STOCK` row was ever written for it). Every variant's real
starting balance is therefore invisible to the ledger; `sum(quantity_
delta)` only ever reflects movements SINCE that seed point, never the
seed itself. A variant showing `available_quantity = 3379` and
`sum(quantity_delta) = -527` is not necessarily corrupted -- it most
likely means the variant was seeded at roughly 3379 - (-527) = 3906
boxes and has legitimately moved down since. This migration cannot tell
"expected, un-ledgered baseline" apart from "genuine corruption" from
`inventory_movements` alone, and does not try to.

Consequently: this migration NEVER writes `product_variants.
available_quantity` under any condition. Its only two possible actions
per duplicate group are (1) delete non-canonical rows that are PROVABLY
redundant -- identical `quantity_after` across the entire group, meaning
they can only be repeated recordings of the exact same already-applied
change -- or (2) leave the group completely untouched and require a
human to resolve it out of band. See `_dedupe_inventory_movements()`
below for the full algorithm.

============================================================================
DEDUP ALGORITHM
============================================================================
Per (product_variant_id, order_id, movement_type) group with more than
one row, ordered by (created_at, id):

  1. Canonical row = the earliest (first legitimate write). Always kept,
     never touched, never deleted.
  2. If EVERY row in the group shares the exact same `quantity_after`:
     this is a "lost update" -- concurrent racers each read the same
     pre-write `available_quantity` and wrote the same absolute value
     (see `InventoryService.apply_dispatch`/`apply_rto_restock`, which
     write an absolute -- not relative -- `available_quantity`). The
     ledger has redundant duplicate rows but their recorded effect is
     IDENTICAL to the canonical row's, so deleting the non-canonical
     ones changes nothing about what the ledger records happened.
     Provably safe: the non-canonical rows are deleted.
  3. Any other group (quantity_after differs anywhere in the group) is
     "true double deduction/restock or otherwise ambiguous". Nothing in
     this group is deleted, modified, or otherwise touched. It is
     printed as ACTION REQUIRED with full row detail (including, purely
     for a human's reference, the variant's current `available_quantity`
     -- never read for any decision this migration makes).

If ANY group falls into case 3, this migration prints every such group
in full and then RAISES, aborting before `create_unique_constraint` is
ever called. Because `env.py` wraps the whole run in one transaction,
raising rolls back everything from this run, including any case-2
deletions already made -- so the outcome is always all-or-nothing: either
every duplicate group in the table is resolvable by the provably-safe
rule and the constraint gets created, or nothing at all changes and
`alembic_version` stays at `b7d2e5a91c3f` until the ACTION REQUIRED
groups are resolved by a human, out of band, and this migration is run
again. This migration is never the thing that decides what an ambiguous
group "should" have been -- only a human reviewing the printed detail
(or the read-only `scripts/diagnose_inventory_duplicates.py` report) can
do that.

This never touches MANUAL_ADJUSTMENT/INITIAL_STOCK rows (`order_id IS
NULL`, excluded by the grouping query below) or any row for a variant/
order/type combination that isn't actually duplicated.

Fully idempotent: a second run against an already-resolved database
finds zero duplicate groups, so `_dedupe_inventory_movements()` is a
no-op and only `create_unique_constraint` would run -- which by then
already exists, so in practice this migration is only ever re-attempted
after a rollback to `b7d2e5a91c3f`, never re-run on top of itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal, NamedTuple

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "c9f4a2e6b813"
down_revision: str | None = "b7d2e5a91c3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# `NamedTuple`, not `@dataclass` -- Alembic loads each version file via
# its own `importlib.util.spec_from_file_location` without registering
# it in `sys.modules` first (`alembic/util/pyfiles.py::load_module_py`).
# Combined with `from __future__ import annotations` above (every field
# annotation becomes a string), `@dataclass`'s ClassVar/InitVar
# detection then needs `sys.modules[cls.__module__]` to resolve those
# strings and raises `AttributeError` when it isn't there -- breaking
# `alembic current`/`upgrade` outright. `NamedTuple` never needs that
# resolution at construction time, so it works under Alembic's loader.
class MovementRow(NamedTuple):
    """Plain, DB-independent view of one `inventory_movements` row --
    only the fields the classification logic below actually needs.
    Deliberately excludes `available_quantity`/any variant-level field:
    `_classify_duplicate_group` must never have the ability to base a
    decision on it (see the module docstring for why).
    """

    id: uuid.UUID
    quantity_delta: int
    quantity_after: int


class GroupPlan(NamedTuple):
    """The decision for one duplicate group. `action` is the whole
    contract with the caller: "delete_duplicates" is the only action
    that ever touches a row, and it never touches
    `product_variants.available_quantity`. "action_required" touches
    nothing at all.
    """

    canonical_id: uuid.UUID
    action: Literal["delete_duplicates", "action_required"]
    duplicate_ids_to_delete: tuple[uuid.UUID, ...]
    reason: str


class UnresolvedDuplicateInventoryMovementsError(RuntimeError):
    """Raised when one or more duplicate groups cannot be safely
    auto-resolved. Aborting here (rather than silently skipping the
    constraint) keeps `alembic_version` honest -- this migration is
    never recorded as applied unless the constraint was actually
    created.
    """


def _classify_duplicate_group(rows: Sequence[MovementRow]) -> GroupPlan:
    """Pure decision logic, no DB access -- see the module docstring's
    "DEDUP ALGORITHM" section. `rows` must already be ordered by
    (created_at, id) ascending; `rows[0]` is therefore the canonical
    row by construction.
    """
    if len(rows) < 2:
        raise ValueError("_classify_duplicate_group requires at least 2 rows")

    canonical = rows[0]
    duplicates = rows[1:]
    distinct_quantity_after = {r.quantity_after for r in rows}

    if len(distinct_quantity_after) == 1:
        return GroupPlan(
            canonical_id=canonical.id,
            action="delete_duplicates",
            duplicate_ids_to_delete=tuple(r.id for r in duplicates),
            reason=(
                f"lost update: identical quantity_after ({canonical.quantity_after}) "
                f"across all {len(rows)} rows -- non-canonical rows are provably redundant"
            ),
        )

    return GroupPlan(
        canonical_id=canonical.id,
        action="action_required",
        duplicate_ids_to_delete=(),
        reason=(
            "quantity_after differs across the group "
            f"({sorted(distinct_quantity_after)}) -- cannot prove these rows are redundant "
            "without risking a wrong guess about real inventory history; left untouched"
        ),
    )


_DUPLICATE_GROUPS_SQL = text(
    """
    SELECT product_variant_id, order_id, movement_type, count(*) AS n
    FROM inventory_movements
    WHERE order_id IS NOT NULL
    GROUP BY product_variant_id, order_id, movement_type
    HAVING count(*) > 1
    """
)

_GROUP_ROWS_SQL = text(
    """
    SELECT id, quantity_delta, quantity_after, created_at
    FROM inventory_movements
    WHERE product_variant_id = :variant_id
      AND order_id = :order_id
      AND movement_type = :movement_type
    ORDER BY created_at ASC, id ASC
    """
)


def _dedupe_inventory_movements(bind: Connection) -> None:
    """Resolve provably-safe duplicate groups and delete only those
    rows; raise `UnresolvedDuplicateInventoryMovementsError` (touching
    nothing further) if any group cannot be proven safe. Never reads or
    writes `product_variants.available_quantity` for any decision --
    see the module docstring.
    """
    groups = bind.execute(_DUPLICATE_GROUPS_SQL).fetchall()

    if not groups:
        print("[c9f4a2e6b813] No duplicate inventory_movements groups found.")
        return

    print(f"[c9f4a2e6b813] Found {len(groups)} duplicate inventory_movements group(s).")

    resolved = 0
    rows_deleted = 0
    action_required: list[dict[str, Any]] = []

    for group in groups:
        raw_rows = bind.execute(
            _GROUP_ROWS_SQL,
            {
                "variant_id": group.product_variant_id,
                "order_id": group.order_id,
                "movement_type": group.movement_type,
            },
        ).fetchall()
        rows = [MovementRow(id=r.id, quantity_delta=r.quantity_delta, quantity_after=r.quantity_after) for r in raw_rows]
        plan = _classify_duplicate_group(rows)

        print(
            f"[c9f4a2e6b813]   group variant={group.product_variant_id} "
            f"order={group.order_id} type={group.movement_type} rows={len(rows)} "
            f"canonical_id={plan.canonical_id} action={plan.action}"
        )
        for r, raw in zip(rows, raw_rows, strict=True):
            print(
                f"[c9f4a2e6b813]     id={r.id} created_at={raw.created_at} "
                f"quantity_before={r.quantity_after - r.quantity_delta} "
                f"quantity_delta={r.quantity_delta} quantity_after={r.quantity_after}"
                f"{' (CANONICAL)' if r.id == plan.canonical_id else ''}"
            )
        print(f"[c9f4a2e6b813]     -> {plan.reason}")

        if plan.action == "delete_duplicates":
            bind.execute(
                text("DELETE FROM inventory_movements WHERE id = ANY(:ids)"),
                {"ids": list(plan.duplicate_ids_to_delete)},
            )
            rows_deleted += len(plan.duplicate_ids_to_delete)
            resolved += 1
        else:
            current_quantity = bind.execute(
                text("SELECT available_quantity FROM product_variants WHERE id = :variant_id"),
                {"variant_id": group.product_variant_id},
            ).scalar_one()
            print(
                f"[c9f4a2e6b813]     current available_quantity={current_quantity} "
                "(reference only -- NOT read for, or changed by, this decision)"
            )
            action_required.append(
                {
                    "variant_id": group.product_variant_id,
                    "order_id": group.order_id,
                    "movement_type": group.movement_type,
                }
            )

    print(
        f"[c9f4a2e6b813] Summary: {len(groups)} group(s) total -- "
        f"{resolved} resolved ({rows_deleted} duplicate row(s) deleted, "
        "0 quantity changes), "
        f"{len(action_required)} require manual review."
    )

    if action_required:
        print(
            f"[c9f4a2e6b813] ABORTING: {len(action_required)} group(s) could not be "
            "proven safe to auto-resolve -- see the ACTION REQUIRED groups printed above. "
            "No rows were deleted for these groups and available_quantity was not modified "
            "anywhere. This migration will NOT be recorded as applied; re-run it after "
            "resolving the listed group(s) out of band."
        )
        for g in action_required:
            print(
                f"[c9f4a2e6b813]   ACTION REQUIRED: variant={g['variant_id']} "
                f"order={g['order_id']} type={g['movement_type']}"
            )
        raise UnresolvedDuplicateInventoryMovementsError(
            f"{len(action_required)} duplicate inventory_movements group(s) require manual "
            "review before the unique constraint can be created; see migration output above "
            "for full detail. No production data was modified."
        )


def upgrade() -> None:
    _dedupe_inventory_movements(op.get_bind())
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
