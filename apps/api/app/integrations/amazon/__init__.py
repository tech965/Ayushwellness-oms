"""Amazon SP-API integration.

Two independent, non-overlapping pieces live in this package:

1. **Live sales analytics** (`client.py`/`config.py`/`errors.py`/
   `reports.py`, used by `app.api.v1.endpoints.amazon_sales` /
   `app.services.amazon_sales_service`) — read-only report fetches
   against the real SP-API. Nothing here persists provider data: no DB
   models, no Redis, no files.
2. **Marketplace Orders module stub adapter** (`adapter.py`/
   `normalizer.py`, registered below) — a stub `AmazonAdapter`:
   `health_check`/`authenticate` always report "not configured" (no
   `AMAZON_*` credential exists yet via `app.integrations.credentials`),
   `fetch`/`fetch_incremental` always return an empty page, and
   `process_webhook` raises `NotImplementedError`. This mirrors
   `app.integrations.shopify`'s own "register unconditionally, report a
   clean not-configured outcome" pattern — so the Amazon row already
   appears in the admin Integrations list (status DISCONNECTED) rather
   than the generic "no adapter registered" error. A later phase
   replaces this adapter's internals with real SP-API calls (OAuth/LWA
   auth, Orders API pagination, `AmazonOrderNormalizer.normalize`)
   without touching `SyncService`, `app.integrations.entity_sync`, the
   Marketplace Orders API/UI, or anything else that already depends on
   this adapter purely through the `IntegrationAdapter` interface.
"""

from __future__ import annotations

from app.integrations.amazon.adapter import AmazonAdapter
from app.integrations.registry import register_adapter

__all__ = ["AmazonAdapter", "register"]


def register() -> None:
    register_adapter(AmazonAdapter())
