"""MeeshoAdapter — Marketplace Orders module, Phase 1 (stub).

Implements `app.integrations.base.IntegrationAdapter` with every method
reporting a clean "not configured" / empty-page outcome — no real Meesho
API call is made anywhere in this file (Meesho has no confirmed public
seller API; see the package docstring in `app/integrations/meesho/
__init__.py`). Structurally mirrors `app.integrations.amazon.adapter.
AmazonAdapter`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.exceptions import IntegrationError
from app.integrations.base import FetchPage, HealthCheckResult, IntegrationAdapter
from app.integrations.credentials import get_credential_provider
from app.integrations.meesho.normalizer import ENTITY_NORMALIZERS
from app.models.integration import IntegrationCode

_NOT_CONFIGURED_MESSAGE = (
    "Meesho integration is not configured. Meesho has no confirmed public "
    "seller API — this adapter stays a stub until Meesho grants official, "
    "authorized API access (see app.integrations.meesho's module docstring)."
)


class MeeshoAdapter(IntegrationAdapter):
    code = IntegrationCode.MEESHO

    def __init__(self) -> None:
        self._configured = (
            get_credential_provider().get_credential(self.code, "access_token") is not None
        )

    async def authenticate(self) -> None:
        raise IntegrationError(_NOT_CONFIGURED_MESSAGE, details={"error_type": "not_configured"})

    async def health_check(self) -> HealthCheckResult:
        return HealthCheckResult(connected=False, error_message=_NOT_CONFIGURED_MESSAGE)

    async def fetch(
        self, entity_type: str, *, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        return FetchPage(nodes=[], next_cursor=None, has_more=False)

    async def fetch_incremental(
        self, entity_type: str, *, since: datetime, cursor: str | None = None, limit: int = 50
    ) -> FetchPage:
        return FetchPage(nodes=[], next_cursor=None, has_more=False)

    async def process_webhook(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "MeeshoAdapter is a Phase 1 stub — webhook processing is not yet implemented."
        )

    def normalize(self, entity_type: str, raw: dict[str, Any]) -> dict[str, Any]:
        normalizer = ENTITY_NORMALIZERS.get(entity_type)
        if normalizer is None:
            raise IntegrationError(
                f"Meesho adapter does not support entity_type '{entity_type}'.",
                details={"error_type": "validation_error"},
            )
        return normalizer.normalize(raw)
