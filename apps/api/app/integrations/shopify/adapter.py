"""ShopifyAdapter — the only Shopify-specific implementation of
`app.integrations.base.IntegrationAdapter`. Contains Shopify behavior
only: no generic sync/retry/idempotency logic lives here (that's
`SyncService`/`WebhookService`, reused unchanged from Phase 2.1).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.core.exceptions import IntegrationError
from app.integrations.base import FetchPage, HealthCheckResult, IntegrationAdapter
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.config import ShopifyConfig
from app.integrations.shopify.errors import ShopifyApiError
from app.integrations.shopify.normalizer import ENTITY_NORMALIZERS
from app.integrations.shopify.queries import ENTITY_QUERIES, SHOP_PING_QUERY, updated_since_filter
from app.integrations.shopify.webhook_shapes import WEBHOOK_SHAPE_TRANSLATORS
from app.models.integration import IntegrationCode


class ShopifyAdapter(IntegrationAdapter):
    code = IntegrationCode.SHOPIFY

    def __init__(self, client: ShopifyClient | None = None) -> None:
        self._client = client
        self._configured = client is not None or ShopifyConfig.from_settings() is not None

    def _get_client(self) -> ShopifyClient:
        if self._client is not None:
            return self._client
        config = ShopifyConfig.from_settings()
        if config is None:
            raise IntegrationError(
                "Shopify integration is not configured "
                "(missing SHOPIFY_STORE_DOMAIN/SHOPIFY_ACCESS_TOKEN).",
                details={"error_type": "not_configured"},
            )
        self._client = ShopifyClient(config)
        return self._client

    async def aclose(self) -> None:
        """Closes and drops the cached client so the next call lazily
        creates a fresh one. `ShopifyAdapter` is a process-lifetime
        singleton (registered once at worker startup), but each Celery
        task runs its own `asyncio.run(...)` — a client's connections
        opened under one task's event loop break once that loop closes,
        so this must run after every task (see `app.db.session.
        dispose_engine_sync`, which has the identical problem for the DB
        engine).

        Drops the reference *before* attempting the close, not after —
        `client.aclose()` closing an already-broken connection can
        itself raise (`app.integrations.registry.aclose_all_adapters`
        catches that). If the drop happened after, a raise would leave
        `self._client` pointed at the same dead client forever, and
        every later call would keep reusing — and re-failing on — it.
        """
        if self._client is None:
            return
        client, self._client = self._client, None
        await client.aclose()

    async def authenticate(self) -> None:
        client = self._get_client()
        try:
            await client.execute(SHOP_PING_QUERY)
        except ShopifyApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

    async def health_check(self) -> HealthCheckResult:
        if not self._configured and self._client is None:
            return HealthCheckResult(
                connected=False,
                error_message=(
                    "Shopify integration is not configured "
                    "(missing SHOPIFY_STORE_DOMAIN/SHOPIFY_ACCESS_TOKEN)."
                ),
            )

        started = time.perf_counter()
        try:
            await self.authenticate()
        except IntegrationError as exc:
            return HealthCheckResult(
                connected=False,
                response_time_ms=(time.perf_counter() - started) * 1000,
                error_message=exc.message,
            )
        return HealthCheckResult(
            connected=True, response_time_ms=(time.perf_counter() - started) * 1000
        )

    async def fetch(
        self, entity_type: str, *, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        return await self._fetch_page(entity_type, cursor=cursor, limit=limit, query_filter=None)

    async def fetch_incremental(
        self, entity_type: str, *, since: datetime, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        query_filter = updated_since_filter(since.isoformat())
        return await self._fetch_page(
            entity_type, cursor=cursor, limit=limit, query_filter=query_filter
        )

    async def _fetch_page(
        self, entity_type: str, *, cursor: str | None, limit: int, query_filter: str | None
    ) -> FetchPage:
        query = ENTITY_QUERIES.get(entity_type)
        if query is None:
            raise IntegrationError(
                f"Shopify adapter does not support entity_type '{entity_type}'.",
                details={"error_type": "validation_error"},
            )

        client = self._get_client()
        try:
            data = await client.execute(
                query, {"first": limit, "after": cursor, "query": query_filter}
            )
        except ShopifyApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

        connection = data.get(entity_type) or {}
        edges = connection.get("edges") or []
        page_info = connection.get("pageInfo") or {}
        has_more = bool(page_info.get("hasNextPage"))

        return FetchPage(
            nodes=[edge["node"] for edge in edges],
            next_cursor=page_info.get("endCursor") if has_more else None,
            has_more=has_more,
        )

    async def process_webhook(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Round 4 fix: a Shopify webhook delivers the REST Admin API
        resource shape (snake_case, flat prices, plain integer ids) —
        never the GraphQL node shape `ENTITY_NORMALIZERS` was built for
        (see `webhook_shapes.py`'s module docstring for how this was
        confirmed: a real orders/create payload crashed
        `ShopifyOrderNormalizer.normalize()` before this translation
        step existed). Translate first, then reuse the exact same,
        unmodified normalizer the pull-sync path uses, so both paths
        converge on identical OMS records for the same order.
        """
        entity_type = event_type.split("/", 1)[0]
        normalizer = ENTITY_NORMALIZERS.get(entity_type)
        if normalizer is None:
            return {"entity_type": None, "normalized": None}
        translate = WEBHOOK_SHAPE_TRANSLATORS.get(entity_type)
        graphql_shaped = translate(payload) if translate else payload
        return {"entity_type": entity_type, "normalized": normalizer.normalize(graphql_shaped)}

    def normalize(self, entity_type: str, raw: dict[str, Any]) -> dict[str, Any]:
        normalizer = ENTITY_NORMALIZERS.get(entity_type)
        if normalizer is None:
            raise IntegrationError(
                f"Shopify adapter does not support entity_type '{entity_type}'.",
                details={"error_type": "validation_error"},
            )
        return normalizer.normalize(raw)
