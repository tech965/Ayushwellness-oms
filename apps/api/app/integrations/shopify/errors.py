"""Classifies a Shopify API failure into the `error_type` vocabulary
`app.integrations.retry` already knows how to schedule retries for, and
into a human-readable, credential-free message safe to persist on
`SyncError`/`Integration.last_failure_message` or return from the
health-check endpoint.
"""

from __future__ import annotations

import httpx


class ShopifyApiError(Exception):
    def __init__(self, message: str, *, error_type: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code


def classify_http_error(exc: httpx.HTTPStatusError) -> ShopifyApiError:
    status = exc.response.status_code
    if status == 401:
        return ShopifyApiError(
            "Shopify rejected the access token (authentication failed).",
            error_type="authentication_error",
            status_code=status,
        )
    if status == 403:
        return ShopifyApiError(
            "Shopify access token is missing a required scope (permission failure).",
            error_type="authorization_error",
            status_code=status,
        )
    if status == 429:
        return ShopifyApiError(
            "Shopify rate limit exceeded (429).", error_type="http_429", status_code=status
        )
    if 500 <= status < 600:
        return ShopifyApiError(
            f"Shopify API returned a server error ({status}).",
            error_type=f"http_{status}" if status in (500, 502, 503, 504) else "http_500",
            status_code=status,
        )
    return ShopifyApiError(
        f"Shopify API returned an unexpected status ({status}).",
        error_type="permanent_error",
        status_code=status,
    )


def classify_transport_error(exc: httpx.TransportError) -> ShopifyApiError:
    if isinstance(exc, httpx.TimeoutException):
        return ShopifyApiError("Timed out contacting Shopify.", error_type="timeout")
    return ShopifyApiError(f"Network error contacting Shopify: {exc}", error_type="network_error")


def classify_graphql_errors(errors: list[dict]) -> ShopifyApiError:
    codes = {(err.get("extensions") or {}).get("code") for err in errors if isinstance(err, dict)}
    messages = "; ".join(str(err.get("message", "")) for err in errors if isinstance(err, dict))

    if "THROTTLED" in codes:
        return ShopifyApiError("Shopify GraphQL query cost throttled.", error_type="http_429")
    if "ACCESS_DENIED" in codes:
        return ShopifyApiError(
            f"Shopify GraphQL access denied: {messages}", error_type="authorization_error"
        )
    if "UNAUTHENTICATED" in codes:
        return ShopifyApiError(
            f"Shopify GraphQL authentication failed: {messages}",
            error_type="authentication_error",
        )
    return ShopifyApiError(
        f"Shopify GraphQL query error: {messages or 'unknown error'}",
        error_type="validation_error",
    )
