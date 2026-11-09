"""Route guard for views that are still computed from locally stored provider
data. While SHOPIFY_LIVE_MODE is on they refuse explicitly instead of
returning stale numbers."""

from __future__ import annotations

from app.core.config import settings
from app.core.exceptions import LiveModeUnavailableError


async def unavailable_in_live_mode() -> None:
    if settings.SHOPIFY_LIVE_MODE:
        raise LiveModeUnavailableError(
            "This view is not yet available in live-data mode because it is built from locally "
            "stored order data. Use /analytics/live/summary instead."
        )


def unavailable_in_live_mode_for(hint: str):  # noqa: ANN201
    """Same guard with a hint naming the live replacement (or why none exists)."""

    async def _guard() -> None:
        if settings.SHOPIFY_LIVE_MODE:
            raise LiveModeUnavailableError(
                "This view is not available in live-data mode because it is built from locally "
                f"stored provider data. {hint}"
            )

    return _guard
