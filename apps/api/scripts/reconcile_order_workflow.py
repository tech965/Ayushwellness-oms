"""Reconcile `orders` with `order_workflow` (OMS-owned fields only).

Dry run by default: prints a JSON report and changes nothing.

    python scripts/reconcile_order_workflow.py                 # dry run
    python scripts/reconcile_order_workflow.py --apply --yes   # repair

`--apply` additionally requires `--yes` and refuses to run while
SHOPIFY_LIVE_MODE is on. The target database is whatever DATABASE_URL
points at -- check it before applying; this script never runs on its own.
See app/services/workflow_reconciliation.py for the exact guarantees.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.workflow_reconciliation import reconcile  # noqa: E402


def _database_label() -> str:
    url = settings.DATABASE_URL
    host = url.rsplit("@", 1)[-1] if "@" in url else url
    return host  # credentials are never printed


async def main(apply: bool) -> int:
    print(f"Target database: {_database_label()}  (mode: {'APPLY' if apply else 'dry run'})")
    async with AsyncSessionLocal() as session:
        report = await reconcile(session, apply=apply)
    print(json.dumps(report.as_dict(), indent=2, default=str))
    return 1 if report.errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repairs")
    parser.add_argument("--yes", action="store_true", help="confirm --apply")
    args = parser.parse_args()
    if args.apply and not args.yes:
        parser.error("--apply requires --yes")
    raise SystemExit(asyncio.run(main(args.apply)))
