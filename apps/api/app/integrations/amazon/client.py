"""Thin SP-API HTTP client: LWA auth, per-operation throttling, 429 retry.

Access tokens live in memory only. SP-API no longer requires AWS SigV4
signing, so the LWA token in `x-amz-access-token` is the only credential.
"""

from __future__ import annotations

import asyncio
import gzip
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.integrations.amazon.config import LWA_TOKEN_URL, AmazonConfig
from app.integrations.amazon.errors import (
    AmazonAuthError,
    AmazonError,
    AmazonNotConfiguredError,
    AmazonPermissionError,
    AmazonRateLimitError,
    AmazonReportError,
)

# Documented SP-API limits as (requests/second, burst) -- verified against
# developer-docs.amazon reference pages (createReport/getReportDocument
# 0.0167 rps, getReport 2 rps, getOrderMetrics 0.5 rps, Catalog 2 rps burst 2).
RATES: dict[str, tuple[float, int]] = {
    "createReport": (0.0167, 15),
    "getReport": (2.0, 15),
    "getReportDocument": (0.0167, 15),
    "orderMetrics": (0.5, 15),
    "catalog": (2.0, 2),
}
# Never block an HTTP request longer than this waiting for a token; fail
# with a clear rate-limit error instead.
MAX_WAIT_SECONDS = 15.0
MAX_RETRIES = 3

# Process-wide token buckets: clients are created per request, so limiter
# state must outlive them to limit anything.
_SHARED_BUCKETS: dict[str, tuple[float, float]] = {}


class AmazonClient:
    def __init__(
        self,
        config: AmazonConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rates: dict[str, tuple[float, int]] | None = None,
        buckets: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._sleep = sleeper
        self._rates = RATES if rates is None else rates
        self._buckets = _SHARED_BUCKETS if buckets is None else buckets
        self._token: str | None = None
        self._token_expiry = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _access_token(self, force: bool = False) -> str:
        if not self._config.configured:
            raise AmazonNotConfiguredError(
                "Amazon SP-API credentials are not configured.",
                details={"missing": self._config.missing()},
            )
        if self._token and not force and time.monotonic() < self._token_expiry:
            return self._token
        try:
            resp = await self._http.post(
                LWA_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._config.refresh_token,
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise AmazonError(f"Could not reach Amazon login service: {exc}") from exc
        if resp.status_code != 200:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            raise AmazonAuthError(
                "Amazon rejected the credentials "
                f"({body.get('error', resp.status_code)}): "
                f"{body.get('error_description', 'check client id/secret/refresh token')}"
            )
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + max(int(data.get("expires_in", 3600)) - 60, 30)
        return self._token

    async def _throttle(self, op: str) -> None:
        spec = self._rates.get(op)
        if not spec:
            return
        rate, burst = spec
        now = time.monotonic()
        tokens, last = self._buckets.get(op, (float(burst), now))
        tokens = min(float(burst), tokens + (now - last) * rate)
        if tokens < 1:
            wait = (1 - tokens) / rate
            if wait > MAX_WAIT_SECONDS:
                self._buckets[op] = (tokens, now)
                raise AmazonRateLimitError(
                    f"Amazon allows {rate * 60:.1f} {op} calls/minute and the burst allowance "
                    f"is used up; retry in about {int(wait)} seconds."
                )
            await self._sleep(wait)
            now = time.monotonic()
            tokens = 1.0
        self._buckets[op] = (tokens - 1, now)

    async def request(
        self, op: str, method: str, path: str, *, params: dict | None = None, json: Any = None
    ) -> dict:
        token = await self._access_token()
        refreshed = False
        for attempt in range(MAX_RETRIES + 1):
            await self._throttle(op)
            try:
                resp = await self._http.request(
                    method,
                    self._config.base_url + path,
                    params=params,
                    json=json,
                    headers={"x-amz-access-token": token},
                )
            except httpx.HTTPError as exc:
                raise AmazonError(f"Amazon request failed: {exc}") from exc
            code = resp.status_code
            if code == 429:
                if attempt >= MAX_RETRIES:
                    raise AmazonRateLimitError(
                        "Amazon rate limit hit; please retry in a few minutes."
                    )
                ra = resp.headers.get("Retry-After")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2.0 * 2**attempt
                await self._sleep(delay)
                continue
            if code == 401 and not refreshed:
                token = await self._access_token(force=True)
                refreshed = True
                continue
            if code in (401, 403):
                raise AmazonPermissionError(
                    "Amazon denied access: the app/role is missing a required SP-API "
                    "permission, or the authorization was revoked. " + _msg(resp)
                )
            if code >= 400:
                raise AmazonError(f"Amazon API error {code}: {_msg(resp)}")
            return resp.json()
        raise AmazonRateLimitError("Amazon rate limit hit.")  # pragma: no cover

    async def download_document(self, url: str, compression: str | None) -> bytes:
        """Download a report document into memory (no temp files)."""
        try:
            resp = await self._http.get(url)
        except httpx.HTTPError as exc:
            raise AmazonReportError(f"Could not download report: {exc}") from exc
        if resp.status_code != 200:
            raise AmazonReportError(f"Report download failed ({resp.status_code}).")
        raw = resp.content
        if (compression or "").upper() == "GZIP":
            raw = gzip.decompress(raw)
        return raw


def _msg(resp: httpx.Response) -> str:
    try:
        errs = resp.json().get("errors") or []
        return "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errs)
    except Exception:  # noqa: BLE001
        return resp.text[:200]
