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
from app.core.logging import get_logger
from app.integrations.base import FetchPage, HealthCheckResult, IntegrationAdapter
from app.integrations.shiprocket.client import ShiprocketClient
from app.integrations.shiprocket.config import ShiprocketConfig
from app.integrations.shiprocket.errors import ShiprocketApiError
from app.integrations.shiprocket.normalizer import (
    NDR_NORMALIZER,
    ORDER_PUSH_NORMALIZER,
    SHIPMENT_NORMALIZER,
)
from app.models.integration import IntegrationCode

logger = get_logger(__name__)

# Round 10 diagnostic (temporary, until a real /shipments payload has
# been confirmed and this logging is no longer needed): 150/150 real
# shipments failed with "channel_order_id=None" — `ShiprocketShipment
# Normalizer` was written against Shiprocket's commonly *documented*
# `/shipments` shape (never verified live, unlike NDR, which was fixed
# against a real captured payload the same way this diagnostic exists
# to enable now). Denylist-first, not allowlist-first: anything whose
# key name suggests PII is never logged, and only a value survives that
# check *and* looks like a plain id/status/date-shaped scalar (never a
# nested object/list, which could hide an address or name inside).
_NEVER_LOG_KEY_SUBSTRINGS = (
    "phone",
    "mobile",
    "email",
    "address",
    "name",
    "pincode",
    "pin_code",
    "zip",
    "gstin",
    "pan",
    "password",
    "token",
    "secret",
    "auth",
)


def _safe_scalar_values(obj: dict[str, Any]) -> dict[str, Any]:
    """Same key-denylist, applied to one dict level — scalars only pass
    through; a nested dict is recursed into once (one level is enough to
    find a field like `order.channel_order_id` without risking an
    unbounded walk into something like line items or an address block);
    a nested list is reported only as its length, never its contents.
    """
    safe: dict[str, Any] = {}
    for key, value in obj.items():
        lowered = key.lower()
        if any(bad in lowered for bad in _NEVER_LOG_KEY_SUBSTRINGS):
            continue
        if isinstance(value, dict):
            safe[key] = _safe_scalar_values(value)
        elif isinstance(value, list):
            safe[key] = f"<list, {len(value)} item(s)>"
        else:
            safe[key] = value
    return safe


def _log_first_shipment_shape(raw: dict[str, Any]) -> None:
    logger.info(
        "shiprocket_shipment_raw_shape",
        all_top_level_keys=sorted(raw.keys()),
        safe_values=_safe_scalar_values(raw),
    )


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
            # Silent otherwise — every scheduled NDR sync attempt and every
            # push action (ship/assign-awb/tracking) routes through here,
            # so a missing/misconfigured env var on this process previously
            # left no trace in the logs at all.
            logger.warning("shiprocket_not_configured", reason="missing_email_or_password")
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

    # entity_type -> (HTTP path, response-body keys that might hold the
    # node list). Both endpoints share the same `data`/`meta.pagination.
    # total_pages` envelope shape Shiprocket documents for its list
    # endpoints.
    _FETCH_ROUTES: dict[str, tuple[str, tuple[str, ...]]] = {
        "ndr": ("/ndr/all", ("data", "ndr")),
        "shipments": ("/shipments", ("data", "shipments")),
    }

    async def fetch(
        self, entity_type: str, *, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        route = self._FETCH_ROUTES.get(entity_type)
        if route is None:
            raise IntegrationError(
                f"Shiprocket adapter's generic fetch() only supports "
                f"{sorted(self._FETCH_ROUTES)} (got '{entity_type}') — tracking "
                "is OMS-shipment-driven, see app.integrations.shiprocket.sync.",
                details={"error_type": "validation_error"},
            )
        path, node_keys = route
        page_number = int(cursor) if cursor else 1
        client = self._get_client()
        try:
            data = await client.request(
                "GET", path, params={"page": page_number, "per_page": limit}
            )
        except ShiprocketApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

        nodes: list[dict[str, Any]] = next(
            (data[key] for key in node_keys if data.get(key)), []
        )
        if entity_type == "shipments" and nodes:
            _log_first_shipment_shape(nodes[0])
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
        # Neither Shiprocket list endpoint this adapter supports (NDR,
        # shipments) has a documented "changed since" filter — incremental
        # sync degrades to a full pull, same as a full sync. Re-verify
        # against a live account; until then this is intentionally
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
        if entity_type == "shipments":
            return SHIPMENT_NORMALIZER.normalize(raw)
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
