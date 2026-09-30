"""simplify telecalling_status enum

Revision ID: f3a7c9e1b6d2
Revises: d9a4b7c2e5f1
Create Date: 2026-09-30 00:00:00.000000

Review meeting, 2026-09-30 ("Simplify Telecaller Call Status"): trims
`TelecallingStatus` (`app/models/enums.py`) from 13 to 10 values, used by
`order_assignments.current_status`, `call_attempts.outcome`,
`checkout_assignments.current_status`, and `checkout_call_attempts.outcome`.

Removed: call_attempted, connected, not_received, invalid_number,
call_back_requested, follow_up_required.
Added:   not_answering, call_back_later, other.
Kept unchanged: not_called, busy, switched_off, interested, not_interested,
confirmed, cancelled.

Existing rows are remapped (never dropped) per `_VALUE_MAP` below:
  call_attempted, not_received  -> not_answering  (closest real match)
  call_back_requested,
  follow_up_required            -> call_back_later (closest real match)
  connected, invalid_number     -> other           (no direct successor --
                                                      the original value is
                                                      preserved in `notes`
                                                      first, see below)

Postgres-only (this project's only real deployment target -- see
`alembic/env.py`'s `_sync_database_url`); the test suite creates its
schema straight from the current model code (`Base.metadata.create_all`
on SQLite), so it never runs this migration at all.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f3a7c9e1b6d2"
down_revision = "d9a4b7c2e5f1"
branch_labels = None
depends_on = None

OLD_VALUES = (
    "not_called",
    "call_attempted",
    "connected",
    "not_received",
    "busy",
    "switched_off",
    "invalid_number",
    "call_back_requested",
    "interested",
    "not_interested",
    "follow_up_required",
    "confirmed",
    "cancelled",
)

NEW_VALUES = (
    "not_called",
    "not_answering",
    "busy",
    "switched_off",
    "call_back_later",
    "interested",
    "not_interested",
    "confirmed",
    "cancelled",
    "other",
)

_UPGRADE_VALUE_MAP = {
    "call_attempted": "not_answering",
    "not_received": "not_answering",
    "call_back_requested": "call_back_later",
    "follow_up_required": "call_back_later",
    "connected": "other",
    "invalid_number": "other",
}

# Lossy but reasonable: new-only values have no old-vocabulary equivalent,
# so downgrade maps them to the closest pre-existing status rather than
# failing outright.
_DOWNGRADE_VALUE_MAP = {
    "not_answering": "not_received",
    "call_back_later": "call_back_requested",
    "other": "call_attempted",
}

# Tables (+ column) sharing the `telecalling_status` enum type.
_COLUMNS = (
    ("order_assignments", "current_status"),
    ("call_attempts", "outcome"),
    ("checkout_assignments", "current_status"),
    ("checkout_call_attempts", "outcome"),
)

# The two outcome tables that have a free-text `notes` column to preserve
# the discarded original value in, for the two values collapsing into
# "other" with no direct successor.
_NOTES_TABLES = ("call_attempts", "checkout_call_attempts")

_OTHER_SOURCE_LABELS = {"connected": "Connected", "invalid_number": "Invalid Number"}


def _case_sql(column: str, mapping: dict[str, str]) -> str:
    whens = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in mapping.items())
    return f"(CASE {column}::text {whens} ELSE {column}::text END)"


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Before the outcome/current_status columns change type, preserve
    #    the pre-migration value for "connected"/"invalid_number" (the two
    #    outcomes with no direct new-vocabulary successor) inside the
    #    existing `notes` text -- otherwise that detail is lost the moment
    #    the CASE mapping below collapses them into "other".
    for table in _NOTES_TABLES:
        for old_value, label in _OTHER_SOURCE_LABELS.items():
            op.execute(
                f"""
                UPDATE {table}
                SET notes = TRIM(
                    COALESCE(notes, '') ||
                    CASE WHEN notes IS NULL OR notes = '' THEN '' ELSE ' ' END ||
                    '(migrated from: {label})'
                )
                WHERE outcome::text = '{old_value}'
                """
            )

    # 2) Create the new enum type (a fresh name -- Postgres can't ADD/DROP
    #    enum values transactionally in one migration in a way that also
    #    lets existing rows be remapped, so recreate-and-swap is the safe,
    #    standard approach).
    new_type = postgresql.ENUM(*NEW_VALUES, name="telecalling_status_new")
    new_type.create(bind, checkfirst=True)

    # 3) Move every column to the new type, remapping removed values in
    #    the same USING cast that changes the column's type.
    for table, column in _COLUMNS:
        op.execute(
            f"""
            ALTER TABLE {table}
            ALTER COLUMN {column} TYPE telecalling_status_new
            USING {_case_sql(column, _UPGRADE_VALUE_MAP)}::telecalling_status_new
            """
        )

    # 4) Drop the old type and rename the new one into its place, so the
    #    type name (and therefore every model's `sa_enum(..., "telecalling_
    #    status")` declaration) is unchanged going forward.
    postgresql.ENUM(name="telecalling_status").drop(bind, checkfirst=True)
    op.execute("ALTER TYPE telecalling_status_new RENAME TO telecalling_status")


def downgrade() -> None:
    bind = op.get_bind()

    old_type = postgresql.ENUM(*OLD_VALUES, name="telecalling_status_old")
    old_type.create(bind, checkfirst=True)

    for table, column in _COLUMNS:
        op.execute(
            f"""
            ALTER TABLE {table}
            ALTER COLUMN {column} TYPE telecalling_status_old
            USING {_case_sql(column, _DOWNGRADE_VALUE_MAP)}::telecalling_status_old
            """
        )

    postgresql.ENUM(name="telecalling_status").drop(bind, checkfirst=True)
    op.execute("ALTER TYPE telecalling_status_old RENAME TO telecalling_status")
