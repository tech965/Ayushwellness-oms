"""Positively identify the database BEFORE running migrations or an app against
it (for staging). Run from apps/api:

    $env:OMS_ENV_FILE = ".env.staging.local"
    python scripts/check_db_target.py --expect <host id shown in Render>
    python scripts/check_db_target.py --expect <host id> --connect

Without --connect nothing is contacted. With it, one READ-ONLY session reports
the schema version. It refuses (exit 1) unless all of these hold:
  * OMS_ENV_FILE names the settings file, and the shell does not override
    DATABASE_URL;
  * the target is not any database server named in ../../.env or .env (those
    include production), and such a reference exists to compare against;
  * the target host matches --expect;
  * no live Shopify / Shiprocket / WhatsApp / Amazon credential is loaded.
A password or full URL is never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

API_DIR = Path(__file__).resolve().parents[1]
REFERENCE_FILES = (API_DIR.parents[1] / ".env", API_DIR / ".env")
LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "postgres", "db"}
LIVE_CREDENTIALS = (
    "SHOPIFY_ACCESS_TOKEN",
    "SHIPROCKET_PASSWORD",
    "WHATSAPP_ACCESS_TOKEN",
    "AMAZON_SP_API_REFRESH_TOKEN",
)


def server_id(url: str) -> str:
    """Render's internal (`dpg-x-a`) and external (`dpg-x-a.region...`) names
    are the same server; two databases on one server count as the same."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return (
        host.split(".")[0].removesuffix("-a")
        if "render.com" in host or host.startswith("dpg-")
        else host
    )


def protected_servers(files: Iterable[Path]) -> set[str]:
    found: set[str] = set()
    for path in files:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            name, _, value = line.partition("=")
            value = value.strip().strip("\"'")
            if "DATABASE_URL" not in name or name.lstrip().startswith("#") or "://" not in value:
                continue
            if (urlsplit(value).hostname or "").lower() not in LOCAL_HOSTS:
                found.add(server_id(value))
    return found


def problems(
    url: str,
    expected: str,
    protected: set[str],
    environment: Mapping[str, str],
    live_credentials: Iterable[str],
) -> list[str]:
    out = []
    if not environment.get("OMS_ENV_FILE"):
        out.append("OMS_ENV_FILE is not set, so .env would be used.")
    if "DATABASE_URL" in environment:
        out.append("DATABASE_URL is set in this shell and overrides the file. Remove it.")
    if not protected:
        out.append("No remote reference database was found to compare against.")
    target = server_id(url)
    if target in protected:
        out.append("The target is a database server named in .env (this includes PRODUCTION).")
    if not expected or server_id(f"postgresql://{expected}") != target:
        out.append("The target host does not match --expect.")
    for name in live_credentials:
        out.append(f"{name} is loaded: remove live credentials from the staging file.")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", required=True, help="host (or Render dpg-... id)")
    parser.add_argument("--connect", action="store_true", help="read-only schema check")
    args = parser.parse_args()

    sys.path.insert(0, str(API_DIR))
    from app.core.config import settings

    url = settings.DATABASE_URL
    loaded = [name for name in LIVE_CREDENTIALS if getattr(settings, name, None)]
    found = problems(url, args.expect, protected_servers(REFERENCE_FILES), os.environ, loaded)
    parts = urlsplit(url)
    print(f"target host: {parts.hostname}\ndatabase:    {parts.path.lstrip('/')}")
    if found:
        print("REFUSED. Do not run anything against this configuration:")
        for item in found:
            print(f"  - {item}")
        return 1
    print("OK: not a database named in .env, and it matches --expect.")
    if not args.connect:
        return 0

    import sqlalchemy as sa

    engine = sa.create_engine(
        url.replace("+asyncpg", "+psycopg2"),
        connect_args={"options": "-c default_transaction_read_only=on", "connect_timeout": 15},
    )
    try:
        with engine.connect() as conn:
            tables = set(sa.inspect(conn).get_table_names())
            version = (
                conn.execute(sa.text("select version_num from alembic_version")).scalar()
                if "alembic_version" in tables
                else None
            )
            users = (
                conn.execute(sa.text("select count(*) from users")).scalar()
                if "users" in tables
                else None
            )
    except Exception as exc:  # noqa: BLE001 - never echo the URL
        print(f"Could not connect or read ({type(exc).__name__}).")
        return 1
    finally:
        engine.dispose()
    print(f"schema version: {version or 'none (empty database)'}")
    print(f"tables: {len(tables)} | users: {'no users table' if users is None else users}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
