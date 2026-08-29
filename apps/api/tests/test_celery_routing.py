"""Queue routing — a slow/stuck Shiprocket list crawl (`shipments`/`ndr`/
`tracking`) must run on its own Celery queue so it can never occupy the
worker slot a Shopify order sync needs. Production incident: a multi-hour
Shiprocket `shipments` backlog crawl monopolised the single worker and
Shopify orders stopped syncing.
"""

from __future__ import annotations

from app.workers.celery_app import (
    DEFAULT_QUEUE,
    SHIPROCKET_QUEUE,
    SYNC_TASK_SOFT_TIME_LIMIT,
    SYNC_TASK_TIME_LIMIT,
    celery_app,
    queue_for_entity,
    route_task,
)


def test_queue_for_entity_splits_shiprocket_crawls_from_everything_else() -> None:
    assert queue_for_entity("shipments") == SHIPROCKET_QUEUE
    assert queue_for_entity("ndr") == SHIPROCKET_QUEUE
    assert queue_for_entity("tracking") == SHIPROCKET_QUEUE

    assert queue_for_entity("orders") == DEFAULT_QUEUE
    assert queue_for_entity("customers") == DEFAULT_QUEUE
    assert queue_for_entity("products") == DEFAULT_QUEUE
    assert queue_for_entity(None) == DEFAULT_QUEUE


def test_route_task_sends_scheduled_shiprocket_sync_to_the_shiprocket_queue() -> None:
    # `run_sync_task(integration_id, sync_type, entity_type)` — entity_type
    # is the 3rd positional arg (how Beat's `_run_scheduled_sync` enqueues).
    route = route_task("sync.run", ("int-id", "incremental", "shipments"), {}, {})
    assert route == {"queue": SHIPROCKET_QUEUE}

    route = route_task("sync.run", ("int-id", "incremental", "ndr"), {}, {})
    assert route == {"queue": SHIPROCKET_QUEUE}


def test_route_task_keeps_shopify_order_sync_on_the_default_queue() -> None:
    route = route_task("sync.run", ("int-id", "incremental", "orders"), {}, {})
    assert route == {"queue": DEFAULT_QUEUE}

    # kwargs form is honoured too.
    route = route_task("sync.run", (), {"entity_type": "orders"}, {})
    assert route == {"queue": DEFAULT_QUEUE}


def test_route_task_sends_tracking_refresh_to_the_shiprocket_queue() -> None:
    assert route_task("shiprocket.refresh_tracking", ("job-id",), {}, {}) == {
        "queue": SHIPROCKET_QUEUE
    }


def test_route_task_has_no_opinion_on_unrelated_tasks() -> None:
    # `sync.execute` carries only a job id — the trigger endpoint routes it
    # explicitly via apply_async(queue=...); the router stays out of it.
    assert route_task("sync.execute", ("job-id",), {}, {}) is None
    assert route_task("webhooks.process_event", ("evt-id",), {}, {}) is None
    assert route_task("sync.reap_stale", (), {}, {}) is None


def test_celery_app_registers_the_router_and_default_queue() -> None:
    assert celery_app.conf.task_default_queue == DEFAULT_QUEUE
    assert route_task in tuple(celery_app.conf.task_routes)


def test_sync_tasks_carry_a_hard_time_limit_so_a_hung_crawl_frees_its_worker() -> None:
    # App budget is 8 min; these are the hard kill for a genuinely stuck
    # task (scoped to the crawl tasks, not global, so long non-sync tasks
    # like reconciliation are unaffected).
    assert SYNC_TASK_SOFT_TIME_LIMIT == 600
    assert SYNC_TASK_TIME_LIMIT == 660
    for task_name in ("sync.run", "sync.execute", "shiprocket.refresh_tracking"):
        task = celery_app.tasks[task_name]
        assert task.time_limit == SYNC_TASK_TIME_LIMIT
        assert task.soft_time_limit == SYNC_TASK_SOFT_TIME_LIMIT


def test_beat_still_schedules_shopify_order_sync_every_ten_minutes() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["run-scheduled-sync"]["task"] == "sync.run_scheduled"
    assert schedule["run-scheduled-sync"]["schedule"] == 600.0
    assert schedule["reap-stale-sync-jobs"]["task"] == "sync.reap_stale"
