"""Classifies a Cashfree Payments API failure into the `error_type`
vocabulary `app.integrations.retry` already knows how to schedule
retries for — the same role `app.integrations.shiprocket.errors` plays
for Shiprocket.
"""

from __future__ import annotations

import httpx


class CashfreeApiError(Exception):
    def __init__(self, message: str, *, error_type: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code


def classify_http_error(exc: httpx.HTTPStatusError) -> CashfreeApiError:
    status = exc.response.status_code
    # Cashfree returns a JSON error body (`{"code", "message", "type"}` —
    # documented, e.g. `{"message": "order_id already exists", ...}`) on
    # every 4xx; best-effort only, since a truly malformed response body
    # must never crash error classification itself.
    try:
        body = exc.response.json()
        detail = body.get("message") if isinstance(body, dict) else None
    except ValueError:
        detail = None
    suffix = f" ({detail})" if detail else ""

    if status in (401, 403):
        return CashfreeApiError(
            f"Cashfree rejected the credentials{suffix}.",
            error_type="authentication_error",
            status_code=status,
        )
    if status == 404:
        return CashfreeApiError(
            f"Cashfree order/payment not found{suffix}.",
            error_type="not_found",
            status_code=status,
        )
    if status == 409:
        return CashfreeApiError(
            f"Cashfree order_id conflict{suffix}.",
            error_type="conflict",
            status_code=status,
        )
    if status == 422 or status == 400:
        return CashfreeApiError(
            f"Cashfree rejected the request payload{suffix}.",
            error_type="validation_error",
            status_code=status,
        )
    if status == 429:
        return CashfreeApiError(
            "Cashfree rate limit exceeded (429).", error_type="http_429", status_code=status
        )
    if 500 <= status < 600:
        return CashfreeApiError(
            f"Cashfree API returned a server error ({status}).",
            error_type=f"http_{status}" if status in (500, 502, 503, 504) else "http_500",
            status_code=status,
        )
    return CashfreeApiError(
        f"Cashfree API returned an unexpected status ({status}){suffix}.",
        error_type="permanent_error",
        status_code=status,
    )


def classify_transport_error(exc: httpx.TransportError) -> CashfreeApiError:
    if isinstance(exc, httpx.TimeoutException):
        return CashfreeApiError("Timed out contacting Cashfree.", error_type="timeout")
    return CashfreeApiError(f"Network error contacting Cashfree: {exc}", error_type="network_error")
