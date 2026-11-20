"""NimbusPost integration — authentication + Test Connection phase
(2026-11-20).

Contains: `NimbusPostClient` (HTTP + token management), `NimbusPostConfig`
(env-var config), `NimbusPostAdapter` (implements
`app.integrations.base.IntegrationAdapter`). Courier operations
(serviceability, shipment creation, AWB, tracking, cancellation) are a
later phase — see the adapter's module docstring for why.
"""

from __future__ import annotations

from app.integrations.nimbuspost.adapter import NimbusPostAdapter
from app.integrations.registry import register_adapter

__all__ = ["NimbusPostAdapter", "register"]


def register() -> None:
    """Registers a `NimbusPostAdapter` regardless of whether credentials
    are configured — mirrors `app.integrations.shiprocket.register()`.
    """
    register_adapter(NimbusPostAdapter())
