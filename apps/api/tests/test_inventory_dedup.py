"""Tests for the duplicate-cleanup step of migration `c9f4a2e6b813`.

The migration (see its module docstring) exposes a pure classifier
`_classify_duplicate_group` and `_dedupe_inventory_movements(bind)`, which
deletes only provably-redundant rows (identical `quantity_after` across the
group), never touches `product_variants.available_quantity`, and raises
`UnresolvedDuplicateInventoryMovementsError` if any group is ambiguous
(the surrounding Alembic transaction then rolls everything back).

The delete uses PostgreSQL `= ANY(:ids)`, which SQLite cannot execute, so the
DB layer is exercised through a small in-memory fake connection that records
every statement. The migration file itself is loaded through Alembic's
ScriptDirectory (migrations never import mutable `app.*` logic) and is NOT
modified by these tests.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


def _load_migration():
    here = pathlib.Path(__file__).resolve().parents[1]
    config = Config(str(here / "alembic.ini"))
    config.set_main_option("script_location", str(here / "alembic"))
    script = ScriptDirectory.from_config(config)
    return script, script.get_revision("c9f4a2e6b813").module


_SCRIPT, _MIGRATION = _load_migration()
classify = _MIGRATION._classify_duplicate_group
dedupe = _MIGRATION._dedupe_inventory_movements
MovementRow = _MIGRATION.MovementRow
Unresolved = _MIGRATION.UnresolvedDuplicateInventoryMovementsError

_T0 = datetime(2026, 9, 7, 20, 8, 21, tzinfo=UTC)


def _row(qd: int = -1, qa: int = 298) -> MovementRow:
    return MovementRow(id=uuid.uuid4(), quantity_delta=qd, quantity_after=qa)


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def fetchall(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class FakeBind:
    """In-memory stand-in for the Alembic connection (dispatches on SQL text)."""

    def __init__(self, movements: list[dict], available: dict[str, int] | None = None):
        # movement: variant, order, type, id, qd, qa, created_at
        self.movements = movements
        self.available = available or {}
        self.deleted: list[uuid.UUID] = []
        self.statements: list[str] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        params = params or {}
        if "GROUP BY" in sql:
            counts: dict[tuple, int] = {}
            for m in self.movements:
                if m["order"] is None:
                    continue
                k = (m["variant"], m["order"], m["type"])
                counts[k] = counts.get(k, 0) + 1
            return _Result(
                [
                    SimpleNamespace(product_variant_id=v, order_id=o, movement_type=t, n=n)
                    for (v, o, t), n in counts.items()
                    if n > 1
                ]
            )
        if "ORDER BY created_at" in sql:
            rows = sorted(
                (
                    m
                    for m in self.movements
                    if (m["variant"], m["order"], m["type"])
                    == (params["variant_id"], params["order_id"], params["movement_type"])
                ),
                key=lambda m: (m["created_at"], str(m["id"])),
            )
            return _Result(
                [
                    SimpleNamespace(
                        id=m["id"],
                        quantity_delta=m["qd"],
                        quantity_after=m["qa"],
                        created_at=m["created_at"],
                    )
                    for m in rows
                ]
            )
        if sql.lstrip().startswith("DELETE"):
            self.deleted.extend(params["ids"])
            self.movements = [m for m in self.movements if m["id"] not in params["ids"]]
            return _Result()
        if "SELECT available_quantity" in sql:
            return _Result(scalar=self.available[params["variant_id"]])
        raise AssertionError(f"unexpected SQL: {sql}")


def _m(variant, order, qa, at, *, type_="dispatch", qd=-1):
    return {
        "variant": variant,
        "order": order,
        "type": type_,
        "id": uuid.uuid4(),
        "qd": qd,
        "qa": qa,
        "created_at": at,
    }


# --- pure classifier -----------------------------------------------------


def test_identical_quantity_after_is_a_provable_race_duplicate() -> None:
    rows = [_row(qa=298), _row(qa=298), _row(qa=298)]
    plan = classify(rows)
    assert plan.action == "delete_duplicates"
    assert plan.canonical_id == rows[0].id  # earliest row is canonical
    assert plan.duplicate_ids_to_delete == (rows[1].id, rows[2].id)


def test_differing_quantity_after_is_action_required_and_deletes_nothing() -> None:
    plan = classify([_row(qa=300), _row(qa=299)])
    assert plan.action == "action_required"
    assert plan.duplicate_ids_to_delete == ()


def test_classifier_requires_two_rows() -> None:
    with pytest.raises(ValueError):
        classify([_row()])


# --- _dedupe_inventory_movements ----------------------------------------


def test_race_duplicates_collapse_to_the_canonical_row_only() -> None:
    order = str(uuid.uuid4())
    first = _m("v1", order, 298, _T0)
    dup = _m("v1", order, 298, _T0 + timedelta(milliseconds=133))
    bind = FakeBind([first, dup], {"v1": 298})

    dedupe(bind)

    assert bind.deleted == [dup["id"]]
    assert [m["id"] for m in bind.movements] == [first["id"]]


def test_available_quantity_is_never_written() -> None:
    order = str(uuid.uuid4())
    bind = FakeBind(
        [_m("v1", order, 298, _T0), _m("v1", order, 298, _T0 + timedelta(seconds=1))],
        {"v1": 298},
    )
    dedupe(bind)
    assert not any("UPDATE" in s.upper() for s in bind.statements)
    assert not any("product_variants" in s and "SELECT" not in s for s in bind.statements)


def test_multiple_groups_across_variants_and_orders() -> None:
    oa, ob, oc = (str(uuid.uuid4()) for _ in range(3))
    keepers = [
        _m("v1", oa, 3571, _T0),
        _m("v1", ob, 3570, _T0 + timedelta(minutes=1)),
        _m("v2", oc, 298, _T0 + timedelta(minutes=2)),
    ]
    dups = [
        _m("v1", oa, 3571, _T0 + timedelta(milliseconds=133)),
        _m("v1", ob, 3570, _T0 + timedelta(minutes=1, milliseconds=99)),
        _m("v2", oc, 298, _T0 + timedelta(minutes=2, milliseconds=1)),
    ]
    bind = FakeBind(keepers + dups)
    dedupe(bind)
    assert sorted(map(str, bind.deleted)) == sorted(str(d["id"]) for d in dups)
    assert {m["id"] for m in bind.movements} == {k["id"] for k in keepers}


def test_null_order_manual_adjustments_and_unique_rows_are_untouched() -> None:
    order = str(uuid.uuid4())
    movements = [
        _m("v1", None, 25, _T0, type_="manual_adjustment", qd=5),
        _m("v1", None, 22, _T0 + timedelta(days=1), type_="manual_adjustment", qd=-3),
        _m("v1", order, 8, _T0, type_="dispatch", qd=-2),
        _m("v1", order, 10, _T0 + timedelta(days=1), type_="rto_restock", qd=2),
    ]
    bind = FakeBind(list(movements))
    dedupe(bind)
    assert bind.deleted == []
    assert len(bind.movements) == 4


def test_rerun_is_a_noop() -> None:
    order = str(uuid.uuid4())
    bind = FakeBind([_m("v1", order, 10, _T0), _m("v1", order, 10, _T0 + timedelta(seconds=1))])
    dedupe(bind)
    assert len(bind.deleted) == 1
    dedupe(bind)
    assert len(bind.deleted) == 1  # second run found nothing


def test_ambiguous_group_raises_and_deletes_nothing_for_that_group() -> None:
    order = str(uuid.uuid4())
    a = _m("v1", order, 300, _T0)
    b = _m("v1", order, 299, _T0 + timedelta(seconds=5))
    bind = FakeBind([a, b], {"v1": 299})
    with pytest.raises(Unresolved, match="require manual"):
        dedupe(bind)
    assert bind.deleted == []
    assert len(bind.movements) == 2


def test_one_ambiguous_group_aborts_the_whole_run() -> None:
    """Clean groups may already have been deleted in-transaction when the
    ambiguous group is hit; the raise makes Alembic roll the transaction
    back, so the migration is all-or-nothing."""
    clean, bad = str(uuid.uuid4()), str(uuid.uuid4())
    bind = FakeBind(
        [
            _m("v1", clean, 10, _T0),
            _m("v1", clean, 10, _T0 + timedelta(milliseconds=40)),
            _m("v1", bad, 9, _T0),
            _m("v1", bad, 8, _T0 + timedelta(milliseconds=40)),
        ],
        {"v1": 8},
    )
    with pytest.raises(Unresolved):
        dedupe(bind)
    assert not any("UPDATE" in s.upper() for s in bind.statements)


# --- migration file guards ----------------------------------------------


def test_migration_down_revision_is_unchanged() -> None:
    rev = _SCRIPT.get_revision("c9f4a2e6b813")
    assert rev.down_revision == "b7d2e5a91c3f"
    assert _SCRIPT.get_revision("b7d2e5a91c3f").down_revision == "a4e9d3c7f158"
    merge = _SCRIPT.get_revision("d4e5f6a7b8c9")
    assert set(merge.down_revision) == {"c1d4f8a63b57", "c9f4a2e6b813"}


def test_upgrade_dedupes_then_creates_the_constraint(monkeypatch) -> None:
    from alembic.operations import Operations

    order_of_calls: list[str] = []
    monkeypatch.setattr(_MIGRATION.op, "get_bind", lambda: "BIND", raising=False)
    monkeypatch.setattr(
        _MIGRATION,
        "_dedupe_inventory_movements",
        lambda bind: order_of_calls.append(f"dedupe:{bind}"),
    )
    monkeypatch.setattr(
        _MIGRATION.op,
        "create_unique_constraint",
        lambda name, table, cols, **kw: order_of_calls.append(f"constraint:{name}:{table}:{cols}"),
        raising=False,
    )
    _MIGRATION.upgrade()
    assert order_of_calls == [
        "dedupe:BIND",
        "constraint:uq_inventory_movements_variant_order_type:inventory_movements:"
        "['product_variant_id', 'order_id', 'movement_type']",
    ]
    assert Operations  # imported to confirm alembic.operations is available
