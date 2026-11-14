from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppQueuedMessage,
    WhatsAppRecipientGroup,
    WhatsAppRecipientGroupMember,
    WhatsAppSchedule,
    WhatsAppScheduleRecipient,
)
from app.services.whatsapp_schedule_service import (
    build_idempotency_key,
    queue_schedule,
    resolve_recipients,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _contact(session: AsyncSession, name: str, phone: str) -> WhatsAppContact:
    c = WhatsAppContact(name=name, phone_number=phone, opted_in=True, created_at=datetime.now(UTC))
    session.add(c)
    await session.flush()
    return c


async def _schedule(session: AsyncSession, **kwargs) -> WhatsAppSchedule:
    now = datetime.now(UTC)
    s = WhatsAppSchedule(
        name=kwargs.get("name", "Test schedule"),
        schedule_kind=kwargs.get("schedule_kind", "one_time"),
        message_type=kwargs.get("message_type", "text"),
        body_text=kwargs.get("body_text", "Hello"),
        timezone="Asia/Kolkata",
        is_enabled=kwargs.get("is_enabled", True),
        is_paused=kwargs.get("is_paused", False),
        created_at=now,
        updated_at=now,
    )
    session.add(s)
    await session.flush()
    return s


async def test_resolve_recipients_includes_direct_contact_and_group_members(
    db_session: AsyncSession,
):
    c1 = await _contact(db_session, "Alice", "919991111111")
    c2 = await _contact(db_session, "Bob", "919992222222")
    c3 = await _contact(db_session, "Carol", "919993333333")
    group = WhatsAppRecipientGroup(name="Management", created_at=datetime.now(UTC))
    db_session.add(group)
    await db_session.flush()
    db_session.add(WhatsAppRecipientGroupMember(group_id=group.id, contact_id=c2.id))
    db_session.add(WhatsAppRecipientGroupMember(group_id=group.id, contact_id=c3.id))
    schedule = await _schedule(db_session)
    db_session.add(WhatsAppScheduleRecipient(schedule_id=schedule.id, contact_id=c1.id))
    db_session.add(WhatsAppScheduleRecipient(schedule_id=schedule.id, group_id=group.id))
    await db_session.commit()

    recipients = await resolve_recipients(db_session, schedule.id)
    assert {c.id for c in recipients} == {c1.id, c2.id, c3.id}


async def test_resolve_recipients_excludes_opted_out(db_session: AsyncSession):
    c1 = await _contact(db_session, "Alice", "919991111111")
    c1.opted_in = False
    schedule = await _schedule(db_session)
    db_session.add(WhatsAppScheduleRecipient(schedule_id=schedule.id, contact_id=c1.id))
    await db_session.commit()
    recipients = await resolve_recipients(db_session, schedule.id)
    assert recipients == []


async def test_queue_schedule_creates_one_message_per_recipient(db_session: AsyncSession):
    c1 = await _contact(db_session, "Alice", "919991111111")
    c2 = await _contact(db_session, "Bob", "919992222222")
    schedule = await _schedule(db_session)
    db_session.add(WhatsAppScheduleRecipient(schedule_id=schedule.id, contact_id=c1.id))
    db_session.add(WhatsAppScheduleRecipient(schedule_id=schedule.id, contact_id=c2.id))
    await db_session.commit()

    created = await queue_schedule(
        db_session, schedule, scheduled_send_at=datetime.now(UTC), send_slot="2026-11-10"
    )
    await db_session.commit()
    assert len(created) == 2
    assert all(m.status == "QUEUED" for m in created)
    # Never marked sent merely by being queued.
    assert all(m.sent_at is None for m in created)


async def test_queue_schedule_is_idempotent_for_same_slot(db_session: AsyncSession):
    c1 = await _contact(db_session, "Alice", "919991111111")
    schedule = await _schedule(db_session)
    db_session.add(WhatsAppScheduleRecipient(schedule_id=schedule.id, contact_id=c1.id))
    await db_session.commit()

    first = await queue_schedule(
        db_session, schedule, scheduled_send_at=datetime.now(UTC), send_slot="2026-11-10"
    )
    await db_session.commit()
    second = await queue_schedule(
        db_session, schedule, scheduled_send_at=datetime.now(UTC), send_slot="2026-11-10"
    )
    await db_session.commit()
    assert len(first) == 1
    assert len(second) == 0  # duplicate slot -- no second row created

    rows = (
        await db_session.execute(
            select(WhatsAppQueuedMessage).where(WhatsAppQueuedMessage.schedule_id == schedule.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_queue_schedule_skips_when_disabled_or_paused(db_session: AsyncSession):
    c1 = await _contact(db_session, "Alice", "919991111111")
    disabled = await _schedule(db_session, name="Disabled", is_enabled=False)
    paused = await _schedule(db_session, name="Paused", is_paused=True)
    for s in (disabled, paused):
        db_session.add(WhatsAppScheduleRecipient(schedule_id=s.id, contact_id=c1.id))
    await db_session.commit()

    assert (
        await queue_schedule(
            db_session, disabled, scheduled_send_at=datetime.now(UTC), send_slot="x"
        )
        == []
    )
    assert (
        await queue_schedule(
            db_session, paused, scheduled_send_at=datetime.now(UTC), send_slot="x"
        )
        == []
    )


def test_idempotency_key_differs_per_slot():
    import uuid

    sid = uuid.uuid4()
    cid = uuid.uuid4()
    k1 = build_idempotency_key(schedule_id=sid, contact_id=cid, send_slot="2026-11-10")
    k2 = build_idempotency_key(schedule_id=sid, contact_id=cid, send_slot="2026-11-11")
    assert k1 != k2


def test_idempotency_key_same_for_same_slot():
    import uuid

    sid = uuid.uuid4()
    cid = uuid.uuid4()
    k1 = build_idempotency_key(schedule_id=sid, contact_id=cid, send_slot="2026-11-10")
    k2 = build_idempotency_key(schedule_id=sid, contact_id=cid, send_slot="2026-11-10")
    assert k1 == k2
