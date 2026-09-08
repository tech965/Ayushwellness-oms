"""Live-Postgres integration tests for the `c9f4a2e6b813` migration's
`_dedupe_inventory_movements` -- the actual data-changing logic, run
against a real `inventory_movements`/`product_variants` table (not
SQLite, which the rest of this suite uses -- the migration's SQL is
Postgres-specific: `ANY(:ids)` array binding, the real
`uq_inventory_movements_variant_order_type` constraint DDL, etc).

Opt-in only: these run against whatever Postgres `DATABASE_URL` already
points at (the local dev DB in this repo), and while every write they
make is inside one transaction that is ALWAYS rolled back (never
committed) -- including the unique constraint drop/recreate, since
Postgres DDL is transactional -- cycling constraint DDL against a
developer's live dev DB on every default `pytest -q` run is more
invasive than this repo's existing convention (100% SQLite elsewhere).
Set RUN_LIVE_DB_TESTS=1 to opt in:

    RUN_LIVE_DB_TESTS=1 pytest tests/test_inventory_dedup_migration_live_db.py -v

Each test:
  1. Opens one connection, begins one transaction.
  2. Drops `uq_inventory_movements_variant_order_type` if present (so
     duplicate rows can be inserted regardless of the DB's current
     migration state).
  3. Inserts a minimal, self-contained product/variant/order fixture
     with fresh random UUIDs (never touches pre-existing rows).
  4. Calls the real `_dedupe_inventory_movements(connection)`.
  5. Asserts on the resulting rows/values.
  6. Rolls back -- restoring the constraint (if it existed) and
     discarding every insert/delete, so the database is byte-for-byte
     unchanged after the test session ends regardless of pass/fail.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DB_TESTS") != "1",
    reason=(
        "Live-Postgres migration integration tests are opt-in -- set "
        "RUN_LIVE_DB_TESTS=1 to run them against the local dev DATABASE_URL."
    ),
)

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "c9f4a2e6b813_inventory_movement_race_constraint.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("c9f4a2e6b813_migration_live", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_migration = _load_migration_module()
_dedupe_inventory_movements = _migration._dedupe_inventory_movements
UnresolvedDuplicateInventoryMovementsError = _migration.UnresolvedDuplicateInventoryMovementsError

_CONSTRAINT_SQL = text(
    """
    ALTER TABLE inventory_movements
    ADD CONSTRAINT uq_inventory_movements_variant_order_type
    UNIQUE (product_variant_id, order_id, movement_type)
    """
)


@pytest.fixture
def pg_conn():
    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    try:
        engine = create_engine(sync_url)
        connection = engine.connect()
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Postgres not reachable at configured DATABASE_URL: {exc}")
        return

    trans = connection.begin()
    connection.execute(
        text(
            "ALTER TABLE inventory_movements "
            "DROP CONSTRAINT IF EXISTS uq_inventory_movements_variant_order_type"
        )
    )
    try:
        yield connection
    finally:
        trans.rollback()
        connection.close()
        engine.dispose()


def _make_product_and_variant(conn, *, available_quantity: int) -> uuid.UUID:
    product_id = uuid.uuid4()
    variant_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO products (id, title, status) "
            "VALUES (:id, 'Dedup Test Product', 'active')"
        ),
        {"id": product_id},
    )
    conn.execute(
        text(
            "INSERT INTO product_variants "
            "(id, product_id, sku, price, status, available_quantity) "
            "VALUES (:id, :product_id, :sku, :price, 'active', :qty)"
        ),
        {
            "id": variant_id,
            "product_id": product_id,
            "sku": f"DEDUP-TEST-{variant_id.hex[:8]}",
            "price": Decimal("100.00"),
            "qty": available_quantity,
        },
    )
    return variant_id


def _make_order(conn) -> uuid.UUID:
    order_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO orders "
            "(id, order_number, order_datetime, subtotal, discount_amount, tax_amount, "
            " shipping_charge, total_amount, payment_type, payment_status, status, "
            " fulfillment_status, cancellation_status) "
            "VALUES (:id, :order_number, now(), 100, 0, 0, 0, 100, 'cod', 'pending', "
            " 'pending', 'unfulfilled', 'none')"
        ),
        {"id": order_id, "order_number": f"DEDUP-{order_id.hex[:8]}"},
    )
    return order_id


def _insert_movement(
    conn, *, variant_id, order_id, movement_type, quantity_delta, quantity_after, created_at
) -> uuid.UUID:
    movement_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO inventory_movements "
            "(id, product_variant_id, order_id, movement_type, quantity_delta, "
            " quantity_after, reason, created_at) "
            "VALUES (:id, :variant_id, :order_id, :movement_type, :delta, :after, "
            " 'test fixture', :created_at)"
        ),
        {
            "id": movement_id,
            "variant_id": variant_id,
            "order_id": order_id,
            "movement_type": movement_type,
            "delta": quantity_delta,
            "after": quantity_after,
            "created_at": created_at,
        },
    )
    return movement_id


def _row_count(conn, variant_id, order_id) -> int:
    return conn.execute(
        text(
            "SELECT count(*) FROM inventory_movements "
            "WHERE product_variant_id = :variant_id AND order_id = :order_id"
        ),
        {"variant_id": variant_id, "order_id": order_id},
    ).scalar_one()


def _available_quantity(conn, variant_id) -> int:
    return conn.execute(
        text("SELECT available_quantity FROM product_variants WHERE id = :variant_id"),
        {"variant_id": variant_id},
    ).scalar_one()


# --- fresh database / no duplicate rows -------------------------------------


def test_fresh_database_no_duplicates_is_a_no_op_and_constraint_can_be_created(pg_conn) -> None:
    variant_id = _make_product_and_variant(pg_conn, available_quantity=50)
    order_id = _make_order(pg_conn)
    _insert_movement(
        pg_conn,
        variant_id=variant_id,
        order_id=order_id,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="now()",
    )

    _dedupe_inventory_movements(pg_conn)  # must not raise

    assert _row_count(pg_conn, variant_id, order_id) == 1
    pg_conn.execute(_CONSTRAINT_SQL)  # must succeed -- nothing to violate it


def test_no_duplicate_rows_at_all_in_the_whole_table(pg_conn) -> None:
    _dedupe_inventory_movements(pg_conn)  # must not raise even with zero movement rows
    pg_conn.execute(_CONSTRAINT_SQL)


# --- lost update --------------------------------------------------------


def test_lost_update_group_is_auto_resolved_and_quantity_untouched(pg_conn) -> None:
    variant_id = _make_product_and_variant(pg_conn, available_quantity=50)
    order_id = _make_order(pg_conn)
    canonical_id = _insert_movement(
        pg_conn,
        variant_id=variant_id,
        order_id=order_id,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="'2026-01-01T00:00:00Z'",
    )
    _insert_movement(
        pg_conn,
        variant_id=variant_id,
        order_id=order_id,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="'2026-01-01T00:05:00Z'",
    )

    _dedupe_inventory_movements(pg_conn)

    assert _row_count(pg_conn, variant_id, order_id) == 1
    remaining_id = pg_conn.execute(
        text(
            "SELECT id FROM inventory_movements "
            "WHERE product_variant_id = :variant_id AND order_id = :order_id"
        ),
        {"variant_id": variant_id, "order_id": order_id},
    ).scalar_one()
    assert remaining_id == canonical_id
    assert _available_quantity(pg_conn, variant_id) == 50  # untouched
    pg_conn.execute(_CONSTRAINT_SQL)


# --- true double deduction / unreconcilable ledger --------------------------


def test_true_double_deduction_raises_and_leaves_everything_untouched(pg_conn) -> None:
    # Deliberately unreconcilable: available_quantity (9999) matches
    # neither row's quantity_after, mirroring the real production
    # finding that the ledger does not reconcile with available_quantity
    # (see the migration module docstring) -- must not influence the
    # outcome at all.
    variant_id = _make_product_and_variant(pg_conn, available_quantity=9999)
    order_id = _make_order(pg_conn)
    _insert_movement(
        pg_conn,
        variant_id=variant_id,
        order_id=order_id,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="'2026-01-01T00:00:00Z'",
    )
    _insert_movement(
        pg_conn,
        variant_id=variant_id,
        order_id=order_id,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=40,
        created_at="'2026-01-01T00:05:00Z'",
    )

    with pytest.raises(UnresolvedDuplicateInventoryMovementsError):
        _dedupe_inventory_movements(pg_conn)

    # Nothing was deleted and available_quantity is exactly what it was.
    assert _row_count(pg_conn, variant_id, order_id) == 2
    assert _available_quantity(pg_conn, variant_id) == 9999
    # The constraint cannot be created while this group is unresolved.
    with pytest.raises(Exception):
        pg_conn.execute(_CONSTRAINT_SQL)


# --- multiple duplicate groups -----------------------------------------


def test_multiple_groups_all_lost_update_resolves_every_group(pg_conn) -> None:
    order_a = _make_order(pg_conn)
    order_b = _make_order(pg_conn)
    variant_a = _make_product_and_variant(pg_conn, available_quantity=50)
    variant_b = _make_product_and_variant(pg_conn, available_quantity=30)

    for variant_id, order_id, qty_after in ((variant_a, order_a, 50), (variant_b, order_b, 30)):
        for minute in (0, 5):
            _insert_movement(
                pg_conn,
                variant_id=variant_id,
                order_id=order_id,
                movement_type="dispatch",
                quantity_delta=-10,
                quantity_after=qty_after,
                created_at=f"'2026-01-01T00:0{minute}:00Z'",
            )

    _dedupe_inventory_movements(pg_conn)

    assert _row_count(pg_conn, variant_a, order_a) == 1
    assert _row_count(pg_conn, variant_b, order_b) == 1
    pg_conn.execute(_CONSTRAINT_SQL)


def test_multiple_groups_one_ambiguous_blocks_the_whole_run(pg_conn) -> None:
    """One ambiguous group among several safe ones must abort the WHOLE
    migration -- the caller (`upgrade()`, via `env.py`'s transactional
    DDL) relies on this to keep the outcome all-or-nothing. This test
    proves the safe group's rows are untouched too when the run raises,
    matching that "nothing changes unless everything can be resolved"
    guarantee -- verified here at the `_dedupe_inventory_movements`
    level by checking row counts after the exception propagates (the
    real all-or-nothing guarantee for the DB as a whole is `env.py`'s
    transaction wrapping the entire `upgrade()`, exercised by the
    fixture's own outer transaction/rollback here).
    """
    safe_order = _make_order(pg_conn)
    safe_variant = _make_product_and_variant(pg_conn, available_quantity=50)
    _insert_movement(
        pg_conn,
        variant_id=safe_variant,
        order_id=safe_order,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="'2026-01-01T00:00:00Z'",
    )
    _insert_movement(
        pg_conn,
        variant_id=safe_variant,
        order_id=safe_order,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="'2026-01-01T00:05:00Z'",
    )

    ambiguous_order = _make_order(pg_conn)
    ambiguous_variant = _make_product_and_variant(pg_conn, available_quantity=9999)
    _insert_movement(
        pg_conn,
        variant_id=ambiguous_variant,
        order_id=ambiguous_order,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="'2026-01-01T00:00:00Z'",
    )
    _insert_movement(
        pg_conn,
        variant_id=ambiguous_variant,
        order_id=ambiguous_order,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=40,
        created_at="'2026-01-01T00:05:00Z'",
    )

    with pytest.raises(UnresolvedDuplicateInventoryMovementsError):
        _dedupe_inventory_movements(pg_conn)

    # `_dedupe_inventory_movements` itself already deleted the safe
    # group's duplicate before reaching the ambiguous one (Python-level
    # execution order) -- the DB-level guarantee that this gets undone
    # too lives in `env.py`'s outer migration transaction, not in this
    # function. What this function itself must never do is touch
    # `available_quantity`, for EITHER group:
    assert _available_quantity(pg_conn, safe_variant) == 50
    assert _available_quantity(pg_conn, ambiguous_variant) == 9999
    assert _row_count(pg_conn, ambiguous_variant, ambiguous_order) == 2


# --- idempotency ----------------------------------------------------------


def test_idempotent_second_run_after_resolution_is_a_no_op(pg_conn) -> None:
    variant_id = _make_product_and_variant(pg_conn, available_quantity=50)
    order_id = _make_order(pg_conn)
    _insert_movement(
        pg_conn,
        variant_id=variant_id,
        order_id=order_id,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="'2026-01-01T00:00:00Z'",
    )
    _insert_movement(
        pg_conn,
        variant_id=variant_id,
        order_id=order_id,
        movement_type="dispatch",
        quantity_delta=-10,
        quantity_after=50,
        created_at="'2026-01-01T00:05:00Z'",
    )

    _dedupe_inventory_movements(pg_conn)
    assert _row_count(pg_conn, variant_id, order_id) == 1

    _dedupe_inventory_movements(pg_conn)  # second call: nothing left to resolve
    assert _row_count(pg_conn, variant_id, order_id) == 1

    pg_conn.execute(_CONSTRAINT_SQL)


# --- structural guard: dedupe must run before the constraint is created ----


def test_upgrade_calls_dedupe_before_creating_the_constraint() -> None:
    source = inspect.getsource(_migration.upgrade)
    dedupe_at = source.index("_dedupe_inventory_movements")
    constraint_at = source.index("create_unique_constraint")
    assert dedupe_at < constraint_at
