"""Amazon-specific errors, mapped to the standard OMS error envelope."""

from __future__ import annotations

from app.core.exceptions import IntegrationError


class AmazonError(IntegrationError):
    status_code = 502
    error_code = "amazon_error"


class AmazonNotConfiguredError(AmazonError):
    status_code = 503
    error_code = "amazon_not_configured"


class AmazonAuthError(AmazonError):
    status_code = 502
    error_code = "amazon_auth_failed"


class AmazonPermissionError(AmazonError):
    status_code = 403
    error_code = "amazon_missing_permission"


class AmazonRateLimitError(AmazonError):
    status_code = 429
    error_code = "amazon_rate_limited"


class AmazonDateRangeError(AmazonError):
    status_code = 422
    error_code = "amazon_invalid_date_range"


class AmazonReportError(AmazonError):
    status_code = 502
    error_code = "amazon_report_failed"
