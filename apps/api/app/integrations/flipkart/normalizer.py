"""Flipkart order normalizer — Marketplace Orders module, Phase 1 (stub).

Never actually called in Phase 1 — see `app.integrations.amazon.
normalizer`'s docstring for the full rationale (identical here). A real
implementation maps one Flipkart Seller API order to the exact kwargs
`OrderService.upsert_synced_order` expects, with `source_system="flipkart"`
(`app.models.mixins.SourceSystem.FLIPKART`) and the Flipkart order id as
`external_id`.
"""

from __future__ import annotations

from typing import Any

from app.integrations.normalizer import OrderNormalizer


class FlipkartOrderNormalizer(OrderNormalizer):
    entity_type = "orders"

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "FlipkartOrderNormalizer is a Phase 1 stub — FlipkartAdapter.fetch() "
            "never returns real nodes yet, so this is never actually called."
        )


ENTITY_NORMALIZERS: dict[str, OrderNormalizer] = {"orders": FlipkartOrderNormalizer()}
