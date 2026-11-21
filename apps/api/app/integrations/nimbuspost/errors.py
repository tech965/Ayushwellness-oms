"""Classifies a NimbusPost REST API failure into the `error_type`
vocabulary `app.integrations.retry` already knows how to schedule
retries for — adapted from `app.integrations.shiprocket.errors` for
NimbusPost's REST shape (confirmed: a 200 response can still carry a
`status: false` application-level failure, distinct from an HTTP error
status -- see `NimbusPostClient.request`).
"""

from __future__ import annotations

import httpx


class NimbusPostApiError(Exception):
    def __init__(self, message: str, *, error_type: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.status_code = status_code


def classify_http_error(exc: httpx.HTTPStatusError) -> NimbusPostApiError:
    status = exc.response.status_code
    if status == 401:
        return NimbusPostApiError(
            "NimbusPost rejected the credentials/token (authentication failed).",
            error_type="authentication_error",
            status_code=status,
        )
    if status == 403:
        return NimbusPostApiError(
            "NimbusPost account lacks permission for this operation.",
            error_type="authorization_error",
            status_code=status,
        )
    if status == 422:
        return NimbusPostApiError(
            "NimbusPost rejected the request payload (validation error).",
            error_type="validation_error",
            status_code=status,
        )
    if status == 429:
        return NimbusPostApiError(
            "NimbusPost rate limit exceeded (429).", error_type="http_429", status_code=status
        )
    if 500 <= status < 600:
        return NimbusPostApiError(
            f"NimbusPost API returned a server error ({status}).",
            error_type=f"http_{status}" if status in (500, 502, 503, 504) else "http_500",
            status_code=status,
        )
    return NimbusPostApiError(
        f"NimbusPost API returned an unexpected status ({status}).",
        error_type="permanent_error",
        status_code=status,
    )


def classify_transport_error(exc: httpx.TransportError) -> NimbusPostApiError:
    if isinstance(exc, httpx.TimeoutException):
        return NimbusPostApiError("Timed out contacting NimbusPost.", error_type="timeout")
    return NimbusPostApiError(
        f"Network error contacting NimbusPost: {exc}", error_type="network_error"
    )
