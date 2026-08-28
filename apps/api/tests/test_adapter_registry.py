"""Adapter registration — `register_all_adapters()` -> `get_adapter()`.

No test previously covered this at all, despite it being the exact
thing `SyncService.execute_sync` and the scheduled-sync enqueue logic
(`app.tasks.sync_tasks._run_scheduled_sync`) depend on to decide whether
a provider is implemented. Added after investigating a reported
production `SyncError` — "No adapter registered for integration
'shiprocket'" — which did **not** reproduce against this codebase:
`app.integrations.shiprocket.register()` already registers a
`ShiprocketAdapter` unconditionally, following the identical pattern
`app.integrations.shopify.register()` uses (see both modules'
docstrings). These tests lock that behavior in going forward.
"""

from __future__ import annotations

import pytest
from app.integrations.bootstrap import register_all_adapters
from app.integrations.registry import (
    clear_adapters,
    get_adapter,
    restore_adapters,
    snapshot_adapters,
)
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.integration import IntegrationCode


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    """Snapshot/restore, not clear-and-leave-empty — the registry is a
    single process-wide dict, and `app.workers.celery_app`'s module
    import (which every test process triggers once, indirectly) already
    populates it for the rest of the suite. A bare `clear_adapters()`
    teardown here would leave it empty for every test that runs
    afterward in the same process, not just this file's own tests —
    exactly the kind of test pollution `snapshot_adapters()`/
    `restore_adapters()` exist to prevent (see their docstrings).
    """
    snapshot = snapshot_adapters()
    yield
    restore_adapters(snapshot)


def test_register_all_adapters_registers_shiprocket() -> None:
    clear_adapters()
    register_all_adapters()

    adapter = get_adapter(IntegrationCode.SHIPROCKET)

    assert adapter is not None
    assert isinstance(adapter, ShiprocketAdapter)


def test_register_all_adapters_registers_shopify() -> None:
    clear_adapters()
    register_all_adapters()

    adapter = get_adapter(IntegrationCode.SHOPIFY)

    assert adapter is not None
    assert isinstance(adapter, ShopifyAdapter)


def test_shiprocket_is_registered_regardless_of_whether_credentials_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors `test_shopify_...` coverage of the same guarantee — a
    provider with no `SHIPROCKET_EMAIL`/`SHIPROCKET_PASSWORD` configured
    must still be registered (reporting "not configured" is the adapter's
    job at call time, not the registry's job at startup) rather than
    silently absent, which is what produces the misleading
    "no adapter registered" error instead of an accurate
    "not configured" one.
    """
    from app.integrations.shiprocket.config import ShiprocketConfig

    monkeypatch.setattr(ShiprocketConfig, "from_settings", classmethod(lambda cls: None))
    clear_adapters()
    register_all_adapters()

    adapter = get_adapter(IntegrationCode.SHIPROCKET)
    assert adapter is not None
    assert isinstance(adapter, ShiprocketAdapter)


def test_get_adapter_returns_none_for_an_unimplemented_provider() -> None:
    """The registry itself doesn't invent adapters for providers that
    genuinely have none yet (Blue Dart, Delhivery, ...) — this is the
    one case `get_adapter` returning `None` is correct, not a bug.
    """
    clear_adapters()
    register_all_adapters()

    assert get_adapter(IntegrationCode.BLUE_DART) is None
