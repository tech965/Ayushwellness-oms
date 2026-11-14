"""WhatsApp automation — OMS-owned scheduling/queue/delivery-history data.

Deliberately holds no provider order/customer snapshots: these tables
only track *this OMS's own* outbound-message intent (what to send, to
whom, when) and the delivery lifecycle Meta's webhooks report back.
Message bodies/template params are stored because they ARE the OMS's
own composed content, not a copy of someone else's data; the one
provider-shaped payload in here, `WhatsAppQueuedMessage.payload`, is
what *this app* built to send -- not something fetched from Meta.

`WhatsAppContact` is a small internal directory (staff/stakeholders
this feature notifies — Management/Sales/Marketing teams per the spec)
deliberately separate from `Customer`: this feature's recipients are
not necessarily OMS customers, and giving it its own table keeps the
WhatsApp opt-in/opt-out state (a messaging-compliance concern) from
leaking into the customer model.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    pass


class WhatsAppContact(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "whatsapp_contacts"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # E.164, e.g. "919991234567" -- validated at the schema layer before
    # a row is ever created (app.services.whatsapp_contact_service).
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    opted_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    opted_out_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class WhatsAppRecipientGroup(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "whatsapp_recipient_groups"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)

    members: Mapped[list[WhatsAppRecipientGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class WhatsAppRecipientGroupMember(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "whatsapp_recipient_group_members"
    __table_args__ = (UniqueConstraint("group_id", "contact_id", name="uq_group_contact"),)

    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_recipient_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    group: Mapped[WhatsAppRecipientGroup] = relationship(back_populates="members")
    contact: Mapped[WhatsAppContact] = relationship()


class WhatsAppSchedule(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "whatsapp_schedules"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # "one_time" | "recurring_daily" | "daily_sales_report"
    schedule_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # "text" | "template" | "image"
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    template_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    template_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    body_text: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    media_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    media_link: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # One-time: exact UTC instant. Recurring/daily-report: "HH:MM" in
    # `timezone` (default Asia/Kolkata per the spec) -- the Celery task
    # computes each day's UTC instant from this, never stores a UTC time
    # for a recurring schedule (that would silently drift across DST-like
    # policy changes to IST, which has none today, but keeps the stored
    # intent — "9 AM local" — honest either way).
    send_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    time_of_day: Mapped[str | None] = mapped_column(String(5), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="Asia/Kolkata")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Guards the recurring/daily-report queuer against enqueuing the same
    # day's run twice (e.g. a beat misfire, a worker restart mid-tick).
    last_queued_for_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)

    recipients: Mapped[list[WhatsAppScheduleRecipient]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class WhatsAppScheduleRecipient(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "whatsapp_schedule_recipients"

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Exactly one of these two is set -- enforced in the service layer
    # (an individual contact, or an entire recipient group).
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"), nullable=True
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_recipient_groups.id", ondelete="CASCADE"), nullable=True
    )

    schedule: Mapped[WhatsAppSchedule] = relationship(back_populates="recipients")
    contact: Mapped[WhatsAppContact | None] = relationship()
    group: Mapped[WhatsAppRecipientGroup | None] = relationship()


class WhatsAppQueuedMessage(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "whatsapp_queued_messages"
    __table_args__ = (
        Index("ix_wa_queue_due", "status", "scheduled_send_at"),
        UniqueConstraint("idempotency_key", name="uq_wa_queue_idempotency"),
    )

    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("whatsapp_schedules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # The exact composed message this app is sending -- template name +
    # component parameters, or text body, or image id/link+caption. Not
    # a copy of provider data; this is what the OMS itself generated.
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    # Deterministic per (schedule_id or "adhoc", contact_id, send slot) --
    # see `whatsapp_queue_service.build_idempotency_key`. A duplicate
    # queuer call (retry, double-click, a re-run beat tick) collides on
    # this unique constraint instead of creating a second send.
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    scheduled_send_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    # QUEUED | SENDING | SENT | DELIVERED | READ | FAILED | CANCELLED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED", index=True)
    provider_message_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    failure_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    sent_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)

    contact: Mapped[WhatsAppContact] = relationship()
    schedule: Mapped[WhatsAppSchedule | None] = relationship()


class WhatsAppCustomerWindow(Base, UUIDPrimaryKeyMixin):
    """Tracks Meta's 24-hour customer service window per contact: the
    timestamp of their most recent inbound message/call, updated only by
    the inbound webhook. A free-form (non-template) send is only valid
    while `now - last_inbound_at < 24h` -- see
    `whatsapp_send_service.window_is_open`.
    """

    __tablename__ = "whatsapp_customer_windows"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    last_inbound_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)

    # Whole-delivery idempotency (on top of the provider_message_id dedup
    # above) reuses the existing `WebhookDelivery` ledger
    # (`app.models.webhook_delivery`, provider="whatsapp") rather than a
    # new table -- see `app.services.whatsapp_webhook_processor`.
