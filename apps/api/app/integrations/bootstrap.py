"""Registers every implemented `IntegrationAdapter` into
`app.integrations.registry`. Called once from both process entrypoints
that need adapters available — `app.main` (FastAPI) and
`app.workers.celery_app` (Celery worker) — since the registry is an
in-memory, per-process dict.

Add a new provider here as it's implemented; nothing else needs to
change to make a newly-registered adapter visible to
`SyncService`/`IntegrationService`/the webhook processing task.
"""

from __future__ import annotations

import app.integrations.shiprocket as shiprocket_integration
import app.integrations.shopify as shopify_integration


def register_all_adapters() -> None:
    shopify_integration.register()
    shiprocket_integration.register()
