"""Shiprocket integration — Phase 2.3.

Contains: `ShiprocketClient` (HTTP + token management), `ShiprocketConfig`
(env-var config), `ShiprocketAdapter` (implements
`app.integrations.base.IntegrationAdapter` plus concrete push
capabilities), normalizers (pull: tracking/NDR; push: order-create
payload), and `sync.py` (the OMS-shipment-driven tracking refresh
orchestration). See `docs/architecture/integrations.md` and
`docs/integrations/shiprocket.md` for the full data-flow documentation.
"""

from __future__ import annotations

from app.integrations.registry import register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter

__all__ = ["ShiprocketAdapter", "register"]


def register() -> None:
    """Registers a `ShiprocketAdapter` regardless of whether credentials
    are configured — mirrors `app.integrations.shopify.register()`.
    """
    register_adapter(ShiprocketAdapter())
