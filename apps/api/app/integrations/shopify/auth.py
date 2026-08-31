"""Shopify Client Credentials Grant token management.

Shopify's current Dev Dashboard app model authenticates via
`POST /admin/oauth/access_token` with `grant_type=client_credentials` —
a direct, server-to-server token exchange with no redirect/callback step
at all (unlike the classic 3-legged authorization-code OAuth grant). The
resulting access token expires in ~24h (`expires_in`, read from Shopify's
response — never hardcoded) and has no refresh token; the only way to
renew it is to repeat the same client-credentials request.

`ShopifyTokenManager` caches the token in memory, refreshing it shortly
before expiry, and is per-`ShopifyClient`-instance — `ShopifyAdapter`
instances are process-lifetime singletons (one per worker process), so
this is effectively one cache per worker process. That's an accepted
tradeoff for now, not a bug: a Redis/DB-backed shared cache would add
real complexity (invalidation across processes, a new failure mode) for
a token that's cheap to refresh and already safe to fetch redundantly
across processes.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.core.logging import get_logger
from app.integrations.retry import DEFAULT_RETRY_POLICY, RetryPolicy, compute_backoff_seconds
from app.integrations.shopify.errors import (
    ShopifyApiError,
    classify_http_error,
    classify_transport_error,
)

logger = get_logger(__name__)

# Shopify doesn't document an exact minimum safe margin; refreshing a few
# minutes before the token's own `expires_in` deadline avoids a request
# racing the exact expiry instant.
TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS = 300

_RETRYABLE_ERROR_TYPES = {
    "timeout",
    "network_error",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
}


def _classify_token_endpoint_error(exc: httpx.HTTPStatusError) -> ShopifyApiError:
    """Same `ShopifyApiError`/`error_type` vocabulary `classify_http_error`
    uses, but with wording accurate to the token endpoint — reusing that
    function's 401 message ("Shopify rejected the access token") here
    would be misleading: a 401 from `/admin/oauth/access_token` means the
    client_id/client_secret pair itself is wrong, not an access token.
    """
    status = exc.response.status_code
    if status == 400:
        return ShopifyApiError(
            "Shopify rejected the client-credentials request "
            "(malformed request, or invalid SHOPIFY_CLIENT_ID format).",
            error_type="validation_error",
            status_code=status,
        )
    if status == 401:
        return ShopifyApiError(
            "Shopify rejected SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET "
            "(invalid client credentials).",
            error_type="authentication_error",
            status_code=status,
        )
    if status == 403:
        return ShopifyApiError(
            "Shopify refused to issue a token for this app on this store "
            "(app not installed on SHOPIFY_STORE_DOMAIN, or client "
            "credentials grant not permitted for this app/store).",
            error_type="authorization_error",
            status_code=status,
        )
    return classify_http_error(exc)


class ShopifyTokenManager:
    def __init__(
        self,
        *,
        shop_domain: str,
        client_id: str,
        client_secret: str,
        http_client: httpx.AsyncClient,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        self._shop_domain = shop_domain
        self._client_id = client_id
        self._client_secret = client_secret
        # Reuses the caller's `httpx.AsyncClient` (the same one GraphQL
        # requests go through) rather than opening a second connection
        # pool — one client per adapter instance, per the existing
        # `ShopifyClient` design.
        self._client = http_client
        self._retry_policy = retry_policy
        self._token: str | None = None
        self._expires_at: float = 0.0
        # Collapses concurrent callers into a single in-flight token
        # request instead of a "stampede" (N concurrent GraphQL calls
        # all finding no cached token and each firing their own token
        # request). Only the first waiter after the lock actually
        # refreshes; every other waiter's re-check below finds the
        # freshly cached token and returns immediately.
        self._lock = asyncio.Lock()

    def invalidate_token(self) -> None:
        """Called after Shopify rejects the cached token mid-flight (a
        GraphQL 401) — forces the next `get_access_token()` call to fetch
        a fresh one instead of reusing the one just rejected.
        """
        self._token = None
        self._expires_at = 0.0

    async def get_access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token
        async with self._lock:
            if self._token is not None and time.monotonic() < self._expires_at:
                return self._token
            return await self.refresh_access_token()

    async def refresh_access_token(self) -> str:
        token, expires_in, scope = await self._request_token()
        self._token = token
        self._expires_at = time.monotonic() + max(
            expires_in - TOKEN_EXPIRY_SAFETY_MARGIN_SECONDS, 0
        )
        logger.info(
            "shopify_token_refreshed",
            shop_domain=self._shop_domain,
            token_refresh=True,
            expires_in=expires_in,
            scope_count=len(scope.split(",")) if scope else 0,
        )
        return token

    async def _request_token(self) -> tuple[str, int, str]:
        url = f"https://{self._shop_domain}/admin/oauth/access_token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }

        attempt = 0
        while True:
            try:
                response = await self._client.post(url, data=data)
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPStatusError as exc:
                error = _classify_token_endpoint_error(exc)
            except httpx.TransportError as exc:
                error = classify_transport_error(exc)
            except ValueError as exc:
                # Malformed JSON body — not a retryable condition.
                raise ShopifyApiError(
                    "Shopify token endpoint returned a response that "
                    "isn't valid JSON.",
                    error_type="validation_error",
                ) from exc
            else:
                access_token = body.get("access_token")
                expires_in = body.get("expires_in")
                if not access_token or not isinstance(expires_in, int):
                    raise ShopifyApiError(
                        "Shopify token endpoint response is missing "
                        "'access_token' or a valid 'expires_in'.",
                        error_type="validation_error",
                    )
                return access_token, expires_in, body.get("scope", "")

            attempt += 1
            if attempt > self._retry_policy.max_retries or error.error_type not in (
                _RETRYABLE_ERROR_TYPES
            ):
                raise error

            delay = compute_backoff_seconds(attempt=attempt, policy=self._retry_policy)
            logger.warning(
                "shopify_token_request_retrying",
                attempt=attempt,
                error_type=error.error_type,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)
