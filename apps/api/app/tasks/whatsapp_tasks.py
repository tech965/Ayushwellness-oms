"""WhatsApp automation Celery tasks: the message-queue processor
(sends anything due), the recurring-schedule queuer (including the
9 AM daily sales report), and delivery-webhook-driven status updates
land through the API layer directly, not a task (see
`app.api.v1.endpoints.whatsapp_webhooks`).

Survives restarts / no duplicate execution: every queued row is created
with a deterministic idempotency key (`whatsapp_schedule_service.
build_idempotency_key`) before any send is attempted, so a worker
restart mid-batch just re-reads the same QUEUED rows from Postgres on
the next tick -- nothing is held only in a worker process's memory.
`WhatsAppSchedule.last_queued_for_date` additionally stops the
recurring-schedule queuer from creating a second day's batch if a beat
tick fires twice for the same day (the idempotency key already prevents
duplicate *sends*; this also avoids redundant *queuing* work).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, run_with_cleanup
from app.models.whatsapp_automation import WhatsAppQueuedMessage, WhatsAppSchedule
from app.services.whatsapp_daily_report_service import build_daily_sales_report
from app.services.whatsapp_report_formatter import format_report_text
from app.services.whatsapp_schedule_service import queue_schedule
from app.services.whatsapp_send_service import send_queued_message
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Caps a single tick's batch so one worker run can't hang indefinitely
# on a large backlog -- same reasoning as the time budgets added to the
# Amazon/Flipkart Orders API work this session (this backend may run as
# a Vercel serverless function with a hard request-time limit; Celery
# tasks run on the separate worker, not Vercel, but keeping batches
# bounded is still good hygiene so a stuck/slow provider can't starve
# the next tick for minutes).
QUEUE_BATCH_SIZE = 25


@celery_app.task(name="whatsapp.process_queue")
def process_whatsapp_queue_task() -> dict:
    return asyncio.run(run_with_cleanup(_process_queue()))


async def _process_queue() -> dict:
    sent = failed = 0
    async with AsyncSessionLocal() as session:
        now = datetime.now(UTC)
        rows = (
            await session.execute(
                select(WhatsAppQueuedMessage)
                .where(
                    WhatsAppQueuedMessage.status == "QUEUED",
                    WhatsAppQueuedMessage.scheduled_send_at <= now,
                )
                .order_by(WhatsAppQueuedMessage.scheduled_send_at)
                .limit(QUEUE_BATCH_SIZE)
            )
        ).scalars().all()
        for msg in rows:
            await send_queued_message(session, msg)
            await session.commit()
            if msg.status == "SENT":
                sent += 1
            elif msg.status == "FAILED":
                failed += 1
    logger.info("whatsapp_queue_processed", sent=sent, failed=failed, batch=len(rows))
    return {"sent": sent, "failed": failed, "batch": len(rows)}


@celery_app.task(name="whatsapp.enqueue_recurring_schedules")
def enqueue_recurring_schedules_task() -> dict:
    return asyncio.run(run_with_cleanup(_enqueue_recurring_schedules()))


async def _enqueue_recurring_schedules() -> dict:
    """Checks every enabled, non-paused `recurring_daily` schedule
    (generic recurring messages -- not the sales report, which has its
    own dedicated task below with its own data-fetch step) against its
    own `time_of_day`/`timezone`, queues it once per local calendar day.
    """
    queued_total = 0
    async with AsyncSessionLocal() as session:
        schedules = (
            await session.execute(
                select(WhatsAppSchedule).where(
                    WhatsAppSchedule.schedule_kind == "recurring_daily",
                    WhatsAppSchedule.is_enabled.is_(True),
                    WhatsAppSchedule.is_paused.is_(False),
                )
            )
        ).scalars().all()
        for schedule in schedules:
            if not schedule.time_of_day:
                continue
            tz = ZoneInfo(schedule.timezone)
            now_local = datetime.now(tz)
            today_str = now_local.date().isoformat()
            if schedule.last_queued_for_date == today_str:
                continue
            hour, minute = (int(x) for x in schedule.time_of_day.split(":"))
            due_at_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now_local < due_at_local:
                continue
            created = await queue_schedule(
                session,
                schedule,
                scheduled_send_at=due_at_local.astimezone(UTC),
                send_slot=today_str,
            )
            schedule.last_queued_for_date = today_str
            schedule.updated_at = datetime.now(UTC)
            await session.commit()
            queued_total += len(created)
    logger.info("whatsapp_recurring_schedules_enqueued", queued=queued_total)
    return {"queued": queued_total}


@celery_app.task(name="whatsapp.send_daily_sales_report")
def send_daily_sales_report_task() -> dict:
    return asyncio.run(run_with_cleanup(_send_daily_sales_report()))


async def _send_daily_sales_report() -> dict:
    """Runs at 9:00 Asia/Kolkata (beat_schedule below: 3:30 UTC, since
    Celery's global `timezone` config stays UTC -- see
    `app.workers.celery_app`'s beat_schedule comment for why it isn't
    changed here). Fetches yesterday's (IST) data fresh every run -- the
    report is never cached or reused across schedule rows.
    """
    queued_total = 0
    async with AsyncSessionLocal() as session:
        schedules = (
            await session.execute(
                select(WhatsAppSchedule).where(
                    WhatsAppSchedule.schedule_kind == "daily_sales_report",
                    WhatsAppSchedule.is_enabled.is_(True),
                    WhatsAppSchedule.is_paused.is_(False),
                )
            )
        ).scalars().all()
        if not schedules:
            return {"queued": 0, "reason": "no enabled daily_sales_report schedules"}

        report = await build_daily_sales_report(session)
        text = format_report_text(report)
        send_slot = report.report_date.isoformat()

        for schedule in schedules:
            if schedule.last_queued_for_date == send_slot:
                continue
            if schedule.message_type == "template":
                payload = {
                    "template_name": schedule.template_name,
                    "language_code": schedule.template_language or "en",
                    "body_parameters": [text[:1024]],
                }
            else:
                payload = {"body": text}
            created = await queue_schedule(
                session,
                schedule,
                scheduled_send_at=datetime.now(UTC),
                send_slot=send_slot,
                payload_override=payload,
            )
            schedule.last_queued_for_date = send_slot
            schedule.updated_at = datetime.now(UTC)
            await session.commit()
            queued_total += len(created)
    logger.info("whatsapp_daily_sales_report_enqueued", queued=queued_total)
    return {"queued": queued_total}
