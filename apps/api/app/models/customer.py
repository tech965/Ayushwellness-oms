"""Customer and CustomerAddress.

Primary source is Shopify starting Phase 2 (`SyncMetadataMixin` +
`shopify_customer_id`); Phase 1 exposes internal CRUD for development,
administration, and manual operational corrections — see
`docs/database/schema.md`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AddressType, sa_enum
from app.models.mixins import SyncMetadataMixin


class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_customers_source_external_id"),
    )

    shopify_customer_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    alternate_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    addresses: Mapped[list[CustomerAddress]] = relationship(
        back_populates="customer", cascade="all, delete-orphan"
    )


class CustomerAddress(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "customer_addresses"
    __table_args__ = (
        UniqueConstraint(
            "source_system", "external_id", name="uq_customer_addresses_source_external_id"
        ),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    address_type: Mapped[AddressType] = mapped_column(
        sa_enum(AddressType, "address_type"), nullable=False, default=AddressType.SHIPPING
    )
    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str] = mapped_column(
        String(120), nullable=False, default="India", server_default="India"
    )
    pin_code: Mapped[str] = mapped_column(String(16), nullable=False)
    landmark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    customer: Mapped[Customer] = relationship(back_populates="addresses")
