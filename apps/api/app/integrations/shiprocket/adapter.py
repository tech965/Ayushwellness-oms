"""ShiprocketAdapter — the only Shiprocket-specific implementation of
`app.integrations.base.IntegrationAdapter`, plus concrete push/pull
capabilities beyond that interface (order creation, AWB assignment,
cancellation, pickup, single-AWB tracking) that the interface never
anticipated, since Phase 2.1's `IntegrationAdapter` was modeled on a
pull-only provider (Shopify). No generic sync/retry/idempotency logic
lives here — that's `SyncService`/`WebhookService`, reused unchanged.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.core.exceptions import IntegrationError
from app.integrations.base import FetchPage, HealthCheckResult, IntegrationAdapter
from app.integrations.shiprocket.client import ShiprocketClient
from app.integrations.shiprocket.config import ShiprocketConfig
from app.integrations.shiprocket.errors import ShiprocketApiError
from app.integrations.shiprocket.normalizer import NDR_NORMALIZER, ORDER_PUSH_NORMALIZER
from app.models.integration import IntegrationCode


class ShiprocketAdapter(IntegrationAdapter):
    code = IntegrationCode.SHIPROCKET

    def __init__(self, client: ShiprocketClient | None = None) -> None:
        self._client = client
        self._configured = client is not None or ShiprocketConfig.from_settings() is not None

    def _get_client(self) -> ShiprocketClient:
        if self._client is not None:
            return self._client
        config = ShiprocketConfig.from_settings()
        if config is None:
            raise IntegrationError(
                "Shiprocket integration is not configured "
                "(missing SHIPROCKET_EMAIL/SHIPROCKET_PASSWORD).",
                details={"error_type": "not_configured"},
            )
        self._client = ShiprocketClient(config)
        return self._client

    async def aclose(self) -> None:
        """Closes and drops the cached client so the next call lazily
        creates a fresh one — see `ShopifyAdapter.aclose` for why this is
        needed (identical process-lifetime-singleton-vs-per-task-event-
        loop issue) and why the reference must drop before the close is
        attempted, not after.
        """
        if self._client is None:
            return
        client, self._client = self._client, None
        await client.aclose()

    # --- IntegrationAdapter interface --------------------------------

    async def authenticate(self) -> None:
        client = self._get_client()
        try:
            await client.ensure_authenticated()
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

    async def health_check(self) -> HealthCheckResult:
        if not self._configured and self._client is None:
            return HealthCheckResult(
                connected=False,
                error_message=(
                    "Shiprocket integration is not configured "
                    "(missing SHIPROCKET_EMAIL/SHIPROCKET_PASSWORD)."
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
        if entity_type != "ndr":
            raise IntegrationError(
                f"Shiprocket adapter's generic fetch() only supports 'ndr' "
                f"(got '{entity_type}') — tracking is OMS-shipment-driven, "
                "see app.integrations.shiprocket.sync.",
                details={"error_type": "validation_error"},
            )
        page_number = int(cursor) if cursor else 1
        client = self._get_client()
        try:
            data = await client.request(
                "GET", "/ndr/all", params={"page": page_number, "per_page": limit}
            )
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

        nodes = data.get("data") or data.get("ndr") or []
        meta = (
            data.get("meta", {}).get("pagination", {}) if isinstance(data.get("meta"), dict) else {}
        )
        total_pages = meta.get("total_pages") or (
            1 if not nodes else page_number + 1 if len(nodes) >= limit else page_number
        )
        has_more = page_number < total_pages and bool(nodes)

        return FetchPage(
            nodes=nodes, next_cursor=str(page_number + 1) if has_more else None, has_more=has_more
        )

    async def fetch_incremental(
        self, entity_type: str, *, since: datetime, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        # Shiprocket's NDR listing has no documented "changed since" filter
        # — incremental sync degrades to a full pull, same as a full sync.
        # Re-verify against a live account; until then this is intentionally
        # conservative rather than silently missing updates.
        return await self.fetch(entity_type, cursor=cursor, limit=limit)

    async def process_webhook(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        # No webhook contract implemented — see docs/integrations/shiprocket.md.
        # Present only to satisfy the interface; app/api/v1/webhooks/shiprocket.py
        # remains an empty router and never calls this.
        return {"entity_type": None, "normalized": None}

    def normalize(self, entity_type: str, raw: dict[str, Any]) -> dict[str, Any]:
        if entity_type == "ndr":
            return NDR_NORMALIZER.normalize(raw)
        raise IntegrationError(
            f"Shiprocket adapter does not support entity_type '{entity_type}'.",
            details={"error_type": "validation_error"},
        )

    # --- Concrete push/pull capabilities beyond the interface --------

    async def create_order(
        self, order: Any, *, pickup_location: str, **dimension_overrides: Any
    ) -> dict[str, Any]:
        payload = ORDER_PUSH_NORMALIZER.build_payload(
            order, pickup_location=pickup_location, **dimension_overrides
        )
        client = self._get_client()
        try:
            return await client.request("POST", "/orders/create/adhoc", json=payload)
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

    async def assign_awb(
        self, shiprocket_shipment_id: str, *, courier_id: str | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"shipment_id": shiprocket_shipment_id}
        if courier_id:
            payload["courier_id"] = courier_id
        client = self._get_client()
        try:
            return await client.request("POST", "/courier/assign/awb", json=payload)
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

    async def cancel_shipment(self, shiprocket_shipment_ids: list[str]) -> dict[str, Any]:
        client = self._get_client()
        try:
            return await client.request(
                "POST", "/orders/cancel/shipment/awbs", json={"awbs": shiprocket_shipment_ids}
            )
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

    async def request_pickup(self, shiprocket_shipment_id: str) -> dict[str, Any]:
        client = self._get_client()
        try:
            return await client.request(
                "POST", "/courier/generate/pickup", json={"shipment_id": [shiprocket_shipment_id]}
            )
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

    async def get_tracking(self, awb: str) -> dict[str, Any]:
        client = self._get_client()
        try:
            return await client.request("GET", f"/courier/track/awb/{awb}")
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

    async def ndr_reattempt(
        self, *, awb: str, address_1: str, address_2: str | None, phone: str
    ) -> dict[str, Any]:
        client = self._get_client()
        try:
            return await client.request(
                "POST",
                "/ndr/reattempt",
                json={
                    "awb": awb,
                    "address_1": address_1,
                    "address_2": address_2 or "",
                    "phone": phone,
                },
            )
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc
