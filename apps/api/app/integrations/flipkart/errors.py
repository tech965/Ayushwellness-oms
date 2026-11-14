"""Flipkart-specific errors, mapped to the standard OMS error envelope."""

from __future__ import annotations

from app.core.exceptions import IntegrationError


class FlipkartError(IntegrationError):
    status_code = 502
    error_code = "flipkart_error"


class FlipkartNotConfiguredError(FlipkartError):
    status_code = 503
    error_code = "flipkart_not_configured"


class FlipkartAuthError(FlipkartError):
    status_code = 502
    error_code = "flipkart_auth_failed"


class FlipkartPermissionError(FlipkartError):
    status_code = 403
    error_code = "flipkart_missing_permission"


class FlipkartRateLimitError(FlipkartError):
    status_code = 429
    error_code = "flipkart_rate_limited"
