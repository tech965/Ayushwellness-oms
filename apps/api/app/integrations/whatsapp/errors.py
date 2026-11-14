"""WhatsApp-specific errors, mapped to the standard OMS error envelope."""

from __future__ import annotations

from app.core.exceptions import IntegrationError


class WhatsAppError(IntegrationError):
    status_code = 502
    error_code = "whatsapp_error"


class WhatsAppNotConfiguredError(WhatsAppError):
    status_code = 503
    error_code = "whatsapp_not_configured"


class WhatsAppPermissionError(WhatsAppError):
    status_code = 403
    error_code = "whatsapp_missing_permission"


class WhatsAppRateLimitError(WhatsAppError):
    status_code = 429
    error_code = "whatsapp_rate_limited"


class WhatsAppWindowClosedError(WhatsAppError):
    """Raised when a free-form (non-template) send is attempted outside
    the recipient's 24-hour customer service window -- Meta: "When the
    window closes, you can only send pre-approved template messages."
    Callers should fall back to a template send, never silently drop
    the message and never silently bypass the rule.
    """

    status_code = 422
    error_code = "whatsapp_window_closed"


class WhatsAppOptedOutError(WhatsAppError):
    status_code = 422
    error_code = "whatsapp_opted_out"
