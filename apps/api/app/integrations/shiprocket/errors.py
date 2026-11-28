"""Classifies a Shiprocket REST API failure into the `error_type`
vocabulary `app.integrations.retry` already knows how to schedule
retries for — the same role `app.integrations.shopify.errors` plays for
Shopify, adapted for a plain REST (not GraphQL) error shape.
"""

from __future__ import annotations

from typing import Any

import httpx


class ShiprocketApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        self.details = details or {}


def _safe_shape(value: object, depth: int = 0) -> object:
    """Key names and value TYPES only -- never values. Used to report a
    Shiprocket error response body (e.g. for diagnosing a 500) without
    ever logging/returning a token, credential, or customer PII, even by
    accident. Mirrors `app.api.v1.endpoints.revenue_reconciliation._shape`
    (same contract, intentionally duplicated so this low-level error
    module has no dependency on an API endpoint module).
    """
    if isinstance(value, dict):
        if depth >= 3:
            return "object"
        return {k: _safe_shape(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, list):
        return [f"list[{len(value)}]", _safe_shape(value[0], depth + 1)] if value else "list[0]"
    return type(value).__name__ if value is not None else "null"


def _safe_response_details(response: httpx.Response) -> dict[str, Any]:
    """Best-effort, always-safe capture of a Shiprocket error response so
    an HTTP 500 (or any other failure) can be diagnosed from logs alone --
    never raises, never includes raw body text or header values."""
    try:
        body = response.json()
    except ValueError:
        text = response.text
        return {"response_body_length": len(text), "response_body_type": "non_json"}
    return {"response_shape": _safe_shape(body)}


def classify_http_error(exc: httpx.HTTPStatusError) -> ShiprocketApiError:
    status = exc.response.status_code
    details = _safe_response_details(exc.response)
    if status == 401:
        return ShiprocketApiError(
            "Shiprocket rejected the credentials/token (authentication failed).",
            error_type="authentication_error",
            status_code=status,
            details=details,
        )
    if status == 403:
        return ShiprocketApiError(
            "Shiprocket account lacks permission for this operation.",
            error_type="authorization_error",
            status_code=status,
            details=details,
        )
    if status == 422:
        return ShiprocketApiError(
            "Shiprocket rejected the request payload (validation error).",
            error_type="validation_error",
            status_code=status,
            details=details,
        )
    if status == 429:
        return ShiprocketApiError(
            "Shiprocket rate limit exceeded (429).",
            error_type="http_429",
            status_code=status,
            details=details,
        )
    if 500 <= status < 600:
        return ShiprocketApiError(
            f"Shiprocket API returned a server error ({status}).",
            error_type=f"http_{status}" if status in (500, 502, 503, 504) else "http_500",
            status_code=status,
            details=details,
        )
    return ShiprocketApiError(
        f"Shiprocket API returned an unexpected status ({status}).",
        error_type="permanent_error",
        status_code=status,
        details=details,
    )


def classify_transport_error(exc: httpx.TransportError) -> ShiprocketApiError:
    if isinstance(exc, httpx.TimeoutException):
        return ShiprocketApiError("Timed out contacting Shiprocket.", error_type="timeout")
    return ShiprocketApiError(
        f"Network error contacting Shiprocket: {exc}", error_type="network_error"
    )
