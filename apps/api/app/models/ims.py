"""Inventory Management System (IMS) tables.

Physical stock held by the IMS is tracked per (warehouse, item) where an
item is either a finished-goods SKU (identified by its Shopify variant id;
catalogue data itself is read live from Shopify, only an identity/label is
kept here) or one packaging unit (BOX/OUTER/SACHETS/ZIPPER/BOTTLE/JAR) of a
packaging product. The two kinds are never combined in one quantity.

`ims_ledger` is the append-only source of truth; `ims_balances` is the
running balance maintained in the same transaction under a row lock. A
unit conversion is a *display* relationship only -- nothing here consumes or
produces components automatically.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.db.base import AwareDateTime, Base, TimestampMixin, UUIDPrimaryKeyMixin

PACKAGING_UNITS = ("BOX", "OUTER", "SACHETS", "ZIPPER", "BOTTLE", "JAR")

KIND_FINISHED = "finished"
KIND_PACKAGING = "packaging"

MOVEMENT_TYPES = (
    "opening_balance",
    "inward",
    "outward",
    "transfer_out",
    "transfer_in",
    "return_in",
    "adjustment_in",
    "adjustment_out",
    "damaged",
    "missing",
)

ADJUSTMENT_TYPES = ("increase", "decrease", "damaged", "missing", "count_correction", "other")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ImsPackagingProduct(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A product whose packaging materials are tracked (e.g. "New Blue Sachets")."""

    __tablename__ = "ims_packaging_products"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shopify_product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImsItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ims_items"
    __table_args__ = (
        UniqueConstraint("shopify_variant_id", name="uq_ims_items_shopify_variant"),
        UniqueConstraint("packaging_product_id", "unit", name="uq_ims_items_pkg_unit"),
        CheckConstraint(_in("kind", (KIND_FINISHED, KIND_PACKAGING)), name="ck_ims_items_kind"),
        CheckConstraint(
            "(kind = 'finished' AND shopify_variant_id IS NOT NULL AND unit IS NULL "
            "AND packaging_product_id IS NULL) OR "
            "(kind = 'packaging' AND packaging_product_id IS NOT NULL AND unit IS NOT NULL "
            "AND shopify_variant_id IS NULL)",
            name="ck_ims_items_shape",
        ),
        CheckConstraint(f"unit IS NULL OR {_in('unit', PACKAGING_UNITS)}", name="ck_ims_items_unit"),
        Index("ix_ims_items_kind", "kind"),
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    shopify_product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shopify_variant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    packaging_product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ims_packaging_products.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Total-across-warehouses level at/below which the item is "low stock".
    # NULL: finished goods use settings.IMS_DEFAULT_LOW_STOCK_THRESHOLD,
    # packaging is not low-stock tracked (units differ by orders of magnitude).
    low_stock_threshold: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    packaging_product: Mapped[ImsPackagingProduct | None] = relationship()


class ImsUnitConversion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """`1 larger_unit = ratio smaller_unit` for one packaging product."""

    __tablename__ = "ims_unit_conversions"
    __table_args__ = (
        UniqueConstraint(
            "packaging_product_id", "larger_unit", "smaller_unit", name="uq_ims_conversion_pair"
        ),
        CheckConstraint("ratio > 0", name="ck_ims_conversion_ratio_positive"),
        CheckConstraint("larger_unit <> smaller_unit", name="ck_ims_conversion_distinct"),
        CheckConstraint(
            f"{_in('larger_unit', PACKAGING_UNITS)} AND {_in('smaller_unit', PACKAGING_UNITS)}",
            name="ck_ims_conversion_units",
        ),
    )

    packaging_product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_packaging_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    larger_unit: Mapped[str] = mapped_column(String(20), nullable=False)
    smaller_unit: Mapped[str] = mapped_column(String(20), nullable=False)
    ratio: Mapped[int] = mapped_column(Integer, nullable=False)


class ImsBalance(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ims_balances"
    __table_args__ = (
        UniqueConstraint("warehouse_id", "item_id", name="uq_ims_balances_wh_item"),
        CheckConstraint("quantity >= 0", name="ck_ims_balances_non_negative"),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ImsExternalParty(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ims_external_parties"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class ImsTransfer(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ims_transfers"
    __table_args__ = (
        CheckConstraint(
            "source_warehouse_id <> destination_warehouse_id", name="ck_ims_transfers_distinct"
        ),
        Index("ix_ims_transfers_created_at", "created_at"),
    )

    reference_no: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    source_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    destination_warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )

    lines: Mapped[list[ImsTransferLine]] = relationship(
        back_populates="transfer", order_by="ImsTransferLine.id"
    )


class ImsTransferLine(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ims_transfer_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_ims_transfer_lines_qty"),)

    transfer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_transfers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)

    transfer: Mapped[ImsTransfer] = relationship(back_populates="lines")
    item: Mapped[ImsItem] = relationship()


class ImsReturn(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ims_returns"
    __table_args__ = (Index("ix_ims_returns_return_date", "return_date"),)

    reference_no: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_external_parties.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    return_date: Mapped[date] = mapped_column(Date, nullable=False)
    document_no: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )

    lines: Mapped[list[ImsReturnLine]] = relationship(
        back_populates="ims_return", order_by="ImsReturnLine.id"
    )


class ImsReturnLine(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ims_return_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_ims_return_lines_qty"),)

    return_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_returns.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)

    ims_return: Mapped[ImsReturn] = relationship(back_populates="lines")
    item: Mapped[ImsItem] = relationship()


class ImsAdjustment(Base, UUIDPrimaryKeyMixin):
    """Immutable audit record of one manual stock adjustment."""

    __tablename__ = "ims_adjustments"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_ims_adjustments_qty"),
        CheckConstraint(_in("adjustment_type", ADJUSTMENT_TYPES), name="ck_ims_adjustments_type"),
        CheckConstraint(_in("direction", ("in", "out")), name="ck_ims_adjustments_direction"),
        CheckConstraint("length(reason) > 0", name="ck_ims_adjustments_reason"),
    )

    reference_no: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_items.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    adjustment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # RESTRICT (not SET NULL): this table is append-only.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False, index=True
    )

    item: Mapped[ImsItem] = relationship()


class ImsLedger(Base, UUIDPrimaryKeyMixin):
    """Append-only stock movement ledger (never updated or deleted)."""

    __tablename__ = "ims_ledger"
    __table_args__ = (
        CheckConstraint(_in("movement_type", MOVEMENT_TYPES), name="ck_ims_ledger_type"),
        CheckConstraint("quantity_delta <> 0", name="ck_ims_ledger_delta_nonzero"),
        CheckConstraint("quantity_after >= 0", name="ck_ims_ledger_after_non_negative"),
        CheckConstraint(
            "quantity_after = quantity_before + quantity_delta", name="ck_ims_ledger_arithmetic"
        ),
        Index("ix_ims_ledger_wh_item_occurred", "warehouse_id", "item_id", "occurred_at"),
        Index("ix_ims_ledger_occurred", "occurred_at"),
        Index("ix_ims_ledger_reference", "reference_no"),
    )

    movement_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ims_items.id", ondelete="RESTRICT"), nullable=False
    )
    quantity_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(40), nullable=True)
    transfer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ims_transfers.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    return_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ims_returns.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    adjustment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ims_adjustments.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    # Business time of the movement (what historical reports key on) vs the
    # time the row was written.
    occurred_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    item: Mapped[ImsItem] = relationship()


@event.listens_for(Session, "before_flush")
def _ledger_is_append_only(session: Session, flush_context, instances) -> None:  # noqa: ANN001
    for obj in list(session.dirty):
        if isinstance(obj, ImsLedger | ImsAdjustment) and session.is_modified(obj):
            raise ValueError("IMS ledger and adjustment records are immutable.")
    for obj in session.deleted:
        if isinstance(obj, ImsLedger | ImsAdjustment):
            raise ValueError("IMS ledger and adjustment records cannot be deleted.")
