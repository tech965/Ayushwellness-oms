from __future__ import annotations

from pathlib import Path

from app.integrations.retry import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    compute_backoff_seconds,
    is_retryable_error_type,
    should_retry,
)


# 9. Retry logic
def test_retryable_error_types_are_retried() -> None:
    assert is_retryable_error_type("timeout") is True
    assert is_retryable_error_type("http_429") is True
    assert is_retryable_error_type("http_503") is True


def test_non_retryable_error_types_are_not_retried() -> None:
    assert is_retryable_error_type("authentication_error") is False
    assert is_retryable_error_type("validation_error") is False
    assert is_retryable_error_type("invalid_payload") is False


def test_should_retry_true_below_max_for_retryable_error() -> None:
    assert should_retry(error_type="timeout", retry_count=0) is True
    assert should_retry(error_type="timeout", retry_count=4) is True


def test_should_retry_false_for_non_retryable_error_even_with_no_attempts() -> None:
    assert should_retry(error_type="authentication_error", retry_count=0) is False


# 10. Retry limit
def test_should_retry_false_once_max_retries_reached() -> None:
    policy = RetryPolicy(max_retries=3)
    assert should_retry(error_type="timeout", retry_count=3, policy=policy) is False
    assert should_retry(error_type="timeout", retry_count=2, policy=policy) is True


def test_backoff_grows_exponentially_and_caps_at_max_delay() -> None:
    policy = RetryPolicy(base_delay_seconds=10, max_delay_seconds=100, backoff_multiplier=2)
    assert compute_backoff_seconds(attempt=1, policy=policy) == 10
    assert compute_backoff_seconds(attempt=2, policy=policy) == 20
    assert compute_backoff_seconds(attempt=3, policy=policy) == 40
    assert compute_backoff_seconds(attempt=10, policy=policy) == 100


def test_default_retry_policy_matches_documented_defaults() -> None:
    assert DEFAULT_RETRY_POLICY.max_retries == 5


# 16. Celery task registration
def test_celery_tasks_are_registered() -> None:
    import app.tasks.retry_processing  # noqa: F401
    import app.tasks.sync_tasks  # noqa: F401
    import app.tasks.webhook_processing  # noqa: F401
    from app.workers.celery_app import celery_app

    registered = set(celery_app.tasks.keys())
    assert "sync.run" in registered
    assert "sync.execute" in registered
    assert "webhooks.process_event" in registered
    assert "sync.retry_failed" in registered


# 15. Database migration
def test_migration_chain_has_a_single_head_through_phase_2_1_through_2_4() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api_root = Path(__file__).resolve().parent.parent
    config = Config(str(api_root / "alembic.ini"))
    config.set_main_option("script_location", str(api_root / "alembic"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1

    head_revision = script.get_revision(heads[0])
    assert head_revision is not None
    assert head_revision.revision == "50337406e09a"
    assert head_revision.down_revision == "1b440c092593"

    phase_2_3_revision = script.get_revision("1b440c092593")
    assert phase_2_3_revision is not None
    assert phase_2_3_revision.down_revision == "54ebf7a087e2"

    phase_2_2_revision = script.get_revision("54ebf7a087e2")
    assert phase_2_2_revision is not None
    assert phase_2_2_revision.down_revision == "4d60488e0bdb"

    phase_2_1_revision = script.get_revision("4d60488e0bdb")
    assert phase_2_1_revision is not None
    assert phase_2_1_revision.down_revision == "abbf8b3ee1d0"
