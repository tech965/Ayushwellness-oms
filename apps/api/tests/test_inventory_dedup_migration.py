"""Unit tests for the pure duplicate-classification logic inside the
`c9f4a2e6b813` migration (`_classify_duplicate_group`).

These deliberately do NOT touch a database -- `_classify_duplicate_group`
takes plain `MovementRow` values and returns a `GroupPlan`, with zero DB
dependency by construction (see that module's docstring for why: it must
be structurally impossible for this function to base a decision on
`product_variants.available_quantity`, since production data has proven
the ledger does not reconcile with it). Loaded via `importlib` from the
migration file directly, rather than a regular import, because Alembic
version files are intentionally not part of the `app` package (or any
importable package -- there is no `alembic/versions/__init__.py`) and
must stay self-contained/frozen, not depend on application code that can
change out from under an old migration.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "c9f4a2e6b813_inventory_movement_race_constraint.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("c9f4a2e6b813_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_migration = _load_migration_module()
MovementRow = _migration.MovementRow
GroupPlan = _migration.GroupPlan
_classify_duplicate_group = _migration._classify_duplicate_group


def _row(quantity_delta: int, quantity_after: int) -> MovementRow:
    return MovementRow(id=uuid.uuid4(), quantity_delta=quantity_delta, quantity_after=quantity_after)


# --- lost update ------------------------------------------------------------


def test_lost_update_two_rows_deletes_the_non_canonical_one() -> None:
    canonical = _row(quantity_delta=-10, quantity_after=50)
    duplicate = _row(quantity_delta=-10, quantity_after=50)

    plan = _classify_duplicate_group([canonical, duplicate])

    assert plan.action == "delete_duplicates"
    assert plan.canonical_id == canonical.id
    assert plan.duplicate_ids_to_delete == (duplicate.id,)


def test_lost_update_three_rows_keeps_only_the_earliest() -> None:
    canonical = _row(quantity_delta=-10, quantity_after=50)
    dup_1 = _row(quantity_delta=-10, quantity_after=50)
    dup_2 = _row(quantity_delta=-10, quantity_after=50)

    plan = _classify_duplicate_group([canonical, dup_1, dup_2])

    assert plan.action == "delete_duplicates"
    assert plan.canonical_id == canonical.id
    assert set(plan.duplicate_ids_to_delete) == {dup_1.id, dup_2.id}


def test_lost_update_never_produces_any_quantity_correction() -> None:
    """The `GroupPlan` type itself has no field for a quantity change --
    this asserts the actual contract (delete-only) rather than just the
    absence of a field, so a future refactor that re-adds a quantity
    write would have to change this test too.
    """
    canonical = _row(quantity_delta=5, quantity_after=20)
    duplicate = _row(quantity_delta=5, quantity_after=20)

    plan = _classify_duplicate_group([canonical, duplicate])

    assert plan.action == "delete_duplicates"
    assert not hasattr(plan, "corrected_quantity")
    assert not hasattr(plan, "new_available_quantity")


# --- true double deduction / ambiguous --------------------------------------


def test_true_double_deduction_is_flagged_action_required_not_deleted() -> None:
    canonical = _row(quantity_delta=-10, quantity_after=50)
    later_deduction = _row(quantity_delta=-10, quantity_after=40)

    plan = _classify_duplicate_group([canonical, later_deduction])

    assert plan.action == "action_required"
    assert plan.canonical_id == canonical.id
    assert plan.duplicate_ids_to_delete == ()


def test_ambiguous_group_with_some_matching_and_some_differing_rows_is_still_flagged() -> None:
    """A mixed group -- some rows genuinely identical to canonical, one
    that isn't -- must not be partially auto-cleaned. Only a pure,
    unanimous lost-update group is safe to touch at all.
    """
    canonical = _row(quantity_delta=-10, quantity_after=50)
    exact_duplicate = _row(quantity_delta=-10, quantity_after=50)
    diverging = _row(quantity_delta=-10, quantity_after=40)

    plan = _classify_duplicate_group([canonical, exact_duplicate, diverging])

    assert plan.action == "action_required"
    assert plan.duplicate_ids_to_delete == ()


def test_rto_restock_double_restock_is_also_flagged_not_corrected() -> None:
    """Same ambiguity applies in the +boxes (RTO restock) direction, not
    just -boxes (dispatch) -- the classification only looks at whether
    quantity_after values match, not the sign of quantity_delta.
    """
    canonical = _row(quantity_delta=10, quantity_after=60)
    later_restock = _row(quantity_delta=10, quantity_after=70)

    plan = _classify_duplicate_group([canonical, later_restock])

    assert plan.action == "action_required"


# --- unreconcilable ledger is irrelevant to this function -------------------


def test_classification_has_no_way_to_receive_available_quantity() -> None:
    """`MovementRow` structurally excludes any variant-level field --
    proves the classification decision cannot depend on
    `product_variants.available_quantity` (or its reconciliation against
    the ledger) even in principle, matching the production incident
    finding that the ledger does not reconcile with it and must never be
    trusted to justify an automatic quantity correction.
    """
    field_names = set(MovementRow._fields)
    assert "quantity_after" in field_names
    assert "quantity_delta" in field_names
    assert not any("available" in f or "quantity_current" in f for f in field_names)


# --- multiple groups (integration of the classifier is exercised in the
# live-Postgres test module; this just confirms independence between
# groups at the pure-function level) ----------------------------------------


def test_two_independent_groups_classified_independently() -> None:
    lost_update_group = [
        _row(quantity_delta=-5, quantity_after=10),
        _row(quantity_delta=-5, quantity_after=10),
    ]
    ambiguous_group = [
        _row(quantity_delta=-5, quantity_after=10),
        _row(quantity_delta=-5, quantity_after=5),
    ]

    plan_a = _classify_duplicate_group(lost_update_group)
    plan_b = _classify_duplicate_group(ambiguous_group)

    assert plan_a.action == "delete_duplicates"
    assert plan_b.action == "action_required"


# --- input validation ---------------------------------------------------


def test_single_row_is_rejected_not_silently_treated_as_a_group() -> None:
    with pytest.raises(ValueError):
        _classify_duplicate_group([_row(quantity_delta=-5, quantity_after=10)])
