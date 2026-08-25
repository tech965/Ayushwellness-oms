"""Shopify configuration — reads `SHOPIFY_*` settings only, never hardcoded.

`ShopifyConfig.from_settings()` returns `None` when the store isn't
configured (missing domain/token) so the adapter can report
"not configured" instead of attempting a call with an empty token.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class ShopifyConfig:
    shop_domain: str
    access_token: str
    api_version: str

    @property
    def graphql_url(self) -> str:
        return f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"

    @classmethod
    def from_settings(cls) -> ShopifyConfig | None:
        if not settings.SHOPIFY_STORE_DOMAIN or not settings.SHOPIFY_ACCESS_TOKEN:
            return None
        return cls(
            shop_domain=settings.SHOPIFY_STORE_DOMAIN,
            access_token=settings.SHOPIFY_ACCESS_TOKEN,
            api_version=settings.SHOPIFY_API_VERSION,
        )
