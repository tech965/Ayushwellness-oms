"""Thin Cashfree Payments API HTTP client (API version 2025-01-01).

All Cashfree communication goes through `request()` — nothing else in
`app/integrations/cashfree/` (or anywhere in the OMS) calls Cashfree
directly. Unlike Shiprocket, Cashfree's Payments API has no login/token
step: every request carries `x-client-id`/`x-client-secret`/
`x-api-version` headers directly (confirmed via Cashfree's official
Node SDK docs — see docs/integrations/cashfree.md), so this client is
simpler than `ShiprocketClient` — no token cache, just retry/backoff for
transient failures via `app.integrations.retry`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.cashfree.config import CashfreeConfig
from app.integrations.cashfree.errors import (
    CashfreeApiError,
    classify_http_error,
    classify_transport_error,
)
from app.integrations.retry import DEFAULT_RETRY_POLICY, RetryPolicy, compute_backoff_seconds

logger = get_logger(__name__)

_RETRYABLE_ERROR_TYPES = {
    "timeout",
    "network_error",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
}


class CashfreeClient:
    def __init__(
        self,
        config: CashfreeConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._config = config
        self._retry_policy = retry_policy
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        # Never logged — see `CashfreeConfig`/`Settings` docstrings.
        return {
            "x-client-id": self._config.client_id,
            "x-client-secret": self._config.client_secret,
            "x-api-version": self._config.api_version,
            "content-type": "application/json",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Runs one REST request against `{base_url}{path}`, retrying
        transient failures (timeout, network error, 429, 5xx) with
        exponential backoff. Raises `CashfreeApiError` — never a raw
        `httpx` exception. Returns whatever Cashfree's response body
        decodes to — a JSON object for most endpoints, a JSON array for
        `GET .../payments` (see `get_payments_for_order`).
        """
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method,
                    f"{self._config.base_url}{path}",
                    json=json,
                    params=params,
                    headers=self._headers(),
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                error = classify_http_error(exc)
            except httpx.TransportError as exc:
                error = classify_transport_error(exc)

            attempt += 1
            if attempt > self._retry_policy.max_retries or error.error_type not in (
                _RETRYABLE_ERROR_TYPES
            ):
                raise error

            delay = compute_backoff_seconds(attempt=attempt, policy=self._retry_policy)
            logger.warning(
                "cashfree_request_retrying",
                attempt=attempt,
                error_type=error.error_type,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)

    # --- Concrete Cashfree Payments API operations ------------------

    async def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """`POST /orders` — `payload` is exactly the body
        `app.integrations.cashfree.normalizer.build_create_order_payload`
        builds (order_id, order_amount, order_currency, customer_details,
        order_meta, ...). Returns Cashfree's order object, including
        `cf_order_id`/`payment_session_id`/`order_status`.
        """
        try:
            return await self.request("POST", "/orders", json=payload)
        except CashfreeApiError as exc:
            logger.warning(
                "cashfree_create_order_failed", error_type=exc.error_type, status=exc.status_code
            )
            raise

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """`GET /orders/{order_id}` — current order state
        (`order_status`: ACTIVE/PAID/EXPIRED/TERMINATED), used for
        reconciliation."""
        return await self.request("GET", f"/orders/{order_id}")

    async def get_payments_for_order(self, order_id: str) -> list[dict[str, Any]]:
        """`GET /orders/{order_id}/payments` — every payment attempt
        against this order (array; a customer can retry with a different
        instrument after a failed attempt, each getting its own
        `cf_payment_id`)."""
        result = await self.request("GET", f"/orders/{order_id}/payments")
        return result if isinstance(result, list) else []

    async def get_payment(self, order_id: str, cf_payment_id: str) -> dict[str, Any]:
        """`GET /orders/{order_id}/payments/{cf_payment_id}` — one
        specific payment attempt by its Cashfree id."""
        return await self.request("GET", f"/orders/{order_id}/payments/{cf_payment_id}")
