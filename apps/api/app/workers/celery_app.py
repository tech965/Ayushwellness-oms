"""Celery application instance.

Status: generic sync/webhook/retry task modules registered since Phase
2.1; `app.tasks.shiprocket_sync` (tracking refresh) added in Phase 2.3.
Delay detection remains a later phase.

Run a worker locally with:
    celery -A app.workers.celery_app worker --loglevel=info

To also run the scheduled-sync beat (see `beat_schedule` below), either
run a second, dedicated process:
    celery -A app.workers.celery_app beat --loglevel=info
or, for a single-worker-instance deployment where a whole extra process
isn't worth it, embed it in the worker itself with `-B`:
    celery -A app.workers.celery_app worker -B --loglevel=info
(`-B` is documented by Celery as unsafe with more than one worker
process/instance — it would enqueue the same scheduled job once per
instance — so only use it if there is exactly one worker running.)
"""

from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.bootstrap import register_all_adapters

logger = get_logger(__name__)

# Registered here at *module import* time so the registry is populated
# in every process that merely imports this module without going
# through Celery's own worker bootstrap at all — the master/beat
# process, an eager task call in tests, a one-off script that imports
# `celery_app` to inspect config, etc.
register_all_adapters()


@worker_process_init.connect
def _register_adapters_in_worker_process(**_kwargs: object) -> None:
    """Belt-and-braces for the one case the module-level call above
    can't guarantee: the *actual* worker process(es) that run tasks.

    Celery's prefork pool forks child processes from the master after
    this module's top-level code (including the `register_all_adapters()`
    call above) has already run — and `fork()` normally inherits that
    already-populated in-memory registry via copy-on-write, so this is
    usually redundant. But "usually" was exactly the gap behind a real
    production incident: a `SyncError` — "No adapter registered for
    integration 'shiprocket'" — that could not be reproduced by manually
    re-running `register_all_adapters()` in a fresh Render shell (it
    always worked there), which only makes sense if the actual *running*
    worker process's copy of the registry, at the moment it executed
    that sync, was a different, stale process than what a fresh shell
    invocation gets — e.g. a worker process that predates a deploy, or a
    pool/process-spawning path that doesn't preserve fork-inherited
    state the way plain `os.fork()` does. `worker_process_init` is
    Celery's own signal for "run this once, in this exact process, right
    before it starts pulling tasks" — firing it here removes the
    dependency on fork-inheritance being reliable, for every pool type
    Celery supports (prefork, solo, gevent, eventlet), without
    introducing a second registry or repeating this per task execution
    (see `SyncService.execute_sync`, which deliberately does not call
    this — registration stays a once-per-process startup concern, not a
    per-call one).
    """
    register_all_adapters()
    logger.info("adapters_registered_in_worker_process")


celery_app = Celery(
    "ayushwellness_oms",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.sync_tasks",
        "app.tasks.webhook_processing",
        "app.tasks.retry_processing",
        "app.tasks.shiprocket_sync",
        "app.tasks.reconciliation_tasks",
        # Provider-specific task modules are added here as needed, e.g.:
        # "app.tasks.delay_detection",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=60,
    task_default_max_retries=5,
    # Every task here persists its own outcome (SyncJob/WebhookEvent rows
    # are the source of truth for status) rather than being awaited via
    # Celery's AsyncResult, so results are never read back — ignoring them
    # skips the Redis result-backend pub/sub round trip entirely, which
    # otherwise makes `.delay()` block (and fail loudly) whenever Redis is
    # unreachable, even though the broker publish itself would have been fine.
    task_ignore_result=True,
    # The automatic-sync backstop (see `app.tasks.sync_tasks.
    # run_scheduled_sync_task`'s docstring for why this exists — found via
    # a live reconciliation: with no webhooks registered and no schedule,
    # new Shopify orders never reached the OMS except on a manual
    # "Trigger Sync" click). 10 minutes bounds how far the OMS can drift
    # behind Shopify even if webhook delivery is missed or never
    # configured, without hammering the Shopify API — each run is a cheap
    # incremental sync (only records changed since the last successful
    # sync), not a full re-pull.
    beat_schedule={
        "run-scheduled-sync": {
            "task": "sync.run_scheduled",
            "schedule": 600.0,
        },
        # Round 4: catches the one way a WebhookEvent can be silently
        # abandoned -- its Celery enqueue call itself raised (a broker
        # outage at the exact moment the webhook arrived) -- see
        # `app.tasks.webhook_processing.recover_stuck_webhook_events_task`'s
        # docstring. Runs more often than the sync backstop since its job
        # is narrow and cheap (one indexed query plus re-enqueueing
        # whatever it finds, normally nothing).
        "recover-stuck-webhook-events": {
            "task": "webhooks.recover_stuck",
            "schedule": 300.0,
        },
        # Real production incident: 8 separate `shipments` SyncJobs ended
        # up stuck RUNNING (several for 18+ hours) after worker restarts
        # killed them mid-flight, with nothing to ever mark them failed —
        # see `app.tasks.sync_tasks.reap_stale_sync_jobs_task`'s docstring.
        # Same cadence as the sync backstop above; the reaper's own
        # 20-minute staleness threshold is what actually bounds how long
        # an orphaned job can sit before cleanup, not this interval.
        "reap-stale-sync-jobs": {
            "task": "sync.reap_stale",
            "schedule": 600.0,
        },
    },
)
