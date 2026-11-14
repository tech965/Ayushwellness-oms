"""Amazon order normalizer — Marketplace Orders module, Phase 1 (stub).

Never actually called in Phase 1: `AmazonAdapter.fetch`/`fetch_incremental`
always return zero nodes, so `AmazonAdapter.normalize` never has a real
record to hand this. Kept as a concrete `OrderNormalizer` subclass (rather
than omitted) so `AmazonAdapter.normalize`'s `ENTITY_NORMALIZERS.get(...)`
dispatch already has the right shape for a later phase to fill in.

A real implementation maps one Amazon SP-API order (Orders API /
`getOrders` + `getOrderItems`) to the exact kwargs
`app.services.order_service.OrderService.upsert_synced_order` expects:
at minimum `source_system="amazon"` (`app.models.mixins.SourceSystem.
AMAZON`), `external_id=<AmazonOrderId>`, `order_number`, `order_datetime`,
customer fields, `items` (SKU/quantity/unit_price per `OrderItem`),
`payment_type`/`payment_status`, `shipping_address`. Duplicate orders are
already prevented for free by `Order`'s existing
`(source_system, external_id)` unique constraint — no new dedup logic
needed here.
"""

from __future__ import annotations

from typing import Any

from app.integrations.normalizer import OrderNormalizer


class AmazonOrderNormalizer(OrderNormalizer):
    entity_type = "orders"

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            "AmazonOrderNormalizer is a Phase 1 stub — AmazonAdapter.fetch() "
            "never returns real nodes yet, so this is never actually called."
        )


ENTITY_NORMALIZERS: dict[str, OrderNormalizer] = {"orders": AmazonOrderNormalizer()}
