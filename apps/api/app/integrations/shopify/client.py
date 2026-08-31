"""Thin Shopify GraphQL Admin API HTTP client.

All Shopify communication goes through `execute()` — nothing else in
`app/integrations/shopify/` (or anywhere in the OMS) makes an HTTP call
to Shopify directly. Handles auth headers, timeouts, and retry/backoff
for transient failures via `app.integrations.retry`; does not know
anything about OMS models.

Under the Client Credentials Grant auth mode (see `ShopifyConfig`),
`execute()` resolves a real access token from `ShopifyTokenManager`
before every request (a cheap in-memory cache hit once fetched, not a
network call per request) instead of reading a static token off config.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.retry import DEFAULT_RETRY_POLICY, RetryPolicy, compute_backoff_seconds
from app.integrations.shopify.auth import ShopifyTokenManager
from app.integrations.shopify.config import ShopifyConfig
from app.integrations.shopify.errors import (
    ShopifyApiError,
    classify_graphql_errors,
    classify_http_error,
    classify_transport_error,
)

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


class ShopifyClient:
    def __init__(
        self,
        config: ShopifyConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        timeout_seconds: float = 30.0,
        token_manager: ShopifyTokenManager | None = None,
    ) -> None:
        self._config = config
        self._retry_policy = retry_policy
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = http_client is None
        # `token_manager` is accepted directly for tests; production
        # construction always takes the branch below, reusing this same
        # `httpx.AsyncClient` (one connection pool per adapter instance,
        # not a second one just for token requests).
        self._token_manager = token_manager
        if self._token_manager is None and config.uses_client_credentials:
            assert config.client_id is not None  # guaranteed by uses_client_credentials
            assert config.client_secret is not None
            self._token_manager = ShopifyTokenManager(
                shop_domain=config.shop_domain,
                client_id=config.client_id,
                client_secret=config.client_secret,
                http_client=self._client,
                retry_policy=retry_policy,
            )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _resolve_access_token(self) -> str:
        if self._token_manager is not None:
            return await self._token_manager.get_access_token()
        # Static-token mode — guaranteed non-None here by
        # `ShopifyConfig.from_settings()` (a config is only ever
        # constructed with an access_token OR client_id/client_secret).
        assert self._config.access_token is not None
        return self._config.access_token

    async def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Runs one GraphQL request, retrying transient failures (timeout,
        network error, 429, 5xx, or a GraphQL-level THROTTLED cost error)
        with exponential backoff. Raises `ShopifyApiError` — never a raw
        `httpx` exception — so callers only need to handle one error type.

        Under Client Credentials Grant, a GraphQL 401 (the cached token
        expired sooner than promised, or was revoked) invalidates the
        cached token and retries the SAME request exactly once with a
        freshly fetched one — never more than once, so a genuine
        client_id/client_secret problem still fails fast rather than
        looping. A static `SHOPIFY_ACCESS_TOKEN` has nothing to refresh
        (`_token_manager is None`), so that failure mode is unchanged
        from before this auth mode existed.
        """
        attempt = 0
        reauthenticated = False
        while True:
            access_token = await self._resolve_access_token()
            try:
                response = await self._client.post(
                    self._config.graphql_url,
                    json={"query": query, "variables": variables or {}},
                    headers={
                        "X-Shopify-Access-Token": access_token,
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

            if (
                error.error_type == "authentication_error"
                and self._token_manager is not None
                and not reauthenticated
            ):
                reauthenticated = True
                self._token_manager.invalidate_token()
                logger.warning(
                    "shopify_token_rejected_reauthenticating",
                    shop_domain=self._config.shop_domain,
                )
                continue

            attempt += 1
            if attempt > self._retry_policy.max_retries or error.error_type not in (
                _RETRYABLE_ERROR_TYPES
            ):
                raise error

            delay = compute_backoff_seconds(attempt=attempt, policy=self._retry_policy)
            logger.warning(
                "shopify_request_retrying",
                attempt=attempt,
                error_type=error.error_type,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)
