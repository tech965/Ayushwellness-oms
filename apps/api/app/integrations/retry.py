"""Reusable retry/backoff policy shared by every integration.

Not tied to Celery or any specific provider — `app.tasks.retry_processing`
and `SyncService` both consult `should_retry`/`compute_backoff_seconds`
so retry behavior is defined once instead of per-adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

# Error types a caller should retry — transient by nature.
RETRYABLE_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "timeout",
        "network_error",
        "connection_error",
        "http_429",
        "http_500",
        "http_502",
        "http_503",
        "http_504",
        "rate_limited",
    }
)

# Error types that will never succeed on retry — retrying just wastes a slot.
NON_RETRYABLE_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "authentication_error",
        "authorization_error",
        "invalid_payload",
        "validation_error",
        "not_found",
        "permanent_error",
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 5
    base_delay_seconds: float = 60.0
    max_delay_seconds: float = 3600.0
    backoff_multiplier: float = 2.0


DEFAULT_RETRY_POLICY = RetryPolicy()


def is_retryable_error_type(error_type: str) -> bool:
    """Unknown error types default to retryable — a transient classifier
    gap should not silently strand a job in a failed, un-retried state.
    Explicitly `NON_RETRYABLE_ERROR_TYPES` are the only ones excluded.
    """
    return error_type not in NON_RETRYABLE_ERROR_TYPES


def should_retry(
    *, error_type: str, retry_count: int, policy: RetryPolicy = DEFAULT_RETRY_POLICY
) -> bool:
    if retry_count >= policy.max_retries:
        return False
    return is_retryable_error_type(error_type)


def compute_backoff_seconds(*, attempt: int, policy: RetryPolicy = DEFAULT_RETRY_POLICY) -> float:
    """`attempt` is 1-indexed (the first retry is attempt 1)."""
    delay = policy.base_delay_seconds * (policy.backoff_multiplier ** max(attempt - 1, 0))
    return min(delay, policy.max_delay_seconds)
