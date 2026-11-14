"""Meesho order normalizer — Marketplace Orders module, Phase 1 (stub).

Never actually called in Phase 1 — see `app.integrations.amazon.
normalizer`'s docstring for the full rationale (identical here). A real
implementation maps one Meesho order to the exact kwargs `OrderService.
upsert_synced_order` expects, with `source_system="meesho"`
(`app.models.mixins.SourceSystem.MEESHO`) — but only once Meesho grants
official, authorized API access (see `app.integrations.meesho`'s package
docstring).
"""

from __future__ import annotations

from typing import Any

from app.integrations.normalizer import OrderNormalizer


class MeeshoOrderNormalizer(OrderNormalizer):
    entity_type = "orders"

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "MeeshoOrderNormalizer is a Phase 1 stub — MeeshoAdapter.fetch() "
            "never returns real nodes yet, so this is never actually called."
        )


ENTITY_NORMALIZERS: dict[str, OrderNormalizer] = {"orders": MeeshoOrderNormalizer()}
