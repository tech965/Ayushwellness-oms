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
    """Round 9 production incident: a real Celery worker process's task-
    execution boundary saw an *empty* registry (`registered_adapters=[]`)
    even though that same worker's own startup logs proved
    `worker_process_init` had already fired and registered both
    providers in it (`adapters_registered_in_worker_process`, once per
    `ForkPoolWorker-N`). A plain in-memory dict has no legitimate way to
    go from populated back to empty on its own — nothing in this
    codebase ever calls `clear_adapters()` outside tests — so this can
    only be explained by *some* process-execution detail specific to
    Render's deployment that isn't reproducible from a local shell or a
    local test suite (a stale/duplicate process still serving traffic
    from before a deploy, an unusual pool/respawn path, or something
    else entirely unprovable without direct Render access).

    Rather than add a third theory-specific hook and hope it's the right
    one, this makes the failure mode structurally impossible instead:
    `get_adapter` now guarantees that if a real `register()` function
    exists for `code`, this call cannot return `None` for it — a cache
    miss triggers exactly one lazy re-run of `register_all_adapters()`
    *in the calling process, at the exact moment of the lookup that
    needs it*, before giving up. This still isn't "register inside
    `SyncService`" (nothing sync-specific here — every caller of
    `get_adapter`, for every provider, gets the same guarantee for
    free) and it isn't a second registry (same `_ADAPTERS` dict,
    same `register_adapter`).

    Critical: `register_all_adapters()` unconditionally builds a *fresh*
    instance for every provider, which would silently replace an
    already-registered adapter that something else deliberately set up
    — a test's stub HTTP client, or in production, a `ShiprocketAdapter`
    mid-authenticated-session. A self-heal triggered by *one* missing
    provider must never clobber a different, already-correct one, so
    this snapshots what's already registered first and restores it
    over whatever the re-run produced — the re-run can only ever fill
    a genuine gap, never overwrite.
    """
    adapter = _ADAPTERS.get(code)
    if adapter is not None:
        return adapter

    # Local import: `bootstrap` imports the provider packages, which
    # import `register_adapter` from *this* module — importing
    # `bootstrap` at module level here would be a circular import at
    # load time. Safe as a function-local import: by the time anything
    # actually calls `get_adapter`, every module involved has already
    # finished importing.
    from app.integrations.bootstrap import register_all_adapters

    already_registered = dict(_ADAPTERS)

    # info, not warning: this fires for every genuinely-unimplemented
    # provider (Blue Dart, Delhivery, ...) on every lookup, forever, by
    # design — that's expected and not alarming on its own. The signal
    # actually worth alarming on lives where it already did, unchanged:
    # `SyncService.execute_sync`'s `sync_adapter_lookup` log and the
    # `integration_error` SyncError it records if this still comes back
    # `None` for a provider that does have a real adapter.
    logger.info(
        "adapter_registry_self_heal_triggered",
        code_missing=code,
        registered_before=sorted(already_registered),
    )
    register_all_adapters()
    _ADAPTERS.update(already_registered)  # never let the re-run clobber what was already correct
    adapter = _ADAPTERS.get(code)
    logger.info(
        "adapter_registry_self_heal_result",
        code_missing=code,
        found_after_self_heal=adapter is not None,
        registered_after=sorted(_ADAPTERS),
    )
    return adapter


def registered_codes() -> list[str]:
    """Production-safe (unlike `snapshot_adapters`, which is test-only
    and returns live adapter instances) read of what's currently
    registered in *this* process — for diagnostic logging at a sync's
    actual execution boundary, see `SyncService.execute_sync`.
    """
    return sorted(_ADAPTERS)


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
