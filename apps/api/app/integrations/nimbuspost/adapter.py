"""NimbusPostAdapter — the only NimbusPost-specific implementation of
`app.integrations.base.IntegrationAdapter`. Phase 1 of this integration
(2026-11-20): authentication + Test Connection only — see
`docs/architecture/integrations.md` for the overall pattern this mirrors
(`ShiprocketAdapter`). Courier operations (serviceability, shipment
creation, AWB, tracking, cancellation) are a later phase, pending
verified request/response payload schemas for NimbusPost's
`shipments`/`courier/serviceability` endpoints (confirmed to exist, by
path, from NimbusPost's official SDK source — their field-level schemas
are not; see the architecture audit for what's confirmed vs. not).

NimbusPost is not an order-pull source (unlike Shopify) — `fetch`/
`fetch_incremental`/`process_webhook`/`normalize` are present only to
satisfy `IntegrationAdapter`'s interface and are not yet used by
anything; they raise rather than silently return empty data, so a wrong
assumption surfaces immediately instead of looking like "no records."
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.integrations.base import FetchPage, HealthCheckResult, IntegrationAdapter
from app.integrations.nimbuspost.client import NimbusPostClient
from app.integrations.nimbuspost.config import NimbusPostConfig
from app.integrations.nimbuspost.errors import NimbusPostApiError
from app.models.integration import IntegrationCode

logger = get_logger(__name__)


class NimbusPostAdapter(IntegrationAdapter):
    code = IntegrationCode.NIMBUSPOST

    def __init__(self, client: NimbusPostClient | None = None) -> None:
        self._client = client
        self._configured = client is not None or NimbusPostConfig.from_settings() is not None

    def _get_client(self) -> NimbusPostClient:
        if self._client is not None:
            return self._client
        config = NimbusPostConfig.from_settings()
        if config is None:
            logger.warning("nimbuspost_not_configured", reason="missing_email_or_password")
            raise IntegrationError(
                "NimbusPost integration is not configured "
                "(missing NIMBUSPOST_EMAIL/NIMBUSPOST_PASSWORD).",
                details={"error_type": "not_configured"},
            )
        self._client = NimbusPostClient(config)
        return self._client

    async def aclose(self) -> None:
        """Closes and drops the cached client — see `ShiprocketAdapter.
        aclose` for why (process-lifetime-singleton-vs-per-task-event-
        loop issue); same pattern here.
        """
        if self._client is None:
            return
        client, self._client = self._client, None
        await client.aclose()

    # --- IntegrationAdapter interface --------------------------------

    async def authenticate(self) -> None:
        client = self._get_client()
        try:
            await client.ensure_authenticated()
        except NimbusPostApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc

    async def health_check(self) -> HealthCheckResult:
        if not self._configured and self._client is None:
            return HealthCheckResult(
                connected=False,
                error_message=(
                    "NimbusPost integration is not configured "
                    "(missing NIMBUSPOST_EMAIL/NIMBUSPOST_PASSWORD)."
                ),
            )

        started = time.perf_counter()
        try:
            await self.authenticate()
        except IntegrationError as exc:
            return HealthCheckResult(
                connected=False,
                response_time_ms=(time.perf_counter() - started) * 1000,
                error_message=exc.message,
            )
        return HealthCheckResult(
            connected=True, response_time_ms=(time.perf_counter() - started) * 1000
        )

    async def fetch(
        self, entity_type: str, *, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        raise IntegrationError(
            "NimbusPost adapter does not yet support fetch() — authentication/Test "
            "Connection only in this phase.",
            details={"error_type": "validation_error"},
        )

    async def fetch_incremental(
        self, entity_type: str, *, since: datetime, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        raise IntegrationError(
            "NimbusPost adapter does not yet support fetch_incremental() — "
            "authentication/Test Connection only in this phase.",
            details={"error_type": "validation_error"},
        )

    async def process_webhook(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"entity_type": None, "normalized": None}

    def normalize(self, entity_type: str, raw: dict[str, Any]) -> dict[str, Any]:
        raise IntegrationError(
            f"NimbusPost adapter does not support entity_type '{entity_type}' yet.",
            details={"error_type": "validation_error"},
        )
