"""WhatsApp Scheduled Messaging & Automated Daily Sales Reports.

Mutations (create/edit/pause/delete schedules, groups, contacts) are
gated on `integrations.manage`; reads on `integrations.read` -- this
reuses the existing integration-admin permission pair rather than
introducing new permission codes that would need their own seed-data
change (no `automation.*` permission exists yet in `scripts/seed.py`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Query
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.integrations.whatsapp.config import WhatsAppConfig
from app.models.auth import User
from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppQueuedMessage,
    WhatsAppRecipientGroup,
    WhatsAppRecipientGroupMember,
    WhatsAppSchedule,
    WhatsAppScheduleRecipient,
)
from app.schemas.response import ApiResponse
from app.schemas.whatsapp_automation import (
    ContactCreate,
    ContactResponse,
    DashboardSummary,
    GroupCreate,
    GroupResponse,
    QueuedMessageResponse,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleUpdate,
    WhatsAppStatus,
)
from app.services.whatsapp_contact_service import InvalidPhoneNumberError, create_contact
from app.services.whatsapp_schedule_service import cancel_message

router = APIRouter()
_read = require_permission("integrations.read")
_manage = require_permission("integrations.manage")


@router.get("/status", response_model=ApiResponse[WhatsAppStatus])
async def status(_: User = Depends(_read)):
    c = WhatsAppConfig.from_settings()
    return ApiResponse(data=WhatsAppStatus(configured=c.configured, missing=c.missing()))


# --- contacts ---------------------------------------------------------


@router.post("/contacts", response_model=ApiResponse[ContactResponse])
async def create_contact_endpoint(
    body: ContactCreate, session: AsyncSession = Depends(get_db), _: User = Depends(_manage)
):
    try:
        contact = await create_contact(session, name=body.name, phone=body.phone)
    except InvalidPhoneNumberError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return ApiResponse(data=ContactResponse.model_validate(contact))


@router.get("/contacts", response_model=ApiResponse[list[ContactResponse]])
async def list_contacts(session: AsyncSession = Depends(get_db), _: User = Depends(_read)):
    rows = (await session.execute(select(WhatsAppContact))).scalars().all()
    return ApiResponse(data=[ContactResponse.model_validate(c) for c in rows])


@router.post("/contacts/{contact_id}/opt-out", response_model=ApiResponse[ContactResponse])
async def opt_out_contact(
    contact_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(_manage)
):
    contact = await session.get(WhatsAppContact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.opted_in = False
    contact.opted_out_at = datetime.now(UTC)
    await session.commit()
    return ApiResponse(data=ContactResponse.model_validate(contact))


@router.post("/contacts/{contact_id}/opt-in", response_model=ApiResponse[ContactResponse])
async def opt_in_contact(
    contact_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(_manage)
):
    contact = await session.get(WhatsAppContact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.opted_in = True
    contact.opted_out_at = None
    await session.commit()
    return ApiResponse(data=ContactResponse.model_validate(contact))


# --- recipient groups ---------------------------------------------------


@router.post("/groups", response_model=ApiResponse[GroupResponse])
async def create_group(
    body: GroupCreate, session: AsyncSession = Depends(get_db), _: User = Depends(_manage)
):
    group = WhatsAppRecipientGroup(name=body.name, created_at=datetime.now(UTC))
    session.add(group)
    await session.flush()
    for contact_id in body.contact_ids:
        session.add(WhatsAppRecipientGroupMember(group_id=group.id, contact_id=contact_id))
    await session.commit()
    return ApiResponse(
        data=GroupResponse(
            id=group.id,
            name=group.name,
            member_count=len(body.contact_ids),
            created_at=group.created_at,
        )
    )


@router.get("/groups", response_model=ApiResponse[list[GroupResponse]])
async def list_groups(session: AsyncSession = Depends(get_db), _: User = Depends(_read)):
    groups = (await session.execute(select(WhatsAppRecipientGroup))).scalars().all()
    out = []
    for g in groups:
        count = (
            await session.execute(
                select(func.count())
                .select_from(WhatsAppRecipientGroupMember)
                .where(WhatsAppRecipientGroupMember.group_id == g.id)
            )
        ).scalar_one()
        out.append(
            GroupResponse(id=g.id, name=g.name, member_count=int(count), created_at=g.created_at)
        )
    return ApiResponse(data=out)


# --- schedules ------------------------------------------------------------


def _validate_schedule_shape(body: ScheduleCreate) -> None:
    if body.message_type == "template" and not body.template_name:
        raise HTTPException(
            status_code=422, detail="template_name is required for message_type=template"
        )
    if body.message_type == "text" and not body.body_text:
        raise HTTPException(
            status_code=422, detail="body_text is required for message_type=text"
        )
    if body.message_type == "image" and not (body.media_id or body.media_link):
        raise HTTPException(
            status_code=422, detail="media_id or media_link is required for message_type=image"
        )
    if body.schedule_kind == "one_time" and not body.send_at:
        raise HTTPException(
            status_code=422, detail="send_at is required for schedule_kind=one_time"
        )
    if body.schedule_kind in ("recurring_daily", "daily_sales_report") and not body.time_of_day:
        raise HTTPException(
            status_code=422,
            detail="time_of_day is required for recurring/daily_sales_report schedules",
        )
    for r in body.recipients:
        if bool(r.contact_id) == bool(r.group_id):
            raise HTTPException(
                status_code=422,
                detail="Each recipient must set exactly one of contact_id/group_id",
            )


@router.post("/schedules", response_model=ApiResponse[ScheduleResponse])
async def create_schedule(
    body: ScheduleCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(_manage),
):
    _validate_schedule_shape(body)
    now = datetime.now(UTC)
    schedule = WhatsAppSchedule(
        name=body.name,
        schedule_kind=body.schedule_kind,
        message_type=body.message_type,
        template_name=body.template_name,
        template_language=body.template_language,
        body_text=body.body_text,
        media_id=body.media_id,
        media_link=body.media_link,
        send_at=body.send_at,
        time_of_day=body.time_of_day,
        timezone=body.timezone,
        is_enabled=True,
        is_paused=False,
        created_by_user_id=user.id,
        created_at=now,
        updated_at=now,
    )
    session.add(schedule)
    await session.flush()
    for r in body.recipients:
        session.add(
            WhatsAppScheduleRecipient(
                schedule_id=schedule.id, contact_id=r.contact_id, group_id=r.group_id
            )
        )
    await session.commit()
    return ApiResponse(data=await _to_schedule_response(session, schedule))


async def _to_schedule_response(
    session: AsyncSession, schedule: WhatsAppSchedule
) -> ScheduleResponse:
    count = (
        await session.execute(
            select(func.count())
            .select_from(WhatsAppScheduleRecipient)
            .where(WhatsAppScheduleRecipient.schedule_id == schedule.id)
        )
    ).scalar_one()
    return ScheduleResponse(
        id=schedule.id,
        name=schedule.name,
        schedule_kind=schedule.schedule_kind,
        message_type=schedule.message_type,
        template_name=schedule.template_name,
        template_language=schedule.template_language,
        body_text=schedule.body_text,
        media_id=schedule.media_id,
        media_link=schedule.media_link,
        send_at=schedule.send_at,
        time_of_day=schedule.time_of_day,
        timezone=schedule.timezone,
        is_enabled=schedule.is_enabled,
        is_paused=schedule.is_paused,
        last_queued_for_date=schedule.last_queued_for_date,
        recipient_count=int(count),
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


@router.get("/schedules", response_model=ApiResponse[list[ScheduleResponse]])
async def list_schedules(session: AsyncSession = Depends(get_db), _: User = Depends(_read)):
    rows = (await session.execute(select(WhatsAppSchedule))).scalars().all()
    return ApiResponse(data=[await _to_schedule_response(session, s) for s in rows])


@router.patch("/schedules/{schedule_id}", response_model=ApiResponse[ScheduleResponse])
async def update_schedule(
    schedule_id: uuid.UUID,
    body: ScheduleUpdate,
    session: AsyncSession = Depends(get_db),
    _: User = Depends(_manage),
):
    schedule = await session.get(WhatsAppSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    for field in (
        "name",
        "body_text",
        "template_name",
        "template_language",
        "media_id",
        "media_link",
        "send_at",
        "time_of_day",
        "is_enabled",
        "is_paused",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(schedule, field, value)
    if body.recipients is not None:
        existing = (
            await session.execute(
                select(WhatsAppScheduleRecipient).where(
                    WhatsAppScheduleRecipient.schedule_id == schedule.id
                )
            )
        ).scalars().all()
        for row in existing:
            await session.delete(row)
        await session.flush()
        for r in body.recipients:
            session.add(
                WhatsAppScheduleRecipient(
                    schedule_id=schedule.id, contact_id=r.contact_id, group_id=r.group_id
                )
            )
    schedule.updated_at = datetime.now(UTC)
    await session.commit()
    return ApiResponse(data=await _to_schedule_response(session, schedule))


@router.delete("/schedules/{schedule_id}", response_model=ApiResponse[None])
async def delete_schedule(
    schedule_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(_manage)
):
    schedule = await session.get(WhatsAppSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await session.delete(schedule)
    await session.commit()
    return ApiResponse(data=None)


# --- queue / delivery history ----------------------------------------


async def _to_queue_response(
    session: AsyncSession, msg: WhatsAppQueuedMessage
) -> QueuedMessageResponse:
    contact = await session.get(WhatsAppContact, msg.contact_id)
    schedule_name = None
    if msg.schedule_id:
        schedule = await session.get(WhatsAppSchedule, msg.schedule_id)
        schedule_name = schedule.name if schedule else None
    return QueuedMessageResponse(
        id=msg.id,
        schedule_id=msg.schedule_id,
        schedule_name=schedule_name,
        contact_id=msg.contact_id,
        contact_name=contact.name if contact else "(deleted contact)",
        contact_phone=contact.phone_number if contact else "",
        message_type=msg.message_type,
        scheduled_send_at=msg.scheduled_send_at,
        status=msg.status,
        provider_message_id=msg.provider_message_id,
        failure_reason=msg.failure_reason,
        retry_count=msg.retry_count,
        sent_at=msg.sent_at,
        delivered_at=msg.delivered_at,
        read_at=msg.read_at,
        created_at=msg.created_at,
    )


def _queue_filters(
    *,
    status: str | None,
    contact_id: uuid.UUID | None,
    schedule_id: uuid.UUID | None,
    message_type: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
):
    filters = []
    if status:
        filters.append(WhatsAppQueuedMessage.status == status)
    if contact_id:
        filters.append(WhatsAppQueuedMessage.contact_id == contact_id)
    if schedule_id:
        filters.append(WhatsAppQueuedMessage.schedule_id == schedule_id)
    if message_type:
        filters.append(WhatsAppQueuedMessage.message_type == message_type)
    if date_from:
        filters.append(WhatsAppQueuedMessage.scheduled_send_at >= date_from)
    if date_to:
        filters.append(WhatsAppQueuedMessage.scheduled_send_at <= date_to)
    return filters


@router.get("/queue", response_model=ApiResponse[list[QueuedMessageResponse]])
async def list_queue(
    status: str | None = Query(default=None),
    contact_id: uuid.UUID | None = Query(default=None),
    schedule_id: uuid.UUID | None = Query(default=None),
    message_type: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(_read),
):
    filters = _queue_filters(
        status=status,
        contact_id=contact_id,
        schedule_id=schedule_id,
        message_type=message_type,
        date_from=date_from,
        date_to=date_to,
    )
    rows = (
        await session.execute(
            select(WhatsAppQueuedMessage)
            .where(*filters)
            .order_by(WhatsAppQueuedMessage.scheduled_send_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return ApiResponse(data=[await _to_queue_response(session, m) for m in rows])


@router.post("/queue/{message_id}/cancel", response_model=ApiResponse[QueuedMessageResponse])
async def cancel_queued_message(
    message_id: uuid.UUID, session: AsyncSession = Depends(get_db), _: User = Depends(_manage)
):
    try:
        msg = await cancel_message(session, message_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return ApiResponse(data=await _to_queue_response(session, msg))


@router.get("/queue/export")
async def export_queue(
    status: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    _: User = Depends(_read),
) -> StreamingResponse:
    filters = _queue_filters(
        status=status, contact_id=None, schedule_id=None, message_type=None,
        date_from=date_from, date_to=date_to,
    )
    rows = (
        await session.execute(
            select(WhatsAppQueuedMessage)
            .where(*filters)
            .order_by(WhatsAppQueuedMessage.scheduled_send_at.desc())
            .limit(5000)
        )
    ).scalars().all()
    wb = Workbook()
    ws = wb.active
    ws.title = "WhatsApp History"
    ws.append(
        [
            "Recipient",
            "Phone",
            "Message type",
            "Scheduled at",
            "Status",
            "Sent at",
            "Delivered at",
            "Read at",
            "Retry count",
            "Failure reason",
        ]
    )
    for m in rows:
        contact = await session.get(WhatsAppContact, m.contact_id)
        ws.append(
            [
                contact.name if contact else "(deleted contact)",
                contact.phone_number if contact else "",
                m.message_type,
                m.scheduled_send_at.isoformat(),
                m.status,
                m.sent_at.isoformat() if m.sent_at else "",
                m.delivered_at.isoformat() if m.delivered_at else "",
                m.read_at.isoformat() if m.read_at else "",
                m.retry_count,
                m.failure_reason or "",
            ]
        )
    buf = BytesIO()
    wb.save(buf)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=whatsapp-history.xlsx"},
    )


# --- dashboard --------------------------------------------------------


@router.get("/dashboard", response_model=ApiResponse[DashboardSummary])
async def dashboard(session: AsyncSession = Depends(get_db), _: User = Depends(_read)):
    active = (
        await session.execute(
            select(func.count())
            .select_from(WhatsAppSchedule)
            .where(WhatsAppSchedule.is_enabled.is_(True), WhatsAppSchedule.is_paused.is_(False))
        )
    ).scalar_one()
    paused = (
        await session.execute(
            select(func.count()).select_from(WhatsAppSchedule).where(WhatsAppSchedule.is_paused.is_(True))
        )
    ).scalar_one()
    pending = (
        await session.execute(
            select(func.count())
            .select_from(WhatsAppQueuedMessage)
            .where(WhatsAppQueuedMessage.status == "QUEUED")
        )
    ).scalar_one()
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = (
        await session.execute(
            select(func.count())
            .select_from(WhatsAppQueuedMessage)
            .where(WhatsAppQueuedMessage.sent_at >= today_start)
        )
    ).scalar_one()
    delivered_today = (
        await session.execute(
            select(func.count())
            .select_from(WhatsAppQueuedMessage)
            .where(WhatsAppQueuedMessage.delivered_at >= today_start)
        )
    ).scalar_one()
    failed_today = (
        await session.execute(
            select(func.count())
            .select_from(WhatsAppQueuedMessage)
            .where(
                WhatsAppQueuedMessage.status == "FAILED",
                WhatsAppQueuedMessage.updated_at >= today_start,
            )
        )
    ).scalar_one()
    latest_report = (
        await session.execute(
            select(WhatsAppQueuedMessage)
            .join(WhatsAppSchedule, WhatsAppQueuedMessage.schedule_id == WhatsAppSchedule.id)
            .where(WhatsAppSchedule.schedule_kind == "daily_sales_report")
            .order_by(WhatsAppQueuedMessage.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return ApiResponse(
        data=DashboardSummary(
            active_schedules=int(active),
            paused_schedules=int(paused),
            pending_messages=int(pending),
            sent_today=int(sent_today),
            delivered_today=int(delivered_today),
            failed_today=int(failed_today),
            next_report_run_utc="03:30 UTC daily (09:00 Asia/Kolkata)",
            latest_daily_report_status=latest_report.status if latest_report else "never run",
        )
    )
