"""Tests for `collapse_duplicate_inventory_movements`, the data-cleanup step
migration `c9f4a2e6b813` runs before creating the
`uq_inventory_movements_variant_order_type` unique constraint.

The helper lives *inside* the migration module (migrations here never call
into mutable `app.*` logic), so it is loaded through the Alembic
ScriptDirectory rather than imported directly.

Uses a throwaway in-memory SQLite table WITHOUT the unique constraint (so
duplicates can be seeded), mirroring the migration's real "dedup, THEN
constrain" order. The helper itself is plain dialect-portable SQL, so this
coverage carries over to production PostgreSQL.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory


def _load_migration():
    here = pathlib.Path(__file__).resolve().parents[1]
    config = Config(str(here / "alembic.ini"))
    config.set_main_option("script_location", str(here / "alembic"))
    script = ScriptDirectory.from_config(config)
    return script, script.get_revision("c9f4a2e6b813").module


_SCRIPT, _MIGRATION = _load_migration()
collapse_duplicate_inventory_movements = _MIGRATION.collapse_duplicate_inventory_movements

_T0 = datetime(2026, 9, 7, 20, 8, 21, tzinfo=UTC)


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(
            sa.text(
                """
                CREATE TABLE product_variants (
                    id VARCHAR PRIMARY KEY,
                    available_quantity INTEGER NOT NULL,
                    packets_per_box INTEGER NOT NULL DEFAULT 1
                )
                """
            )
        )
        # Deliberately no UNIQUE (product_variant_id, order_id, movement_type)
        # here -- that is exactly the constraint the migration adds AFTER
        # this cleanup runs.
        c.execute(
            sa.text(
                """
                CREATE TABLE inventory_movements (
                    id VARCHAR PRIMARY KEY,
                    product_variant_id VARCHAR NOT NULL,
                    movement_type VARCHAR NOT NULL,
                    quantity_delta INTEGER NOT NULL,
                    quantity_after INTEGER NOT NULL,
                    order_id VARCHAR,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
    with engine.connect() as c:
        yield c
        c.rollback()
    engine.dispose()


def _variant(conn, *, available_quantity: int) -> str:
    vid = str(uuid.uuid4())
    conn.execute(
        sa.text(
            "INSERT INTO product_variants (id, available_quantity, packets_per_box) "
            "VALUES (:id, :aq, 1)"
        ),
        {"id": vid, "aq": available_quantity},
    )
    return vid


def _movement(
    conn,
    *,
    variant_id: str,
    order_id: str | None,
    movement_type: str = "dispatch",
    quantity_delta: int = -1,
    quantity_after: int = 0,
    created_at: datetime | None = None,
) -> str:
    mid = str(uuid.uuid4())
    conn.execute(
        sa.text(
            """
            INSERT INTO inventory_movements
                (id, product_variant_id, movement_type, quantity_delta,
                 quantity_after, order_id, created_at)
            VALUES (:id, :pv, :mt, :qd, :qa, :oid, :ca)
            """
        ),
        {
            "id": mid,
            "pv": variant_id,
            "mt": movement_type,
            "qd": quantity_delta,
            "qa": quantity_after,
            "oid": order_id,
            # ISO string: lexically ordered, and avoids the py3.12 sqlite
            # datetime-adapter deprecation warning. Only the ordering
            # matters to the helper under test.
            "ca": (created_at or datetime.now(UTC)).isoformat(),
        },
    )
    return mid


def _movement_rows(conn) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            sa.text(
                "SELECT id, product_variant_id, order_id, movement_type, "
                "quantity_delta, quantity_after FROM inventory_movements "
                "ORDER BY created_at ASC, id ASC"
            )
        ).mappings()
    ]


def _available_quantity(conn, variant_id: str) -> int:
    return conn.execute(
        sa.text("SELECT available_quantity FROM product_variants WHERE id = :id"),
        {"id": variant_id},
    ).scalar_one()


# --- identical-quantity_after race duplicates collapse to one row -----------


def test_identical_quantity_after_race_collapses_to_one(conn) -> None:
    v = _variant(conn, available_quantity=298)
    order_id = str(uuid.uuid4())
    keeper = _movement(
        conn, variant_id=v, order_id=order_id, quantity_after=298, created_at=_T0
    )
    _movement(
        conn,
        variant_id=v,
        order_id=order_id,
        quantity_after=298,
        created_at=_T0 + timedelta(milliseconds=133),
    )

    result = collapse_duplicate_inventory_movements(conn)

    assert (result.groups_found, result.groups_collapsed, result.rows_deleted) == (1, 1, 1)
    rows = _movement_rows(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == keeper  # earliest created_at survives
    assert rows[0]["quantity_delta"] == -1  # survivor untouched ...
    assert rows[0]["quantity_after"] == 298  # ... so derived previous_balance stays 299


def test_id_tie_break_is_deterministic_when_created_at_is_identical(conn) -> None:
    v = _variant(conn, available_quantity=298)
    order_id = str(uuid.uuid4())
    ids = sorted(str(uuid.uuid4()) for _ in range(3))
    for mid in ids:
        conn.execute(
            sa.text(
                "INSERT INTO inventory_movements (id, product_variant_id, movement_type, "
                "quantity_delta, quantity_after, order_id, created_at) "
                "VALUES (:id, :pv, 'dispatch', -1, 298, :oid, :ca)"
            ),
            {"id": mid, "pv": v, "oid": order_id, "ca": _T0.isoformat()},
        )

    collapse_duplicate_inventory_movements(conn)

    rows = _movement_rows(conn)
    assert [r["id"] for r in rows] == [ids[0]]  # lowest id wins the tie


# --- current available_quantity remains unchanged --------------------------


def test_available_quantity_is_never_modified(conn) -> None:
    v = _variant(conn, available_quantity=298)
    order_id = str(uuid.uuid4())
    _movement(conn, variant_id=v, order_id=order_id, quantity_after=298, created_at=_T0)
    _movement(
        conn,
        variant_id=v,
        order_id=order_id,
        quantity_after=298,
        created_at=_T0 + timedelta(milliseconds=100),
    )

    collapse_duplicate_inventory_movements(conn)

    assert _available_quantity(conn, v) == 298


# --- multiple duplicate groups across variants/orders ---------------------


def test_multiple_groups_across_variants_and_orders(conn) -> None:
    """Mirrors the confirmed production shape: two race groups on one
    variant (two different orders), one on another variant.
    """
    v1 = _variant(conn, available_quantity=3379)
    v2 = _variant(conn, available_quantity=298)
    order_a, order_b, order_c = (str(uuid.uuid4()) for _ in range(3))

    keep_a = _movement(conn, variant_id=v1, order_id=order_a, quantity_after=3571, created_at=_T0)
    _movement(
        conn, variant_id=v1, order_id=order_a, quantity_after=3571,
        created_at=_T0 + timedelta(milliseconds=133),
    )
    keep_b = _movement(
        conn, variant_id=v1, order_id=order_b, quantity_after=3570,
        created_at=_T0 + timedelta(minutes=1),
    )
    _movement(
        conn, variant_id=v1, order_id=order_b, quantity_after=3570,
        created_at=_T0 + timedelta(minutes=1, milliseconds=99),
    )
    keep_c = _movement(
        conn, variant_id=v2, order_id=order_c, quantity_after=298,
        created_at=_T0 + timedelta(minutes=2),
    )
    _movement(
        conn, variant_id=v2, order_id=order_c, quantity_after=298,
        created_at=_T0 + timedelta(minutes=2, milliseconds=1),
    )

    result = collapse_duplicate_inventory_movements(conn)

    assert (result.groups_found, result.groups_collapsed, result.rows_deleted) == (3, 3, 3)
    surviving_ids = {r["id"] for r in _movement_rows(conn)}
    assert surviving_ids == {keep_a, keep_b, keep_c}
    assert _available_quantity(conn, v1) == 3379
    assert _available_quantity(conn, v2) == 298


# --- manual adjustments with NULL order_id are untouched -----------------


def test_null_order_id_manual_adjustments_are_never_touched(conn) -> None:
    v = _variant(conn, available_quantity=22)
    # Two manual adjustments for the same variant -- both order_id NULL.
    _movement(
        conn, variant_id=v, order_id=None, movement_type="manual_adjustment",
        quantity_delta=5, quantity_after=25, created_at=_T0,
    )
    _movement(
        conn, variant_id=v, order_id=None, movement_type="manual_adjustment",
        quantity_delta=-3, quantity_after=22, created_at=_T0 + timedelta(days=1),
    )

    result = collapse_duplicate_inventory_movements(conn)

    assert (result.groups_found, result.rows_deleted) == (0, 0)
    assert len(_movement_rows(conn)) == 2
    assert _available_quantity(conn, v) == 22


# --- non-duplicate movements are untouched ------------------------------


def test_non_duplicate_movements_are_untouched(conn) -> None:
    v = _variant(conn, available_quantity=10)
    order_id = str(uuid.uuid4())
    # One dispatch + one RTO restock for the same order (different
    # movement_type -> not a duplicate) + one NULL-order manual adjustment.
    _movement(conn, variant_id=v, order_id=order_id, movement_type="dispatch",
              quantity_delta=-2, quantity_after=8, created_at=_T0)
    _movement(conn, variant_id=v, order_id=order_id, movement_type="rto_restock",
              quantity_delta=2, quantity_after=10, created_at=_T0 + timedelta(days=1))
    _movement(conn, variant_id=v, order_id=None, movement_type="manual_adjustment",
              quantity_delta=0, quantity_after=10, created_at=_T0 + timedelta(days=2))

    result = collapse_duplicate_inventory_movements(conn)

    assert (result.groups_found, result.groups_collapsed, result.rows_deleted) == (0, 0, 0)
    assert len(_movement_rows(conn)) == 3


# --- rerunning cleanup is a no-op --------------------------------------


def test_rerun_is_a_noop(conn) -> None:
    v = _variant(conn, available_quantity=10)
    order_id = str(uuid.uuid4())
    _movement(conn, variant_id=v, order_id=order_id, quantity_after=10, created_at=_T0)
    _movement(
        conn, variant_id=v, order_id=order_id, quantity_after=10,
        created_at=_T0 + timedelta(milliseconds=50),
    )

    first = collapse_duplicate_inventory_movements(conn)
    assert first.rows_deleted == 1

    second = collapse_duplicate_inventory_movements(conn)
    assert (second.groups_found, second.groups_collapsed, second.rows_deleted) == (0, 0, 0)
    assert len(_movement_rows(conn)) == 1


# --- a duplicate group with different quantity_after aborts, no deletion --


def test_ambiguous_group_aborts_before_any_deletion(conn) -> None:
    v = _variant(conn, available_quantity=299)
    order_id = str(uuid.uuid4())
    _movement(conn, variant_id=v, order_id=order_id, quantity_after=300, created_at=_T0)
    _movement(
        conn, variant_id=v, order_id=order_id, quantity_after=299,
        created_at=_T0 + timedelta(seconds=5),
    )

    with pytest.raises(RuntimeError, match="disagree on quantity_after"):
        collapse_duplicate_inventory_movements(conn)

    assert len(_movement_rows(conn)) == 2  # nothing deleted
    assert _available_quantity(conn, v) == 299


def test_one_ambiguous_group_blocks_even_the_clean_ones(conn) -> None:
    v = _variant(conn, available_quantity=10)
    clean_order = str(uuid.uuid4())
    bad_order = str(uuid.uuid4())
    # Clean race group -- would collapse fine on its own.
    _movement(conn, variant_id=v, order_id=clean_order, quantity_after=10, created_at=_T0)
    _movement(
        conn, variant_id=v, order_id=clean_order, quantity_after=10,
        created_at=_T0 + timedelta(milliseconds=40),
    )
    # Ambiguous group -- forces the whole migration to abort.
    _movement(conn, variant_id=v, order_id=bad_order, quantity_after=9, created_at=_T0)
    _movement(
        conn, variant_id=v, order_id=bad_order, quantity_after=8,
        created_at=_T0 + timedelta(milliseconds=40),
    )

    with pytest.raises(RuntimeError, match="disagree on quantity_after"):
        collapse_duplicate_inventory_movements(conn)

    assert len(_movement_rows(conn)) == 4  # NOTHING deleted, not even the clean group


# --- migration file guards --------------------------------------------


def _migration_module():
    return _SCRIPT, _MIGRATION


def test_migration_down_revision_is_unchanged() -> None:
    script, _module = _migration_module()

    rev = script.get_revision("c9f4a2e6b813")
    assert rev.down_revision == "b7d2e5a91c3f"

    b7 = script.get_revision("b7d2e5a91c3f")
    assert b7.down_revision == "a4e9d3c7f158"

    merge = script.get_revision("d4e5f6a7b8c9")
    assert set(merge.down_revision) == {"c1d4f8a63b57", "c9f4a2e6b813"}


# --- the real migration upgrade() glue, end to end -----------------------


def test_upgrade_collapses_duplicates_then_creates_the_constraint(conn, monkeypatch) -> None:
    """Runs the actual `c9f4a2e6b813.upgrade()` against a live connection.

    `op.create_unique_constraint` is stubbed because SQLite cannot ALTER-add
    a constraint (a pre-existing repo-wide limitation -- the suite never runs
    the alembic chain); the assertion is that it is called, with the right
    columns, only AFTER the row cleanup has run.
    """
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    _script, module = _migration_module()

    v = _variant(conn, available_quantity=298)
    order_id = str(uuid.uuid4())
    keeper = _movement(
        conn, variant_id=v, order_id=order_id, quantity_after=298, created_at=_T0
    )
    _movement(
        conn, variant_id=v, order_id=order_id, quantity_after=298,
        created_at=_T0 + timedelta(milliseconds=90),
    )

    calls: list[tuple] = []

    def _fake_create_unique_constraint(self, constraint_name, table_name, columns, **kw):  # noqa: ANN001
        # cleanup must already have happened by the time the constraint goes on
        assert len(_movement_rows(conn)) == 1
        calls.append((constraint_name, table_name, list(columns)))

    monkeypatch.setattr(
        Operations, "create_unique_constraint", _fake_create_unique_constraint
    )

    ctx = MigrationContext.configure(connection=conn)
    with Operations.context(ctx):
        module.upgrade()

    assert calls == [
        (
            "uq_inventory_movements_variant_order_type",
            "inventory_movements",
            ["product_variant_id", "order_id", "movement_type"],
        )
    ]
    rows = _movement_rows(conn)
    assert [r["id"] for r in rows] == [keeper]
    assert _available_quantity(conn, v) == 298


def test_upgrade_refuses_to_run_in_offline_sql_mode(conn, monkeypatch) -> None:
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    _script, module = _migration_module()

    v = _variant(conn, available_quantity=298)
    order_id = str(uuid.uuid4())
    _movement(conn, variant_id=v, order_id=order_id, quantity_after=298, created_at=_T0)
    _movement(
        conn, variant_id=v, order_id=order_id, quantity_after=298,
        created_at=_T0 + timedelta(milliseconds=90),
    )

    ctx = MigrationContext.configure(connection=conn)
    monkeypatch.setattr(ctx, "as_sql", True)

    with Operations.context(ctx), pytest.raises(RuntimeError, match="must run online"):
        module.upgrade()

    assert len(_movement_rows(conn)) == 2  # aborted before touching anything
