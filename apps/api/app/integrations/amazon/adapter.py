"""AmazonAdapter — Marketplace Orders module, Phase 1 (stub).

Implements `app.integrations.base.IntegrationAdapter` with every method
reporting a clean "not configured" / empty-page outcome — no real Amazon
SP-API call is made anywhere in this file. See the package docstring
(`app/integrations/amazon/__init__.py`) and `AmazonOrderNormalizer`'s
docstring for what a later phase needs to fill in to make this real.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.exceptions import IntegrationError
from app.integrations.amazon.normalizer import ENTITY_NORMALIZERS
from app.integrations.base import FetchPage, HealthCheckResult, IntegrationAdapter
from app.integrations.credentials import get_credential_provider
from app.models.integration import IntegrationCode

_NOT_CONFIGURED_MESSAGE = (
    "Amazon integration is not configured (no AMAZON_* credential is set). "
    "This is a Phase 1 stub adapter — see app.integrations.amazon's module "
    "docstring for what a real SP-API implementation needs to provide."
)


class AmazonAdapter(IntegrationAdapter):
    code = IntegrationCode.AMAZON

    def __init__(self) -> None:
        # Mirrors `ShopifyAdapter.__init__`'s `self._configured` check --
        # resolved once at registration time via the same credential seam
        # every other adapter uses (`app.integrations.credentials`), never
        # `os.environ` directly. Always `False` until real AMAZON_* env
        # vars/secrets exist.
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
            "AmazonAdapter is a Phase 1 stub — webhook processing is not yet implemented."
        )

    def normalize(self, entity_type: str, raw: dict[str, Any]) -> dict[str, Any]:
        normalizer = ENTITY_NORMALIZERS.get(entity_type)
        if normalizer is None:
            raise IntegrationError(
                f"Amazon adapter does not support entity_type '{entity_type}'.",
                details={"error_type": "validation_error"},
            )
        return normalizer.normalize(raw)
