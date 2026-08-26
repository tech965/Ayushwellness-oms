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

from app.core.logging import get_logger
from app.integrations.base import IntegrationAdapter

logger = get_logger(__name__)

_ADAPTERS: dict[str, IntegrationAdapter] = {}


def register_adapter(adapter: IntegrationAdapter) -> None:
    _ADAPTERS[adapter.code] = adapter


def get_adapter(code: str) -> IntegrationAdapter | None:
    return _ADAPTERS.get(code)


async def aclose_all_adapters() -> None:
    """Closes every registered adapter's cached HTTP client. Adapters are
    process-lifetime singletons (registered once at worker startup), so
    their clients must be closed after each Celery task — see
    `ShopifyAdapter.aclose`/`ShiprocketAdapter.aclose` for why. Not every
    `IntegrationAdapter` implementation defines `aclose` (only the ones
    with a real HTTP client do), so this skips those that don't.

    This is best-effort cleanup, not a real operation — closing a
    connection that's already dead can itself raise (confirmed: httpx's
    transport teardown can raise `RuntimeError('Event loop is closed')`
    here, unlike SQLAlchemy's pool, which already swallows the
    equivalent error internally). One adapter failing to close cleanly
    must never stop the others from being closed, and must never crash
    whichever Celery task called this next.
    """
    for adapter in _ADAPTERS.values():
        aclose = getattr(adapter, "aclose", None)
        if aclose is None:
            continue
        try:
            await aclose()
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup, see docstring
            logger.warning("adapter_aclose_failed", adapter=adapter.code, error=str(exc))


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
