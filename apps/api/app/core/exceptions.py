"""Domain exception hierarchy.

Services and repositories raise these instead of HTTPException so business
logic stays framework-agnostic. `app.middleware.error_handler` maps them
to the standard API error response shape.
"""

from __future__ import annotations


class OMSError(Exception):
    """Base class for all domain errors."""

    status_code: int = 500
    error_code: str = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(OMSError):
    status_code = 404
    error_code = "not_found"


class ValidationError(OMSError):
    status_code = 422
    error_code = "validation_error"


class ConflictError(OMSError):
    status_code = 409
    error_code = "conflict"


class AuthenticationError(OMSError):
    status_code = 401
    error_code = "authentication_error"


class AuthorizationError(OMSError):
    status_code = 403
    error_code = "authorization_error"


class IntegrationError(OMSError):
    """Raised by adapters/services when an external system call fails."""

    status_code = 502
    error_code = "integration_error"


class WebhookVerificationError(OMSError):
    status_code = 401
    error_code = "webhook_verification_error"


class RateLimitError(OMSError):
    status_code = 429
    error_code = "rate_limit_exceeded"
