"""Cashfree configuration — reads `CASHFREE_*` settings only, never
hardcoded. `CashfreeConfig.from_settings()` returns `None` when the
account isn't configured (missing client id/secret) so callers can
report "not configured" instead of calling Cashfree with empty
credentials.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class CashfreeConfig:
    client_id: str
    client_secret: str
    api_version: str
    base_url: str
    # Falls back to `client_secret` in `app.integrations.cashfree.webhooks`
    # when unset — see that module and `Settings.CASHFREE_WEBHOOK_SECRET`.
    webhook_secret: str | None
    return_url: str | None

    @property
    def environment(self) -> str:
        """`"sandbox"` or `"production"` — derived from `base_url` rather
        than a second, independently-configurable setting that could
        drift out of sync with it. Passed to the frontend (see
        `app.schemas.cashfree.CashfreeCheckoutResponse.mode`) so the
        Cashfree Checkout JS SDK is initialized with the SAME
        environment the order was actually created in — the SDK errors
        if a sandbox `payment_session_id` is opened in `"production"`
        mode or vice versa.
        """
        return "sandbox" if "sandbox" in self.base_url else "production"

    @classmethod
    def from_settings(cls) -> CashfreeConfig | None:
        if not settings.CASHFREE_CLIENT_ID or not settings.CASHFREE_CLIENT_SECRET:
            return None
        return cls(
            client_id=settings.CASHFREE_CLIENT_ID,
            client_secret=settings.CASHFREE_CLIENT_SECRET,
            api_version=settings.CASHFREE_API_VERSION,
            base_url=settings.CASHFREE_BASE_URL,
            webhook_secret=settings.CASHFREE_WEBHOOK_SECRET,
            return_url=settings.CASHFREE_RETURN_URL,
        )
