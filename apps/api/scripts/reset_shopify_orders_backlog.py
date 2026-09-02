"""One-time recovery: force a genuine historical backlog re-crawl for one
Shopify entity type (default: orders) whose backlog was incorrectly
treated as complete before its full history was ever actually imported.

Production incident this recovers from: Shopify orders older than
~2026-03 (e.g. #AWL46048, created 2025-12-28, confirmed live in Shopify)
were never imported into the OMS. Root cause (see `SyncService.
backlog_known_complete_for`'s docstring): a backlog crawl that ran out of
its per-job time budget mid-crawl still completed successfully with a
real `completed_at`, and nothing distinguished "genuinely finished" from
"just ran out of time this cycle" other than an implicit, easily-confused
signal. That gap is now closed by an explicit `backlog_complete` flag,
set only at the exact moment a crawl observes the provider's own
`hasNextPage: false` — but flipping the code forward doesn't retroactively
fix state that was already (incorrectly) persisted before this fix
existed. This script is the one-time correction for that existing state.

SAFE BY DESIGN:
- Uses ONLY the existing SyncService/SyncJobRepository/Celery
  infrastructure — no raw SQL, no direct table writes beyond what
  `SyncService.reset_backlog`/`start_sync` already do as part of their
  normal, tested operation.
- Never deletes or modifies a single existing Order/Customer/Product row
  — `entity_sync._upsert_order`'s existing (source_system, external_id)
  idempotency means every order Shopify still reports is updated in
  place, never duplicated; every order missing so far is created.
- Only resets the ONE named entity_type's crawl checkpoint
  (`Integration.configuration["sync_cursors"]` /
  `["backlog_complete"]`) — customers, products, and every other
  integration are completely untouched.
- Refuses to run if a sync is already active for this entity (the
  existing one-active-job-per-(integration, entity_type) guard in
  `SyncService.start_sync` — this script does not bypass it).
- Only *starts* the crawl and enqueues it on the existing Celery worker —
  it does not attempt to run a potentially hours-long crawl to
  completion inside this one script invocation. The crawl resumes itself
  (persisted cursor) across the existing 8-minute per-job time budget and
  the existing 10-minute scheduled cadence (now correctly re-selecting
  FULL for this entity every cycle until `backlog_complete` is genuinely
  set — see `app.tasks.sync_tasks._run_scheduled_sync`), exactly like any
  other backlog crawl already in progress. Once it finishes, the
  scheduler automatically reverts to incremental sync for this entity —
  no further manual action is ever required.

Run with:
    python scripts/reset_shopify_orders_backlog.py [--entity-type orders] [--dry-run]

Run this from a Render Shell session on either the API or worker service
(both have DB + Celery broker access) — see docs/integrations/shopify.md.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Run as `python scripts/reset_shopify_orders_backlog.py`, this script's
# own directory (`apps/api/scripts`) — not the cwd it happens to be
# launched from — is all Python puts on `sys.path` (`uvicorn app.main:app`
# and `celery -A app.workers.celery_app` both work from `apps/api` only
# because each CLI explicitly inserts the cwd itself; a plain `python
# some_script.py` invocation does not). Insert the repo root (this file's
# parent's parent, i.e. `apps/api`) before importing `app`, so the script
# is self-sufficient from `apps/api` regardless of how it's invoked — no
# PYTHONPATH, no `-m`, no package install step required.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import AsyncSessionLocal, run_with_cleanup  # noqa: E402
from app.models.integration import IntegrationCode  # noqa: E402
from app.repositories.integration import IntegrationRepository  # noqa: E402
from app.services.sync_service import SyncService  # noqa: E402
from app.tasks.sync_tasks import execute_sync_task  # noqa: E402


async def _run(*, entity_type: str, dry_run: bool) -> None:
    async with AsyncSessionLocal() as session:
        integration = await IntegrationRepository(session).get_by_code(IntegrationCode.SHOPIFY)
        if integration is None:
            raise SystemExit(
                "No Shopify integration row found — run scripts/seed.py first, "
                "or confirm the correct database is targeted."
            )

        cursors = (integration.configuration or {}).get("sync_cursors", {})
        backlog_flags = (integration.configuration or {}).get("backlog_complete", {})
        print(f"Shopify integration: {integration.id}")
        print(f"Current sync_cursors[{entity_type!r}]: {cursors.get(entity_type)!r}")
        print(f"Current backlog_complete[{entity_type!r}]: {backlog_flags.get(entity_type)!r}")

        if dry_run:
            print("\n--dry-run: no changes made, no sync triggered.")
            return

        service = SyncService(session)
        await service.reset_backlog(integration_id=integration.id, entity_type=entity_type)
        print(f"\nReset backlog checkpoint for entity_type={entity_type!r}.")

        from app.core.exceptions import ConflictError
        from app.models.enums import SyncType

        try:
            job = await service.start_sync(
                integration_id=integration.id,
                sync_type=SyncType.FULL,
                entity_type=entity_type,
            )
        except ConflictError as exc:
            print(
                f"\nA sync for entity_type={entity_type!r} is already active "
                f"({exc.details.get('status')!r}, job {exc.details.get('sync_job_id')!r}) — "
                "the backlog checkpoint has been reset, but no new job was started. "
                "The already-running/queued job (or the next scheduled cycle, which now "
                "correctly re-selects FULL for this entity) will pick up the reset "
                "checkpoint on its own; re-run this script later only if needed."
            )
            return

        execute_sync_task.delay(str(job.id))
        print(f"\nQueued SyncJob {job.id} (sync_type=full, entity_type={entity_type!r}).")
        print(
            "This crawl is resumable and will continue automatically across the "
            "existing time budget and scheduled cadence until it genuinely "
            "completes — no further manual action is required."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--entity-type",
        default="orders",
        help="Shopify entity type to reset (default: orders).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show current state only — reset and trigger nothing.",
    )
    args = parser.parse_args()
    asyncio.run(run_with_cleanup(_run(entity_type=args.entity_type, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
