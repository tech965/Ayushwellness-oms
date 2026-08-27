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

from app.core.config import settings
from app.integrations.bootstrap import register_all_adapters

# Registered at worker-process import time — the registry is in-memory
# and per-process, so the Celery worker needs its own registration pass
# independent of `app.main`'s (FastAPI request process).
register_all_adapters()

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
    },
)
