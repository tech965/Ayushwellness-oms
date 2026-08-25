"""Shiprocket configuration — reads `SHIPROCKET_*` settings only, never
hardcoded. `ShiprocketConfig.from_settings()` returns `None` when the
account isn't configured (missing email/password) so the adapter can
report "not configured" instead of attempting a login with empty
credentials.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class ShiprocketConfig:
    email: str
    password: str
    api_base_url: str
    pickup_location: str | None

    @classmethod
    def from_settings(cls) -> ShiprocketConfig | None:
        if not settings.SHIPROCKET_EMAIL or not settings.SHIPROCKET_PASSWORD:
            return None
        return cls(
            email=settings.SHIPROCKET_EMAIL,
            password=settings.SHIPROCKET_PASSWORD,
            api_base_url=settings.SHIPROCKET_API_URL,
            pickup_location=settings.SHIPROCKET_PICKUP_LOCATION,
        )
