"""Thin NimbusPost REST API HTTP client.

All NimbusPost communication goes through `request()` — nothing else in
`app/integrations/nimbuspost/` (or anywhere in the OMS) makes an HTTP
call to NimbusPost directly. Handles login/token caching, timeouts, and
retry/backoff for transient failures via `app.integrations.retry` —
mirrors `app.integrations.shiprocket.client.ShiprocketClient`.

Login: `POST users/login` with `{"email": ..., "password": ...}`,
confirmed (business + NimbusPost's official SDK source,
github.com/tech-nimbuspost/nimbuspost-api, 2026-11-20) to respond with
`{"status": bool, "message": str, "data": <token>}` — the token is the
`data` field, used thereafter as `Authorization: Bearer {token}`.
NimbusPost does not document a token lifetime anywhere this engagement
has found (unlike Shiprocket's documented 10-day window), so this never
assumes an expiry — the token is cached until a request actually comes
back 401, which forces exactly one re-login + retry.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.nimbuspost.config import NimbusPostConfig
from app.integrations.nimbuspost.errors import (
    NimbusPostApiError,
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


class NimbusPostClient:
    def __init__(
        self,
        config: NimbusPostConfig,
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

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _login(self) -> str:
        # Safe-to-log diagnostics only (base URL + outcome) — never the
        # email, password, or token. Mirrors `ShiprocketClient._login`.
        try:
            response = await self._client.post(
                f"{self._config.api_base_url}users/login",
                json={"email": self._config.email, "password": self._config.password},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = classify_http_error(exc)
            logger.warning(
                "nimbuspost_login_failed",
                api_base_url=self._config.api_base_url,
                error_type=error.error_type,
                status_code=error.status_code,
            )
            raise error from exc
        except httpx.TransportError as exc:
            error = classify_transport_error(exc)
            logger.warning(
                "nimbuspost_login_failed",
                api_base_url=self._config.api_base_url,
                error_type=error.error_type,
            )
            raise error from exc

        body = response.json()
        # NimbusPost's documented shape (confirmed against its official
        # SDK source): a 200 response can still be an application-level
        # failure — `status` gates success, `message` carries the reason,
        # never raised as an HTTP error status in that case.
        if not body.get("status"):
            message = body.get("message") or "NimbusPost login did not report success."
            logger.warning(
                "nimbuspost_login_failed",
                api_base_url=self._config.api_base_url,
                error_type="authentication_error",
                reason="status_false",
            )
            raise NimbusPostApiError(message, error_type="authentication_error")

        token = body.get("data")
        if not token or not isinstance(token, str):
            logger.warning(
                "nimbuspost_login_failed",
                api_base_url=self._config.api_base_url,
                error_type="authentication_error",
                reason="no_token_in_response",
            )
            raise NimbusPostApiError(
                "NimbusPost login response did not include a token in 'data'.",
                error_type="authentication_error",
            )
        self._token = token
        logger.info("nimbuspost_login_succeeded", api_base_url=self._config.api_base_url)
        return token

    async def _ensure_token(self) -> str:
        if self._token is None:
            return await self._login()
        return self._token

    async def ensure_authenticated(self) -> None:
        """Public entrypoint for `NimbusPostAdapter.authenticate()` — logs
        in (or reuses a cached token) without making any other request.
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
        token refresh + retry on a 401. Raises `NimbusPostApiError` —
        never a raw `httpx` exception. `path` is relative to
        `api_base_url` (no leading slash — see module docstring on the
        SDK's inconsistent leading-slash paths; this always joins
        cleanly regardless of whether `path` itself has one).
        """
        attempt = 0
        reauthenticated = False
        while True:
            try:
                token = await self._ensure_token()
                response = await self._client.request(
                    method,
                    f"{self._config.api_base_url}{path.lstrip('/')}",
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
            except NimbusPostApiError as exc:
                error = exc

            attempt += 1
            if attempt > self._retry_policy.max_retries or error.error_type not in (
                _RETRYABLE_ERROR_TYPES
            ):
                raise error

            delay = compute_backoff_seconds(attempt=attempt, policy=self._retry_policy)
            logger.warning(
                "nimbuspost_request_retrying",
                attempt=attempt,
                error_type=error.error_type,
                delay_seconds=delay,
            )
            await asyncio.sleep(delay)
