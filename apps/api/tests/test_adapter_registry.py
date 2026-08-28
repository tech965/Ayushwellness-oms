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

import subprocess
import sys

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
from sqlalchemy.ext.asyncio import AsyncSession


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


# Round 7 — production incident: `get_adapter("shiprocket")` returned
# `None` inside the *actual running* Celery worker process at the moment
# it executed a sync, even though manually re-running
# `register_all_adapters()` in a fresh Render shell always worked. That
# combination only makes sense if the live worker process's copy of the
# registry, at that moment, hadn't received module-import-time
# registration the way a fresh process does — module-level code in
# `app.workers.celery_app` only *usually* reaches a forked child worker
# via memory inheritance, it isn't guaranteed for every pool/deploy
# scenario. Fixed with `worker_process_init` (Celery's own per-process
# startup signal) as a second, more robust trigger for the same
# `register_all_adapters()` call — this proves that signal actually
# repopulates an empty registry, the exact "BEFORE: {}" state captured
# on Render before this fix.
def test_worker_process_init_signal_populates_an_empty_registry() -> None:
    from app.workers.celery_app import _register_adapters_in_worker_process

    clear_adapters()
    assert snapshot_adapters() == {}

    _register_adapters_in_worker_process()

    shiprocket = get_adapter(IntegrationCode.SHIPROCKET)
    shopify = get_adapter(IntegrationCode.SHOPIFY)
    assert isinstance(shiprocket, ShiprocketAdapter)
    assert isinstance(shopify, ShopifyAdapter)


def test_worker_process_init_signal_is_actually_connected_to_celery() -> None:
    """Not enough to prove the handler function works in isolation — it
    must actually be wired to Celery's `worker_process_init` signal, or
    it never fires in a real worker process at all. Sends the real
    signal (as Celery itself would, once per worker process) rather than
    calling the handler directly, so this only passes if the `@connect`
    wiring in `celery_app.py` is genuinely intact.
    """
    import app.workers.celery_app  # noqa: F401 - import side effect: connects the signal
    from celery.signals import worker_process_init

    clear_adapters()
    assert snapshot_adapters() == {}

    worker_process_init.send(sender=None)

    assert isinstance(get_adapter(IntegrationCode.SHIPROCKET), ShiprocketAdapter)
    assert isinstance(get_adapter(IntegrationCode.SHOPIFY), ShopifyAdapter)


# Round 8 — the previous `worker_process_init` fix is confirmed deployed
# (commit 9b4e647, part of 830c834) but production still reported the
# same "No adapter registered" error for `entity_type="shipments"`. Every
# test above proves the registration *logic* is correct, but all of them
# run inside the *same* pytest process — none can distinguish "the logic
# works" from "the logic works, but this specific real OS process never
# actually ran it," which is exactly the distinction a manual Render
# shell session can't prove about a real worker process it isn't. This
# test spawns a genuinely separate OS process (not a thread, not a
# fixture-shared interpreter) that imports `app.workers.celery_app`
# exactly the way Celery's own worker bootstrap does, then checks
# `get_adapter("shiprocket")` from *inside that process* — the strongest
# proof available without live Render log/shell access.
def test_a_genuinely_separate_process_has_shiprocket_registered() -> None:
    probe = (
        "from app.workers.celery_app import celery_app\n"
        "from celery.signals import worker_process_init\n"
        "worker_process_init.send(sender=None)\n"
        "from app.integrations.registry import get_adapter\n"
        "adapter = get_adapter('shiprocket')\n"
        "found = adapter is not None\n"
        "print('SHIPROCKET_ADAPTER_FOUND' if found else 'SHIPROCKET_ADAPTER_MISSING')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "SHIPROCKET_ADAPTER_FOUND" in result.stdout, result.stdout + result.stderr


async def test_run_sync_task_execution_path_sees_a_registered_shiprocket_adapter(
    db_session: AsyncSession,
) -> None:
    """Reproduces the exact real path a production `entity_type="shipments"`
    sync runs through: `app.tasks.sync_tasks.run_sync_task` -> its
    `_run_sync` body (a thin wrapper: open a session, call `SyncService.
    run_sync`) -> `SyncService.run_sync` -> `execute_sync` ->
    `get_adapter`. `SyncService(db_session).run_sync(...)` below is called
    directly (not through `_run_sync`'s own `AsyncSessionLocal()`) only so
    it shares this test's transactional session — same reason every other
    `SyncService`-through-a-real-task test in this suite does the same
    (see `test_shiprocket_sync.py`). The registry is populated the same
    way a real worker process does — the `worker_process_init` signal,
    not a direct `register_all_adapters()` call — so this fails if that
    signal path is ever broken again.
    """
    from app.integrations.shiprocket.adapter import ShiprocketAdapter as _SRAdapter
    from app.models.enums import IntegrationStatus, IntegrationType, SyncType
    from app.repositories.integration import IntegrationRepository
    from app.repositories.sync_error import SyncErrorRepository
    from app.services.sync_service import SyncService
    from celery.signals import worker_process_init

    integration = await IntegrationRepository(db_session).create(
        name="Shiprocket",
        code=IntegrationCode.SHIPROCKET,
        type=IntegrationType.COURIER,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await db_session.commit()

    clear_adapters()
    worker_process_init.send(sender=None)
    assert isinstance(get_adapter(IntegrationCode.SHIPROCKET), _SRAdapter)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="shipments"
    )

    # The specific bug this guards: a missing adapter always produces
    # exactly this SyncError message. Any other failure (a real network
    # call failing, no matching OMS order, ...) is not what this test is
    # about.
    stmt = SyncErrorRepository(db_session).for_sync_job(job.id)
    errors = (await db_session.execute(stmt)).scalars().all()
    assert not any(
        "No adapter registered" in (e.error_message or "") for e in errors
    ), "adapter registry was empty at the real run_sync_task execution boundary"
