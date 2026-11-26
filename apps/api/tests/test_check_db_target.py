"""The staging database check must refuse anything it cannot prove is not
production. Made-up hosts only; nothing is contacted.
"""

from __future__ import annotations

import pytest
from scripts.check_db_target import problems, protected_servers, server_id

PROD = "postgresql://u:secret@dpg-prod123-a.oregon-postgres.render.com/oms"
STAGING = "postgresql+asyncpg://u:secret@dpg-stage456-a.oregon-postgres.render.com/oms_staging"
OK_ENV = {"OMS_ENV_FILE": ".env.staging.local"}


def check(url=STAGING, expected="dpg-stage456-a", protected=None, env=None, live=()):
    protected = {server_id(PROD)} if protected is None else protected
    return problems(url, expected, protected, OK_ENV if env is None else env, live)


def test_a_distinct_expected_staging_database_passes():
    assert check() == []
    assert check(expected="dpg-stage456-a.oregon-postgres.render.com") == []


@pytest.mark.parametrize(
    "url",
    [
        PROD,
        "postgresql://u:p@dpg-prod123-a/oms",  # Render internal name
        "postgresql://u:p@DPG-PROD123-A.oregon-postgres.render.com./oms",
        "postgresql://u:p@dpg-prod123-a.oregon-postgres.render.com/another_db",
    ],
)
def test_production_is_refused_however_it_is_written_even_if_expected(url):
    found = check(url=url, expected="dpg-prod123-a")
    assert any("PRODUCTION" in item for item in found)


@pytest.mark.parametrize(
    "change",
    [
        {"expected": ""},
        {"expected": "dpg-other-a"},
        {"protected": set()},
        {"env": {}},
        {"env": {**OK_ENV, "DATABASE_URL": "x"}},
        {"live": ["WHATSAPP_ACCESS_TOKEN"]},
    ],
)
def test_anything_unproven_is_refused(change):
    assert check(**change)


def test_references_come_from_every_remote_database_url(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"DATABASE_URL={PROD}\n"
        "# DATABASE_URL=postgresql://u:p@commented.example.com/x\n"
        'OMS_READONLY_DATABASE_URL="postgresql://r:p@replica.example.com/oms"\n'
        "TEST_DATABASE_URL=postgresql://u:p@localhost/oms\n"
    )
    assert protected_servers([env, tmp_path / "missing"]) == {"dpg-prod123", "replica.example.com"}


def test_messages_never_contain_the_password():
    assert "secret" not in " ".join(check(url=PROD, expected="x"))
