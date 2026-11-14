"""Flipkart Seller API configuration.

Only `FLIPKART_APP_ID`/`FLIPKART_APP_SECRET` are documented as required
by Flipkart's OAuth2 client_credentials flow
(https://seller.flipkart.com/api-docs/FMSAPI.html, "Self Access" /
Manage Profile > Developer Access). That page's own text: "a unique
Application ID (`<appid>`) and Application Secret (`<app-secret>`) pair
is assigned" -- no other credential is documented anywhere on that page
or the Order API reference. If live testing later surfaces a required
additional credential (e.g. a per-seller location id), add it here only
once confirmed against the official docs, never guessed.

Only one base host appears anywhere in Flipkart's official docs:
`https://api.flipkart.net` -- no separate sandbox host is documented, so
none is modeled here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.config import settings as default_settings

BASE_URL = "https://api.flipkart.net"
TOKEN_URL = f"{BASE_URL}/oauth-service/oauth/token"
# Documented literally as `scope=Seller_Api` in every curl/Python/Java/C#
# example on FMSAPI.html; the syntax line separately shows
# `scope=Seller_Api,Default` but no example actually uses `,Default` --
# using the scope every real example uses.
OAUTH_SCOPE = "Seller_Api"

_REQUIRED = {
    "FLIPKART_APP_ID": "app_id",
    "FLIPKART_APP_SECRET": "app_secret",
}


@dataclass(frozen=True)
class FlipkartConfig:
    app_id: str | None
    app_secret: str | None

    @classmethod
    def from_settings(cls, s: Settings = default_settings) -> FlipkartConfig:
        return cls(app_id=s.FLIPKART_APP_ID, app_secret=s.FLIPKART_APP_SECRET)

    def missing(self) -> list[str]:
        return [env for env, attr in _REQUIRED.items() if not getattr(self, attr)]

    @property
    def configured(self) -> bool:
        return not self.missing()
