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

# Round 12: `"name"` in the substring denylist above is correct for
# `customer_name`/`first_name`/`last_name`/`billing_name`/etc, but it
# also (wrongly) swallows `channel_name` — which identifies which sales
# channel a shipment came from (e.g. "Shopify"), not a person. Narrow,
# exact-key exceptions only — this must never become a second, broader
# allowlist that could exempt a real PII field by accident.
_KEY_DENYLIST_EXCEPTIONS = frozenset({"channel_name"})


def _is_pii_key(key: str) -> bool:
    if key in _KEY_DENYLIST_EXCEPTIONS:
        return False
    lowered = key.lower()
    return any(bad in lowered for bad in _NEVER_LOG_KEY_SUBSTRINGS)


# Round 11: the first diagnostic (a single `if entity_type == "shipments"
# and nodes: ...` call, gated on this adapter's own node-extraction
# already having succeeded) didn't show up in production logs. Most
# likely a deploy-timing gap (the same class of issue found repeatedly
# this engagement — see the git history), but there's a real second
# possibility worth structurally ruling out at the same time: if the
# top-level key guess this adapter's own `fetch()` uses to find the node
# list (`_FETCH_ROUTES`'s `("data", "shipments")`) is *also* wrong for
# real data, `nodes` would come back empty and that gate would never
# open — meaning this new version fires unconditionally, at the raw
# response boundary, before any of this adapter's own extraction guesses
# run, so it can't be silently skipped by a different wrong guess.
_ORDER_FIELD_HINTS = ("order", "channel")
_SHIPMENT_FIELD_HINTS = ("shipment_id", "awb", "status", "current_status")


def _nested_keys(obj: dict[str, Any], *, max_depth: int = 2, _depth: int = 0) -> dict[str, Any]:
    """Structure only (key names and one-word type markers), never
    values — up to `max_depth` levels of nested dicts.
    """
    if _depth >= max_depth:
        return {}
    result: dict[str, Any] = {}
    for key, value in obj.items():
        if isinstance(value, dict):
            result[key] = _nested_keys(value, max_depth=max_depth, _depth=_depth + 1)
        elif isinstance(value, list):
            result[key] = f"<list, {len(value)} item(s)>"
        else:
            result[key] = "<scalar>"
    return result


def _candidate_fields(
    obj: dict[str, Any], hints: tuple[str, ...], *, prefix: str = "", max_depth: int = 2
) -> dict[str, Any]:
    """Safe (denylist-filtered) values for every key at any depth (up to
    `max_depth`) whose name contains one of `hints` — e.g. `order_id`,
    `channel_order_id`, `shipment_id`, `awb_code`, `current_status`.
    """
    if prefix.count(".") >= max_depth:
        return {}
    found: dict[str, Any] = {}
    for key, value in obj.items():
        lowered = key.lower()
        path = f"{prefix}.{key}" if prefix else key
        if _is_pii_key(key):
            continue
        if any(hint in lowered for hint in hints):
            if isinstance(value, dict):
                found[path] = "<nested object>"
            elif isinstance(value, list):
                found[path] = f"<list, {len(value)} item(s)>"
            else:
                found[path] = value
        if isinstance(value, dict):
            found.update(_candidate_fields(value, hints, prefix=path, max_depth=max_depth))
    return found


def _log_shipments_response_shape(
    *, endpoint: str, response: dict[str, Any], node_keys: tuple[str, ...]
) -> None:
    first_record: dict[str, Any] | None = None
    for key in node_keys:
        value = response.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            first_record = value[0]
            break

    logger.info(
        "shiprocket_shipment_raw_shape",
        endpoint=endpoint,
        response_keys=sorted(response.keys()),
        shipment_keys=sorted(first_record.keys()) if first_record is not None else None,
        nested_keys=_nested_keys(first_record) if first_record is not None else None,
        candidate_order_fields=(
            _candidate_fields(first_record, _ORDER_FIELD_HINTS) if first_record is not None else {}
        ),
        candidate_shipment_fields=(
            _candidate_fields(first_record, _SHIPMENT_FIELD_HINTS)
            if first_record is not None
            else {}
        ),
    )


# Round 12: the broader shape diagnostic above found the real
# `/shipments` keys but the merchant-order-reference candidate never
# surfaced a value — `candidate_order_fields`'s hint list didn't match
# `number`/`code` (neither contains "order" or "channel"), and
# `channel_name`'s value was suppressed by the PII denylist's "name"
# substring match, even though it identifies a sales channel, not a
# person (fixed above via `_KEY_DENYLIST_EXCEPTIONS`). This logs exactly
# those three named fields, explicitly, so the real values are visible
# without expanding the hint-based search further and risking pulling
# in something unintended.
_IDENTITY_FIELD_KEYS = ("number", "code", "channel_name")


def _log_shipment_identity_fields(raw: dict[str, Any]) -> None:
    values = {key: raw.get(key) for key in _IDENTITY_FIELD_KEYS if not _is_pii_key(key)}
    logger.info("shiprocket_shipment_identity_fields", **values)


class ShiprocketAdapter(IntegrationAdapter):
    code = IntegrationCode.SHIPROCKET

    def __init__(self, client: ShiprocketClient | None = None) -> None:
        self._client = client
        self._configured = client is not None or ShiprocketConfig.from_settings() is not None
        # Set the first time `get_order` confirms this Shiprocket account
        # (specifically its API user's granted module permissions) cannot
        # call `GET /orders/show/{id}` at all — a real production incident:
        # every shipment sync record lacking `channel_order_id` (i.e.
        # effectively all of them; `/shipments` never populates it) tried
        # this fallback and got an identical 403, meaning a full backlog
        # (thousands of shipments) hammered a confirmed-permanently-blocked
        # endpoint on every single scheduled run. Once confirmed, every
        # further call in this process's lifetime fails fast with no
        # network round trip — cleared only by a process restart (i.e. the
        # next deploy), which is the right lifetime for "an account-level
        # permission scope, not a per-request fluke."
        self._orders_show_blocked_reason: str | None = None

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

        if entity_type == "shipments":
            # Unconditional -- see `_log_shipments_response_shape`'s
            # docstring for why this must not depend on the node-list
            # key guess below already having succeeded.
            _log_shipments_response_shape(endpoint=path, response=data, node_keys=node_keys)

        nodes: list[dict[str, Any]] = next((data[key] for key in node_keys if data.get(key)), [])
        if entity_type == "shipments" and nodes:
            _log_shipment_identity_fields(nodes[0])
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

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """`GET /orders/show/{order_id}` — the only endpoint confirmed
        live to return `channel_order_id` reliably; `/shipments` never
        populates it (confirmed across every real record inspected this
        engagement). Used by `entity_sync._upsert_shipment` as a fallback
        order-resolution step when a pulled shipment has no matching
        `Shipment` row yet.

        Fails fast (no network call) once a 401/403 has already confirmed
        this account can't call this endpoint at all — see `__init__`.
        """
        if self._orders_show_blocked_reason is not None:
            raise IntegrationError(
                self._orders_show_blocked_reason,
                details={"error_type": "authorization_error", "orders_show_blocked": True},
            )

        client = self._get_client()
        try:
            return await client.request("GET", f"/orders/show/{order_id}")
        except ShiprocketApiError as exc:
            if exc.error_type in ("authorization_error", "authentication_error"):
                self._orders_show_blocked_reason = (
                    f"GET /orders/show is not accessible to this Shiprocket API user "
                    f"({exc.message}) — grant this API user access to the Orders module "
                    "in Shiprocket's dashboard, then redeploy/restart the worker to "
                    "clear this block and retry."
                )
                logger.warning(
                    "shiprocket_orders_show_blocked",
                    error_type=exc.error_type,
                    status_code=exc.status_code,
                )
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
