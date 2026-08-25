"""Runtime registry mapping an `Integration.code` to its `IntegrationAdapter`.

Empty in Phase 2.1 — no provider is implemented yet, so
`SyncService`/`IntegrationService` always see an unregistered adapter and
report `DISCONNECTED`/"no adapter registered" rather than attempting a
live call. Phase 2 registers concrete adapters here (typically at app
startup), e.g.:

    from app.integrations.shopify import ShopifyAdapter
    register_adapter(ShopifyAdapter())
"""

from __future__ import annotations

from app.integrations.base import IntegrationAdapter

_ADAPTERS: dict[str, IntegrationAdapter] = {}


def register_adapter(adapter: IntegrationAdapter) -> None:
    _ADAPTERS[adapter.code] = adapter


def get_adapter(code: str) -> IntegrationAdapter | None:
    return _ADAPTERS.get(code)


def clear_adapters() -> None:
    """Test-only: reset the registry between test cases."""
    _ADAPTERS.clear()


def snapshot_adapters() -> dict[str, IntegrationAdapter]:
    """Test-only: capture the current registry so a test that needs an
    empty/different registry can restore exactly what was there
    afterwards, rather than leaving it permanently cleared for every test
    that runs later in the same process (e.g. the real, but unconfigured,
    adapters `app.workers.celery_app` registers at import time).
    """
    return dict(_ADAPTERS)


def restore_adapters(snapshot: dict[str, IntegrationAdapter]) -> None:
    """Test-only: replace the registry contents with a previously captured
    `snapshot_adapters()` result.
    """
    _ADAPTERS.clear()
    _ADAPTERS.update(snapshot)
