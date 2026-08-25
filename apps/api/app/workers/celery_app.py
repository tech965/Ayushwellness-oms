"""Celery application instance.

Status: generic sync/webhook/retry task modules registered since Phase
2.1; `app.tasks.shiprocket_sync` (tracking refresh) added in Phase 2.3.
Delay detection remains a later phase.

Run a worker locally with:
    celery -A app.workers.celery_app worker --loglevel=info
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
)
