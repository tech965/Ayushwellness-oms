"""Thin Flipkart Seller API HTTP client: OAuth2 client_credentials auth,
429 retry. No official per-operation rate limits are documented
(https://seller.flipkart.com/api-docs/order-api-docs/OMAPIRef.html lists
only per-request ID-count caps, e.g. "Maximum 100 shipments per
request" -- never a requests/second number), so unlike the Amazon
client this one does not run a token-bucket limiter against invented
numbers -- it only backs off on an actual 429 response, honoring
Retry-After when Flipkart sends one.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.integrations.flipkart.config import BASE_URL, OAUTH_SCOPE, TOKEN_URL, FlipkartConfig
from app.integrations.flipkart.errors import (
    FlipkartAuthError,
    FlipkartError,
    FlipkartNotConfiguredError,
    FlipkartPermissionError,
    FlipkartRateLimitError,
)

MAX_RETRIES = 3
# Flipkart's docs don't state a retry-after cap; keep this bounded like
# the Amazon client so a single request can never hang indefinitely.
MAX_BACKOFF_SECONDS = 15.0


def _msg(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    return str(body)[:300]


class FlipkartClient:
    def __init__(
        self,
        config: FlipkartConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._sleep = sleeper
        self._token: str | None = None
        self._token_expiry = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _access_token(self, force: bool = False) -> str:
        if not force and self._token and time.monotonic() < self._token_expiry:
            return self._token
        if not self._config.configured:
            raise FlipkartNotConfiguredError(
                "Flipkart Seller API credentials are not configured.",
                details={"missing": self._config.missing()},
            )
        try:
            resp = await self._http.get(
                TOKEN_URL,
                params={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
                auth=(self._config.app_id or "", self._config.app_secret or ""),
            )
        except httpx.HTTPError as exc:
            raise FlipkartAuthError(f"Flipkart token request failed: {exc}") from exc
        if resp.status_code != 200:
            raise FlipkartAuthError(
                f"Flipkart rejected the credentials ({resp.status_code}): {_msg(resp)}"
            )
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise FlipkartAuthError(f"Flipkart token response had no access_token: {_msg(resp)}")
        expires_in = float(body.get("expires_in", 0) or 0)
        self._token = token
        # 60s safety margin, same pattern as the Amazon client.
        self._token_expiry = time.monotonic() + max(expires_in - 60, 0)
        return token

    async def request(
        self, method: str, path: str, *, params: dict | None = None, json: Any = None
    ) -> dict:
        # Flipkart's own `nextPageUrl` (from a prior response) is an
        # absolute URL, not a path -- pass it straight through rather
        # than mangling it by prepending BASE_URL again.
        url = path if path.startswith("http://") or path.startswith("https://") else BASE_URL + path
        token = await self._access_token()
        refreshed = False
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise FlipkartError(f"Flipkart request failed: {exc}") from exc
            code = resp.status_code
            if code == 429:
                if attempt >= MAX_RETRIES:
                    raise FlipkartRateLimitError("Flipkart rate limit hit; please retry shortly.")
                ra = resp.headers.get("Retry-After")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2.0 * 2**attempt
                await self._sleep(min(delay, MAX_BACKOFF_SECONDS))
                continue
            if code == 401 and not refreshed:
                token = await self._access_token(force=True)
                refreshed = True
                continue
            if code in (401, 403):
                raise FlipkartPermissionError(
                    "Flipkart denied access: the app is missing a required permission, or "
                    "the authorization was revoked. " + _msg(resp)
                )
            if code >= 400:
                raise FlipkartError(f"Flipkart API error {code}: {_msg(resp)}")
            return resp.json()
        raise FlipkartRateLimitError("Flipkart rate limit hit.")  # pragma: no cover
