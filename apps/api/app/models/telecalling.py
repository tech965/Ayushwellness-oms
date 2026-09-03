"""OrderAssignment, CallAttempt — the Team Leader / Telecaller workflow.

`OrderAssignment` tracks *who is currently supposed to be calling about
this order* — an order has at most one row with `assignment_status ==
ACTIVE` at a time (enforced by the partial unique index below and, more
strictly, by `TelecallingService`'s single-transaction assign/reassign
logic). A reassignment never deletes or edits the old row; it flips it to
INACTIVE and inserts a new ACTIVE one, so the full assignment history is
always reconstructable by listing every `OrderAssignment` for an
`order_id`.

`current_status`/`attempt_count`/`last_attempt_at`/`next_follow_up_at` on
`OrderAssignment` are denormalized from `CallAttempt` — kept in sync in
the same transaction/commit that inserts a new `CallAttempt` — so that
list/filter/aggregate queries (call-status filters, today's follow-ups,
telecaller performance counts) are plain indexed-column comparisons
against `order_assignments`, never a per-row subquery into
`call_attempts`. `CallAttempt` itself is append-only and remains the
full source of truth for call history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AssignmentStatus, TelecallingStatus, sa_enum

if TYPE_CHECKING:
    from app.models.abandoned_checkout import AbandonedCheckout
    from app.models.auth import User
    from app.models.order import Order


class OrderAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "order_assignments"
    __table_args__ = (
        # Defense-in-depth for the "one active assignment per order"
        # invariant — the service layer enforces it transactionally
        # (deactivate-then-insert, one commit), this is the DB-level
        # backstop against a race or a bug bypassing the service.
        Index(
            "uq_order_assignments_one_active_per_order",
            "order_id",
            unique=True,
            postgresql_where=text("assignment_status = 'active'"),
            sqlite_where=text("assignment_status = 'active'"),
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_to: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    # Denormalized from `assigned_to`'s `User.team_leader_id` at
    # assignment time, so every team-scoped query (Team Leader dashboard,
    # "team orders", telecaller performance) is a plain indexed-column
    # comparison — never a join back to `users` just to find the team.
    team_leader_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignment_status: Mapped[AssignmentStatus] = mapped_column(
        sa_enum(AssignmentStatus, "assignment_status"),
        nullable=False,
        default=AssignmentStatus.ACTIVE,
        index=True,
    )

    # Populated only on a row created by `TelecallingService.reassign_order`.
    reassigned_from: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reassigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reassigned_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    reassignment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Denormalized calling state for this assignment — see module docstring.
    current_status: Mapped[TelecallingStatus] = mapped_column(
        sa_enum(TelecallingStatus, "telecalling_status"),
        nullable=False,
        default=TelecallingStatus.NOT_CALLED,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True, index=True
    )

    order: Mapped[Order] = relationship()
    telecaller: Mapped[User] = relationship(foreign_keys=[assigned_to])


class CallAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Append-only call-attempt log. No update/delete path is exposed by
    `CallAttemptRepository` — every call creates a new row, never edits a
    previous one (spec: "Do not overwrite previous attempts").
    """

    __tablename__ = "call_attempts"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telecaller_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Continuous per-order sequence (not per-assignment) — if a
    # reassignment happens after 2 attempts, the new telecaller's first
    # call is still "Attempt #3": full call history for an order stays one
    # timeline regardless of who's calling.
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    outcome: Mapped[TelecallingStatus] = mapped_column(
        sa_enum(TelecallingStatus, "telecalling_status"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    order: Mapped[Order] = relationship()


class CheckoutAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """`OrderAssignment`'s exact counterpart for an `AbandonedCheckout`
    lead — same shape, same one-active-assignment-per-subject invariant,
    same denormalized-from-`CheckoutCallAttempt` calling state (see the
    module docstring above for why: list/filter/aggregate queries stay
    plain indexed-column comparisons, never a per-row subquery).

    Kept as a genuinely separate table from `OrderAssignment` rather than
    a polymorphic `order_id`/`checkout_id` pair on one table: an abandoned
    checkout is not an order and never becomes one by being assigned
    (spec: "do not automatically create a fake order" on conversion), and
    every existing `OrderAssignment` query/index/test is untouched by this
    addition — see `docs/architecture/integrations.md` and the Telecalling
    Module delivery notes for the tradeoff.
    """

    __tablename__ = "checkout_assignments"
    __table_args__ = (
        Index(
            "uq_checkout_assignments_one_active_per_checkout",
            "checkout_id",
            unique=True,
            postgresql_where=text("assignment_status = 'active'"),
            sqlite_where=text("assignment_status = 'active'"),
        ),
    )

    checkout_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("abandoned_checkouts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_to: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    team_leader_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignment_status: Mapped[AssignmentStatus] = mapped_column(
        sa_enum(AssignmentStatus, "assignment_status"),
        nullable=False,
        default=AssignmentStatus.ACTIVE,
        index=True,
    )

    reassigned_from: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reassigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reassigned_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    reassignment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    current_status: Mapped[TelecallingStatus] = mapped_column(
        sa_enum(TelecallingStatus, "telecalling_status"),
        nullable=False,
        default=TelecallingStatus.NOT_CALLED,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True, index=True
    )

    checkout: Mapped[AbandonedCheckout] = relationship()
    telecaller: Mapped[User] = relationship(foreign_keys=[assigned_to])


class CheckoutCallAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """`CallAttempt`'s exact counterpart for a `CheckoutAssignment` — same
    append-only, never-overwritten call log.
    """

    __tablename__ = "checkout_call_attempts"

    checkout_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("abandoned_checkouts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    telecaller_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    outcome: Mapped[TelecallingStatus] = mapped_column(
        sa_enum(TelecallingStatus, "telecalling_status"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    checkout: Mapped[AbandonedCheckout] = relationship()
