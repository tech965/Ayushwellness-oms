from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.integrations.base import HealthCheckResult
from app.integrations.registry import get_adapter
from app.models.auth import User
from app.models.enums import IntegrationStatus
from app.models.integration import Integration, SyncJob
from app.repositories.integration import IntegrationRepository
from app.repositories.sync_job import SyncJobRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService


class IntegrationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.integrations = IntegrationRepository(session)
        self.sync_jobs = SyncJobRepository(session)
        self.audit = AuditService(session)

    async def list_integrations(
        self, *, page_params: PageParams, sort_params: SortParams
    ) -> tuple[list[Integration], int]:
        items, total = await self.integrations.list(
            page_params=page_params, sort_params=sort_params
        )
        return list(items), total

    async def get_integration(self, integration_id: uuid.UUID) -> Integration:
        integration = await self.integrations.get_by_id(integration_id)
        if integration is None:
            raise NotFoundError("Integration not found.")
        return integration

    async def get_sync_history(
        self, integration_id: uuid.UUID, *, page_params: PageParams, sort_params: SortParams
    ) -> tuple[list[SyncJob], int]:
        await self.get_integration(integration_id)
        items, total = await self.sync_jobs.list(
            page_params=page_params,
            sort_params=sort_params,
            query=self.sync_jobs.for_integration(integration_id),
        )
        return list(items), total

    async def get_health(self, integration_id: uuid.UUID) -> HealthCheckResult:
        """Read-only snapshot from the last persisted state — does not make
        a live call. Use `run_health_check` to actively probe.
        """
        integration = await self.get_integration(integration_id)
        return HealthCheckResult(
            connected=integration.status == IntegrationStatus.CONNECTED,
            last_successful_at=integration.last_successful_sync_at,
            last_failure_at=integration.last_failure_at,
            error_message=integration.last_failure_message,
        )

    async def run_health_check(
        self, integration_id: uuid.UUID, *, actor: User | None = None
    ) -> HealthCheckResult:
        """Actively probes the registered adapter (Phase 2+) and persists
        the outcome onto `Integration`. In Phase 2.1 no adapter is ever
        registered, so this always reports a clean "not connected, no
        adapter registered" result rather than attempting any network call.
        """
        integration = await self.get_integration(integration_id)
        adapter = get_adapter(integration.code)

        if adapter is None:
            result = HealthCheckResult(
                connected=False,
                error_message=(
                    f"No adapter registered for integration '{integration.code}' "
                    "in this deployment."
                ),
            )
        else:
            started = time.perf_counter()
            result = await adapter.health_check()
            if result.response_time_ms is None:
                result = HealthCheckResult(
                    connected=result.connected,
                    response_time_ms=(time.perf_counter() - started) * 1000,
                    last_successful_at=result.last_successful_at,
                    last_failure_at=result.last_failure_at,
                    error_message=result.error_message,
                )

        now = datetime.now(UTC)
        was_connected = integration.status == IntegrationStatus.CONNECTED
        if result.connected:
            await self.integrations.update(
                integration,
                status=IntegrationStatus.CONNECTED,
                last_successful_sync_at=now,
            )
        else:
            # "not configured" (missing credentials) is DISCONNECTED, not
            # an error — an operator hasn't set anything up wrong, there's
            # just nothing to connect to yet. Any other failure (auth,
            # permission, rate limit, network, provider error) is ERROR.
            not_configured = "not configured" in (result.error_message or "").lower()
            await self.integrations.update(
                integration,
                status=(
                    IntegrationStatus.DISCONNECTED if not_configured else IntegrationStatus.ERROR
                ),
                last_failure_at=now,
                last_failure_message=result.error_message,
            )

        newly_connected = result.connected and not was_connected
        await self.audit.record(
            user=actor,
            action="integration.connected" if newly_connected else "integration.health_check",
            entity_type="integration",
            entity_id=str(integration.id),
            new_value={"connected": result.connected, "error_message": result.error_message},
        )
        await self.session.commit()
        return result
