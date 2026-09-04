"""OMS Settings — Administration -> Settings.

Every section below is a real, applied preference (see
`docs/architecture` for which frontend surfaces read which field), not a
decorative toggle: `dashboard.refresh_interval_seconds` drives the
dashboard's actual polling interval, `general.default_page_size` is the
fallback every list page's pagination hook reads, `security.
session_timeout_minutes` drives the frontend's idle-logout timer, and
`integrations`/connection status is read live from the existing
`Integration` table rather than duplicated here. One row of `AppSettings.
values` (a JSON blob) is validated against this schema on read and on
write -- see `app/services/settings_service.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GeneralSettings(BaseModel):
    organization_name: str = "AyushWellness"
    oms_display_name: str = "AyushWellness OMS"
    default_timezone: Literal["Asia/Kolkata"] = "Asia/Kolkata"
    currency: str = "INR"
    date_format: Literal["DD MMM YYYY", "MM/DD/YYYY", "DD/MM/YYYY", "YYYY-MM-DD"] = "DD MMM YYYY"
    default_page_size: int = Field(default=20, ge=5, le=200)


class OrderSettings(BaseModel):
    default_order_status: Literal[
        "pending", "confirmed", "processing", "packed", "shipped", "delivered"
    ] = "pending"
    auto_refresh_interval_seconds: int = Field(default=0, ge=0, le=3600)
    default_sort_field: Literal["order_datetime", "total_amount", "order_number"] = (
        "order_datetime"
    )
    default_sort_direction: Literal["asc", "desc"] = "desc"


class NotificationSettings(BaseModel):
    email_order_notifications: bool = True
    email_shipment_notifications: bool = True
    email_return_refund_notifications: bool = True


class ShippingSettings(BaseModel):
    default_courier_id: uuid.UUID | None = None
    tracking_refresh_interval_minutes: int = Field(default=60, ge=5, le=1440)


class DashboardSettings(BaseModel):
    default_date_range: Literal[
        "today", "yesterday", "this_week", "last_7_days", "last_30_days", "this_month"
    ] = "last_30_days"
    default_chart_interval: Literal["day", "week", "month"] = "day"
    # 0 = disabled (no auto-refresh).
    refresh_interval_seconds: int = Field(default=0, ge=0, le=3600)


class SecuritySettings(BaseModel):
    # 0 = disabled (no idle logout).
    session_timeout_minutes: int = Field(default=60, ge=0, le=1440)


class AppearanceSettings(BaseModel):
    table_density: Literal["compact", "comfortable"] = "comfortable"


class AppSettingsData(BaseModel):
    general: GeneralSettings = GeneralSettings()
    orders: OrderSettings = OrderSettings()
    notifications: NotificationSettings = NotificationSettings()
    shipping: ShippingSettings = ShippingSettings()
    dashboard: DashboardSettings = DashboardSettings()
    security: SecuritySettings = SecuritySettings()
    appearance: AppearanceSettings = AppearanceSettings()


class AppSettingsUpdateRequest(BaseModel):
    """Every section is optional -- the frontend saves one section (one
    card's "Save" button) at a time; sections omitted here keep their
    current stored value untouched.
    """

    general: GeneralSettings | None = None
    orders: OrderSettings | None = None
    notifications: NotificationSettings | None = None
    shipping: ShippingSettings | None = None
    dashboard: DashboardSettings | None = None
    security: SecuritySettings | None = None
    appearance: AppearanceSettings | None = None


class AppSettingsResponse(BaseModel):
    settings: AppSettingsData
    updated_at: datetime | None
    updated_by_email: str | None
