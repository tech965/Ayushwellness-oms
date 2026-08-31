"""Shopify configuration — reads `SHOPIFY_*` settings only, never hardcoded.

`ShopifyConfig.from_settings()` returns `None` when the store isn't
configured under either supported auth mode, so the adapter can report
"not configured" instead of attempting a call with no usable credential.

Two mutually exclusive auth modes are supported:

- Client Credentials Grant (`SHOPIFY_CLIENT_ID`/`SHOPIFY_CLIENT_SECRET`)
  — Shopify's current Dev Dashboard app model; preferred whenever both
  are configured, even if a legacy `SHOPIFY_ACCESS_TOKEN` is also still
  set, so a stale permanently-stored token is never accidentally kept in
  use once an app has been migrated to this flow. `ShopifyClient` fetches
  and caches a real access token via `app.integrations.shopify.auth.
  ShopifyTokenManager`.
- A manually-generated, permanent custom-app access token
  (`SHOPIFY_ACCESS_TOKEN`) — the only auth mode this integration
  supported before the Client Credentials Grant migration. Kept for
  backward compatibility; used only when CLIENT_ID/SECRET aren't set.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class ShopifyConfig:
    shop_domain: str
    api_version: str
    access_token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None

    @property
    def graphql_url(self) -> str:
        return f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"

    @property
    def uses_client_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @classmethod
    def from_settings(cls) -> ShopifyConfig | None:
        # .strip(): the same class of bug already fixed once for
        # SHOPIFY_WEBHOOK_SECRET (see app/api/v1/webhooks/shopify.py) --
        # a credential pasted into Render's dashboard can silently pick
        # up a trailing newline/space that's invisible when eyeballing
        # the value but makes it byte-for-byte wrong. Applied to every
        # Shopify credential/domain setting here, not just the two new
        # ones, since SHOPIFY_STORE_DOMAIN feeds directly into the token
        # endpoint's URL.
        store_domain = (settings.SHOPIFY_STORE_DOMAIN or "").strip() or None
        if not store_domain:
            return None
        client_id = (settings.SHOPIFY_CLIENT_ID or "").strip() or None
        client_secret = (settings.SHOPIFY_CLIENT_SECRET or "").strip() or None
        if client_id and client_secret:
            return cls(
                shop_domain=store_domain,
                api_version=settings.SHOPIFY_API_VERSION,
                client_id=client_id,
                client_secret=client_secret,
            )
        access_token = (settings.SHOPIFY_ACCESS_TOKEN or "").strip() or None
        if access_token:
            return cls(
                shop_domain=store_domain,
                api_version=settings.SHOPIFY_API_VERSION,
                access_token=access_token,
            )
        return None
