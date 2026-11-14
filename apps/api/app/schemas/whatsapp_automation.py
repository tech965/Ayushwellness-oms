from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WhatsAppStatus(BaseModel):
    configured: bool
    missing: list[str]


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=5, max_length=32)


class ContactResponse(BaseModel):
    id: uuid.UUID
    name: str
    phone_number: str
    opted_in: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    contact_ids: list[uuid.UUID] = Field(default_factory=list)


class GroupResponse(BaseModel):
    id: uuid.UUID
    name: str
    member_count: int
    created_at: datetime


class RecipientSpec(BaseModel):
    """Exactly one of `contact_id`/`group_id` must be set."""

    contact_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    schedule_kind: str = Field(pattern="^(one_time|recurring_daily|daily_sales_report)$")
    message_type: str = Field(pattern="^(text|template|image)$")
    template_name: str | None = None
    template_language: str | None = None
    body_text: str | None = Field(default=None, max_length=4096)
    media_id: str | None = None
    media_link: str | None = None
    send_at: datetime | None = None
    time_of_day: str | None = Field(default=None, pattern="^([01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str = "Asia/Kolkata"
    recipients: list[RecipientSpec] = Field(min_length=1)


class ScheduleUpdate(BaseModel):
    name: str | None = None
    body_text: str | None = None
    template_name: str | None = None
    template_language: str | None = None
    media_id: str | None = None
    media_link: str | None = None
    send_at: datetime | None = None
    time_of_day: str | None = None
    is_enabled: bool | None = None
    is_paused: bool | None = None
    recipients: list[RecipientSpec] | None = None


class ScheduleResponse(BaseModel):
    id: uuid.UUID
    name: str
    schedule_kind: str
    message_type: str
    template_name: str | None
    template_language: str | None
    body_text: str | None
    media_id: str | None
    media_link: str | None
    send_at: datetime | None
    time_of_day: str | None
    timezone: str
    is_enabled: bool
    is_paused: bool
    last_queued_for_date: str | None
    recipient_count: int
    created_at: datetime
    updated_at: datetime


class QueuedMessageResponse(BaseModel):
    id: uuid.UUID
    schedule_id: uuid.UUID | None
    schedule_name: str | None
    contact_id: uuid.UUID
    contact_name: str
    contact_phone: str
    message_type: str
    scheduled_send_at: datetime
    status: str
    provider_message_id: str | None
    failure_reason: str | None
    retry_count: int
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None
    created_at: datetime


class DashboardSummary(BaseModel):
    active_schedules: int
    paused_schedules: int
    pending_messages: int
    sent_today: int
    delivered_today: int
    failed_today: int
    next_report_run_utc: str
    latest_daily_report_status: str
