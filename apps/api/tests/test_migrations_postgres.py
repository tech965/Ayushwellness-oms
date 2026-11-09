"""Alembic upgrade/downgrade cycles against a REAL, disposable PostgreSQL.

Skipped unless `OMS_TEST_PG_ADMIN_URL` points at a throwaway server (e.g.
`postgresql://postgres@127.0.0.1:5432/postgres`). The test creates and drops
its own database; never point it at a shared or production server.

Covers: fresh base -> head, repeated head -> base -> head cycles (no leftover
enum types or tables), and a seeded historical upgrade across the
`telecalling_status` enum rewrite.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ADMIN_URL = os.environ.get("OMS_TEST_PG_ADMIN_URL")
API_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="set OMS_TEST_PG_ADMIN_URL to a disposable PostgreSQL to run"
)


def _alembic(db_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    base = ADMIN_URL.rsplit("/", 1)[0]  # type: ignore[union-attr]
    env = {
        **os.environ,
        "DATABASE_URL": base.replace("postgresql://", "postgresql+asyncpg://") + f"/{db_name}",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def database():
    import psycopg2

    name = f"oms_mig_{uuid.uuid4().hex[:10]}"
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name}")
    base = ADMIN_URL.rsplit("/", 1)[0]
    try:
        yield name, f"{base}/{name}"
    finally:
        cur = admin.cursor()
        cur.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()


def _schema_objects(url: str) -> tuple[list[str], list[str]]:
    import psycopg2

    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "select typname from pg_type t join pg_namespace n on n.oid=t.typnamespace "
            "where n.nspname='public' and t.typtype='e' order by 1"
        )
        enums = [r[0] for r in cur.fetchall()]
        cur.execute("select tablename from pg_tables where schemaname='public' order by 1")
        return enums, [r[0] for r in cur.fetchall()]


def test_fresh_database_upgrades_to_head_and_cycles_cleanly(database) -> None:
    name, url = database

    first = _alembic(name, "upgrade", "head")
    assert first.returncode == 0, first.stderr[-800:]

    for _ in range(2):
        down = _alembic(name, "downgrade", "base")
        assert down.returncode == 0, down.stderr[-800:]
        enums, tables = _schema_objects(url)
        assert enums == [] and tables == ["alembic_version"], (enums, tables)
        up = _alembic(name, "upgrade", "head")
        assert up.returncode == 0, up.stderr[-800:]


def test_seeded_historical_database_survives_the_enum_rewrite(database) -> None:
    import psycopg2

    name, url = database
    assert _alembic(name, "upgrade", "d9a4b7c2e5f1").returncode == 0
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        uid, oid = str(uuid.uuid4()), str(uuid.uuid4())
        cur.execute(
            "insert into users (id,name,email,password_hash,is_active,is_superuser) "
            "values (%s,'U','u@example.com','x',true,false)",
            (uid,),
        )
        cur.execute(
            "insert into orders (id, order_number, order_datetime, currency, subtotal, "
            "discount_amount, tax_amount, shipping_charge, total_amount, payment_type, "
            "payment_status, status, fulfillment_status, cancellation_status) values "
            "(%s,'#H1',now(),'INR',0,0,0,0,0,'cod','pending','pending','unfulfilled','none')",
            (oid,),
        )
        cur.execute(
            "insert into order_assignments (id, order_id, assigned_to, assigned_at, "
            "assignment_status, current_status) values (%s,%s,%s,now(),'active','connected')",
            (str(uuid.uuid4()), oid, uid),
        )

    assert _alembic(name, "upgrade", "head").returncode == 0

    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select current_status::text from order_assignments")
        assert [r[0] for r in cur.fetchall()] == ["other"]  # remapped, row kept
        cur.execute("select count(*) from order_workflow w join orders o on o.id = w.id")
        assert cur.fetchone()[0] == 1  # workflow row reuses the order id
        cur.execute(
            "select column_default from information_schema.columns "
            "where table_name='order_assignments' and column_name='current_status'"
        )
        assert "not_called" in cur.fetchone()[0]  # default re-applied after the type change
