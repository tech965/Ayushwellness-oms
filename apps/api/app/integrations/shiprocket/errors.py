"""Classifies a Shiprocket REST API failure into the `error_type`
vocabulary `app.integrations.retry` already knows how to schedule
retries for — the same role `app.integrations.shopify.errors` plays for
Shopify, adapted for a plain REST (not GraphQL) error shape.
"""

from __future__ import annotations

import httpx


class ShiprocketApiError(Exception):
    def __init__(self, message: str, *, error_type: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code


def classify_http_error(exc: httpx.HTTPStatusError) -> ShiprocketApiError:
    status = exc.response.status_code
    if status == 401:
        return ShiprocketApiError(
            "Shiprocket rejected the credentials/token (authentication failed).",
            error_type="authentication_error",
            status_code=status,
        )
    if status == 403:
        return ShiprocketApiError(
            "Shiprocket account lacks permission for this operation.",
            error_type="authorization_error",
            status_code=status,
        )
    if status == 422:
        return ShiprocketApiError(
            "Shiprocket rejected the request payload (validation error).",
            error_type="validation_error",
            status_code=status,
        )
    if status == 429:
        return ShiprocketApiError(
            "Shiprocket rate limit exceeded (429).", error_type="http_429", status_code=status
        )
    if 500 <= status < 600:
        return ShiprocketApiError(
            f"Shiprocket API returned a server error ({status}).",
            error_type=f"http_{status}" if status in (500, 502, 503, 504) else "http_500",
            status_code=status,
        )
    return ShiprocketApiError(
        f"Shiprocket API returned an unexpected status ({status}).",
        error_type="permanent_error",
        status_code=status,
    )


def classify_transport_error(exc: httpx.TransportError) -> ShiprocketApiError:
    if isinstance(exc, httpx.TimeoutException):
        return ShiprocketApiError("Timed out contacting Shiprocket.", error_type="timeout")
    return ShiprocketApiError(
        f"Network error contacting Shiprocket: {exc}", error_type="network_error"
    )
