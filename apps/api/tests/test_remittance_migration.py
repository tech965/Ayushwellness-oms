"""Migration validation for Modules 6 & 7.

Runs the real migration's `upgrade()` / `downgrade()` against a scratch
database and checks: (1) it creates exactly the tables, columns, indexes and
constraints the models declare, (2) it is reversible, (3) it touches no
existing table, (4) there is still a single Alembic head, and (5) the new
permissions are seeded for new databases.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import app.models  # noqa: F401  (register every model)
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.db.base import Base

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = API_ROOT / "alembic" / "versions" / "c9e1a5d3f7b2_remittance_reconciliation.py"
NEW_TABLES = {
    "settlement_files",
    "bank_credits",
    "cod_remittance_lines",
    "gateway_settlement_lines",
    "bank_credit_links",
    "reconciliation_adjustments",
}


LATER_COLUMNS = {"bank_reference"}  # added by e7a1c3b5d9f4


def _load():
    spec = importlib.util.spec_from_file_location("remittance_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sqlite_friendly(module):
    """SQLite has no `now()`; the server defaults are not what is under test."""

    def base() -> list[sa.Column]:
        return [
            sa.Column("id", module.GUID, nullable=False),
            sa.Column("created_at", module.DT, nullable=False),
            sa.Column("updated_at", module.DT, nullable=False),
            sa.PrimaryKeyConstraint("id"),
        ]

    module._base = base
    module.NEW_PERMISSIONS = {}  # the permission INSERT uses Postgres gen_random_uuid()


def test_single_alembic_head_and_revision_chain():
    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    assert script.get_heads() == ["f2b8d4a6c0e1"]
    assert script.get_revision("f2b8d4a6c0e1").down_revision == "e7a1c3b5d9f4"
    assert script.get_revision("e7a1c3b5d9f4").down_revision == "d3b5f7a9c1e2"
    assert script.get_revision("c9e1a5d3f7b2").down_revision == "b4d8e2a6c1f7"
    assert script.get_revision("d3b5f7a9c1e2").down_revision == "c9e1a5d3f7b2"


def test_migration_matches_models_and_is_reversible():
    module = _load()
    _sqlite_friendly(module)
    engine = sa.create_engine("sqlite://")
    later = {"shiprocket_payouts"}  # needs settlement_files, from this migration
    existing = [t for t in Base.metadata.sorted_tables if t.name not in NEW_TABLES | later]
    Base.metadata.create_all(engine, tables=existing)
    before = set(sa.inspect(engine).get_table_names())

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        with Operations.context(ctx):
            module.upgrade()

        inspector = sa.inspect(conn)
        assert set(inspector.get_table_names()) >= NEW_TABLES

        # Models and migration agree on tables, columns, nullability, indexes, FKs, uniques.
        diffs = []
        for diff in compare_metadata(ctx, Base.metadata):
            ops = diff if isinstance(diff, list) else [diff]
            for op in ops:
                if op[0] == "add_column" and op[3].name in LATER_COLUMNS:
                    continue
                name = getattr(op[1], "name", None) or getattr(
                    getattr(op[1], "table", None), "name", None
                )
                if name in NEW_TABLES:
                    diffs.append(op)
        assert diffs == [], diffs

        uniques = {
            u["name"]
            for t in (
                "settlement_files",
                "bank_credits",
                "cod_remittance_lines",
                "gateway_settlement_lines",
            )
            for u in inspector.get_unique_constraints(t)
        }
        assert {
            "uq_settlement_files_module_sha",
            "uq_bank_credits_dedupe_key",
            "uq_cod_remittance_lines_dedupe_key",
            "uq_gateway_settlement_lines_dedupe_key",
        } <= uniques

        with Operations.context(ctx):
            module.downgrade()
        after = set(sa.inspect(conn).get_table_names())
    assert after == before  # fully reversed; no other table was touched


def test_migration_is_additive_only():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "alter_table" not in source and "drop_column" not in source
    assert "op.add_column" not in source and "op.execute" not in source
    # Only the six new tables are ever created or dropped.
    for table in NEW_TABLES:
        assert f'"{table}"' in source


def test_new_permissions_are_declared_for_seeding():
    module = _load()
    assert set(module.NEW_PERMISSIONS) == {"reconciliation.upload", "reconciliation.adjust"}
    seed = (API_ROOT / "scripts" / "seed.py").read_text(encoding="utf-8")
    assert '"reconciliation.upload"' in seed and '"reconciliation.adjust"' in seed


SYNC_MIGRATION = API_ROOT / "alembic" / "versions" / "d3b5f7a9c1e2_gateway_api_sync.py"


def test_gateway_sync_migration_matches_models_and_is_reversible():
    spec = importlib.util.spec_from_file_location("gateway_sync_migration", SYNC_MIGRATION)
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    base_module = _load()
    _sqlite_friendly(base_module)

    engine = sa.create_engine("sqlite://")
    skip = NEW_TABLES | {"gateway_sync_states", "shiprocket_payouts"}
    Base.metadata.create_all(
        engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in skip]
    )
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        with Operations.context(ctx):
            base_module.upgrade()
            # SQLite has no now() server default.
            sync.sa.text = sa.text
            sync.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("gateway_settlement_lines")}
        assert {"source", "cf_settlement_id", "event_type", "synced_at"} <= cols
        assert "gateway_sync_states" in sa.inspect(conn).get_table_names()
        diffs = []
        for diff in compare_metadata(ctx, Base.metadata):
            ops = diff if isinstance(diff, list) else [diff]
            for op in ops:
                name = getattr(op[1], "name", None) or getattr(
                    getattr(op[1], "table", None), "name", None
                )
                if name in {"gateway_settlement_lines", "gateway_sync_states"}:
                    diffs.append(op)
        assert diffs == [], diffs
        with Operations.context(ctx):
            sync.downgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("gateway_settlement_lines")}
        assert not ({"source", "cf_settlement_id", "event_type", "synced_at"} & cols)
        assert "gateway_sync_states" not in sa.inspect(conn).get_table_names()


def test_manual_verification_migration_adds_two_nullable_columns_and_reverses():
    path = API_ROOT / "alembic" / "versions" / "e7a1c3b5d9f4_manual_bank_verification.py"
    spec = importlib.util.spec_from_file_location("manual_verification_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    added = {
        "bank_credit_links": "bank_reference",
        "cod_remittances": "bank_verification_reason",
    }
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        for table in added:
            conn.execute(sa.text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, keep TEXT)"))
            conn.execute(sa.text(f"INSERT INTO {table} (id, keep) VALUES (1, 'x')"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()
        for table, column in added.items():
            info = {c["name"]: c for c in sa.inspect(conn).get_columns(table)}
            assert info[column]["nullable"] is True
            assert conn.execute(sa.text(f"SELECT keep, {column} FROM {table}")).one() == ("x", None)
        with Operations.context(ctx):
            migration.downgrade()
        for table, column in added.items():
            assert column not in {c["name"] for c in sa.inspect(conn).get_columns(table)}


def test_all_three_migrations_upgrade_downgrade_and_upgrade_again_in_order():
    def load(name):
        spec = importlib.util.spec_from_file_location(
            name, API_ROOT / "alembic" / "versions" / name
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    first = _load()
    _sqlite_friendly(first)
    second = load("d3b5f7a9c1e2_gateway_api_sync.py")
    third = load("e7a1c3b5d9f4_manual_bank_verification.py")
    ours = NEW_TABLES | {"gateway_sync_states"}
    # cod_remittances predates these migrations: create it without the new column.
    later = {"shiprocket_shipment_charges", "shiprocket_payouts"}  # f2b8d4a6c0e1
    existing = [
        t for t in Base.metadata.sorted_tables if t.name not in ours | later | {"cod_remittances"}
    ]
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=existing)
    with engine.begin() as conn:
        columns = ", ".join(
            f'"{c.name}" TEXT'
            for c in Base.metadata.tables["cod_remittances"].columns
            if c.name != "bank_verification_reason"
        )
        conn.execute(sa.text(f"CREATE TABLE cod_remittances ({columns})"))
        before = set(sa.inspect(conn).get_table_names())
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        with Operations.context(ctx):
            second.sa.text = sa.text  # SQLite has no now() server default
            for _round in range(2):
                for step in (first, second, third):
                    step.upgrade()
                inspector = sa.inspect(conn)
                assert ours <= set(inspector.get_table_names())
                for table in ours | {"cod_remittances"}:
                    have = {c["name"] for c in inspector.get_columns(table)}
                    assert have == {c.name for c in Base.metadata.tables[table].columns}, table
                for step in (third, second, first):
                    step.downgrade()
                assert set(sa.inspect(conn).get_table_names()) == before


def test_shiprocket_finance_migration_matches_models_and_is_reversible():
    spec = importlib.util.spec_from_file_location(
        "sr_finance", API_ROOT / "alembic" / "versions" / "f2b8d4a6c0e1_shiprocket_finance.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    new = {"shiprocket_shipment_charges", "shiprocket_payouts"}
    # SQLite has no now() server default.
    migration._base = lambda: [
        sa.Column("id", migration.GUID, nullable=False),
        sa.Column("created_at", migration.DT, nullable=False),
        sa.Column("updated_at", migration.DT, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ]
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in new]
    )
    with engine.begin() as conn:
        before = set(sa.inspect(conn).get_table_names())
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        with Operations.context(ctx):
            migration.upgrade()
        assert set(sa.inspect(conn).get_table_names()) == before | new
        diffs = []
        for diff in compare_metadata(ctx, Base.metadata):
            for op in diff if isinstance(diff, list) else [diff]:
                name = getattr(op[1], "name", None) or getattr(
                    getattr(op[1], "table", None), "name", None
                )
                if name in new:
                    diffs.append(op)
        assert diffs == [], diffs
        with Operations.context(ctx):
            migration.downgrade()
        assert set(sa.inspect(conn).get_table_names()) == before
