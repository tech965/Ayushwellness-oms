"""Normalizer interfaces: provider payload -> OMS-internal schema shape.

`app/services/*` and `app/models/*` never see a raw provider payload —
every adapter routes `fetch()`/`process_webhook()` results through the
matching `Normalizer` before anything reaches a repository. Phase 2
concrete normalizers (`ShopifyCustomerNormalizer`, `ShopifyOrderNormalizer`,
`ShiprocketShipmentNormalizer`, ...) subclass these; Phase 2.1 only
defines the shape.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Normalizer(ABC):
    """Maps one provider's raw record shape to the dict of fields the
    matching OMS service/repository expects (e.g. the kwargs
    `CustomerService.create_customer` or
    `CustomerRepository.upsert_by_external_id` accepts).
    """

    entity_type: str

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]: ...


class CustomerNormalizer(Normalizer):
    entity_type = "customers"


class ProductNormalizer(Normalizer):
    entity_type = "products"


class OrderNormalizer(Normalizer):
    entity_type = "orders"


class ShipmentNormalizer(Normalizer):
    entity_type = "shipments"
