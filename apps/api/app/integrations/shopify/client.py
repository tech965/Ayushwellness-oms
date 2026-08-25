"""Thin Shopify GraphQL Admin API HTTP client.

All Shopify communication goes through `execute()` — nothing else in
`app/integrations/shopify/` (or anywhere in the OMS) makes an HTTP call
to Shopify directly. Handles auth headers, timeouts, and retry/backoff
for transient failures via `app.integrations.retry`; does not know
anything about OMS models.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.retry import DEFAULT_RETRY_POLICY, RetryPolicy, compute_backoff_seconds
from app.integrations.shopify.config import ShopifyConfig
from app.integrations.shopify.errors import (
    ShopifyApiError,
    classify_graphql_errors,
    classify_http_error,
    classify_transport_error,
)

logger = get_logger(__name__)


class ShopifyClient:
    def __init__(
        self,
        config: ShopifyConfig,
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

    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Runs one GraphQL request, retrying transient failures (timeout,
        network error, 429, 5xx, or a GraphQL-level THROTTLED cost error)
        with exponential backoff. Raises `ShopifyApiError` — never a raw
        `httpx` exception — so callers only need to handle one error type.
        """
        attempt = 0
        while True:
            try:
                response = await self._client.post(
                    self._config.graphql_url,
                    json={"query": query, "variables": variables or {}},
                    headers={
                        "X-Shopify-Access-Token": self._config.access_token,
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                body = response.json()

                if body.get("errors"):
                    raise classify_graphql_errors(body["errors"])

                return body["data"]

            except httpx.HTTPStatusError as exc:
                error = classify_http_error(exc)
            except httpx.TransportError as exc:
                error = classify_transport_error(exc)
            except ShopifyApiError as exc:
                error = exc

            attempt += 1
            if attempt > self._retry_policy.max_retries or error.error_type not in {
                "timeout",
                "network_error",
                "http_429",
                "http_500",
                "http_502",
                "http_503",
                "http_504",
            }:
                raise error

            delay = compute_backoff_seconds(attempt=attempt, policy=self._retry_policy)
            logger.warning(
                "shopify_request_retrying",
                attempt=attempt,
                error_type=error.error_type,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)
