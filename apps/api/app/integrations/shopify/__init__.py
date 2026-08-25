"""Shopify integration — Phase 2.2.

Contains: `ShopifyClient` (HTTP), `ShopifyConfig` (env-var config),
`ShopifyAdapter` (implements `app.integrations.base.IntegrationAdapter`),
normalizers, GraphQL query documents, and webhook HMAC verification. See
`docs/architecture/integrations.md` for the full data-flow and field
mapping documentation.
"""

from __future__ import annotations

from app.integrations.registry import register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter

__all__ = ["ShopifyAdapter", "register"]


def register() -> None:
    """Registers a `ShopifyAdapter` regardless of whether credentials are
    configured — `ShopifyAdapter.health_check()`/`fetch()` report a clean
    "not configured" outcome rather than the caller falling back to
    Phase 2.1's generic "no adapter registered" message, which would be
    misleading now that Shopify support actually exists.
    """
    register_adapter(ShopifyAdapter())
