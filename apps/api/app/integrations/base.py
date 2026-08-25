"""Generic integration adapter interface.

Every provider (`ShopifyAdapter`, `ShiprocketAdapter`, `BlueDartAdapter`,
...) implements this interface starting Phase 2; nothing here knows
about any specific provider's API shape. `app.services.sync_service` and
`app.services.integration_service` depend only on `IntegrationAdapter`,
never on a concrete subclass, via the registry in
`app.integrations.registry`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class HealthCheckResult:
    connected: bool
    response_time_ms: float | None = None
    last_successful_at: datetime | None = None
    last_failure_at: datetime | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class FetchPage:
    """One page of `fetch`/`fetch_incremental` results. `cursor`-based —
    every provider that paginates by opaque cursor (Shopify's GraphQL
    `pageInfo`, and most modern REST APIs) can return this shape
    unchanged; a provider `SyncService.execute_sync` loops over.
    """

    nodes: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool


class IntegrationAdapter(ABC):
    """One instance per provider. `code` must match `Integration.code`."""

    code: str

    @abstractmethod
    async def authenticate(self) -> None:
        """Verify/refresh credentials. Raises `IntegrationError` on failure."""

    @abstractmethod
    async def health_check(self) -> HealthCheckResult:
        """Never raises — failures are reported inside the result."""

    @abstractmethod
    async def fetch(
        self, entity_type: str, *, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        """One page of a full pull of `entity_type` (e.g. "orders",
        "customers"). Pass `cursor` (from the previous page's
        `next_cursor`) to continue; `cursor=None` starts from the beginning.
        """

    @abstractmethod
    async def fetch_incremental(
        self, entity_type: str, *, since: datetime, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        """One page of records changed since `since`."""

    @abstractmethod
    async def process_webhook(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Provider-specific webhook handling, called after the generic
        `WebhookEvent` idempotency check has already accepted the event.
        """

    @abstractmethod
    def normalize(self, entity_type: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Delegates to this provider's `Normalizer` for `entity_type`."""
