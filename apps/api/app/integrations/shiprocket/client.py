"""Thin Shiprocket REST API HTTP client.

All Shiprocket communication goes through `request()` — nothing else in
`app/integrations/shiprocket/` (or anywhere in the OMS) makes an HTTP
call to Shiprocket directly. Handles login/token caching (a 10-day
bearer token per Shiprocket's documented behavior — refreshed slightly
early, and forced on any 401), timeouts, and retry/backoff for transient
failures via `app.integrations.retry`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.retry import DEFAULT_RETRY_POLICY, RetryPolicy, compute_backoff_seconds
from app.integrations.shiprocket.config import ShiprocketConfig
from app.integrations.shiprocket.errors import (
    ShiprocketApiError,
    classify_http_error,
    classify_transport_error,
)

logger = get_logger(__name__)

# Shiprocket documents a 240-hour (10-day) token lifetime; refreshing an
# hour early avoids a request failing right at the boundary.
_TOKEN_LIFETIME = timedelta(hours=239)

_RETRYABLE_ERROR_TYPES = {
    "timeout",
    "network_error",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
}


class ShiprocketClient:
    def __init__(
        self,
        config: ShiprocketConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._config = config
        self._retry_policy = retry_policy
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = http_client is None
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _login(self) -> str:
        # Safe-to-log diagnostics only (base URL + outcome) — never the
        # email, password, or token. This is the single choke point every
        # Shiprocket operation (scheduled NDR sync, Test Connection,
        # ship/assign-awb/tracking) goes through, so one log line here
        # answers "did auth even succeed?" from Render logs alone, without
        # needing DB or shell access to the deployment.
        try:
            response = await self._client.post(
                f"{self._config.api_base_url}/auth/login",
                json={"email": self._config.email, "password": self._config.password},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = classify_http_error(exc)
            logger.warning(
                "shiprocket_login_failed",
                api_base_url=self._config.api_base_url,
                error_type=error.error_type,
                status_code=error.status_code,
            )
            raise error from exc
        except httpx.TransportError as exc:
            error = classify_transport_error(exc)
            logger.warning(
                "shiprocket_login_failed",
                api_base_url=self._config.api_base_url,
                error_type=error.error_type,
            )
            raise error from exc

        body = response.json()
        token = body.get("token")
        if not token:
            logger.warning(
                "shiprocket_login_failed",
                api_base_url=self._config.api_base_url,
                error_type="authentication_error",
                reason="no_token_in_response",
            )
            raise ShiprocketApiError(
                "Shiprocket login response did not include a token.",
                error_type="authentication_error",
            )
        self._token = token
        self._token_expires_at = datetime.now(UTC) + _TOKEN_LIFETIME
        logger.info("shiprocket_login_succeeded", api_base_url=self._config.api_base_url)
        return token

    async def _ensure_token(self) -> str:
        if self._token is None or (
            self._token_expires_at is not None and datetime.now(UTC) >= self._token_expires_at
        ):
            return await self._login()
        return self._token

    async def ensure_authenticated(self) -> None:
        """Public entrypoint for `ShiprocketAdapter.authenticate()` — logs
        in (or reuses a cached, unexpired token) without making any other
        request.
        """
        await self._ensure_token()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Runs one REST request, retrying transient failures (timeout,
        network error, 429, 5xx) with exponential backoff, and forcing one
        token refresh + retry on a 401 (a cached token can go stale before
        our tracked expiry, e.g. if revoked). Raises `ShiprocketApiError`
        — never a raw `httpx` exception.
        """
        attempt = 0
        reauthenticated = False
        while True:
            try:
                token = await self._ensure_token()
                response = await self._client.request(
                    method,
                    f"{self._config.api_base_url}{path}",
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                error = classify_http_error(exc)
                if error.status_code == 401 and not reauthenticated:
                    reauthenticated = True
                    self._token = None
                    continue
            except httpx.TransportError as exc:
                error = classify_transport_error(exc)
            except ShiprocketApiError as exc:
                error = exc

            attempt += 1
            if attempt > self._retry_policy.max_retries or error.error_type not in (
                _RETRYABLE_ERROR_TYPES
            ):
                raise error

            delay = compute_backoff_seconds(attempt=attempt, policy=self._retry_policy)
            logger.warning(
                "shiprocket_request_retrying",
                attempt=attempt,
                error_type=error.error_type,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)
