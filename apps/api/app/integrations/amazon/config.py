from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.config import settings as default_settings

ENDPOINTS = {
    "na": "https://sellingpartnerapi-na.amazon.com",
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

_REQUIRED = {
    "AMAZON_SP_API_CLIENT_ID": "client_id",
    "AMAZON_SP_API_CLIENT_SECRET": "client_secret",
    "AMAZON_SP_API_REFRESH_TOKEN": "refresh_token",
    "AMAZON_SP_API_MARKETPLACE_ID": "marketplace_id",
    "AMAZON_SP_API_REGION": "region",
}


@dataclass(frozen=True)
class AmazonConfig:
    client_id: str | None
    client_secret: str | None
    refresh_token: str | None
    marketplace_id: str | None
    region: str | None
    app_id: str | None = None
    selling_partner_id: str | None = None
    timezone: str = "Asia/Kolkata"
    max_range_days: int = 90

    @classmethod
    def from_settings(cls, s: Settings = default_settings) -> AmazonConfig:
        return cls(
            client_id=s.AMAZON_SP_API_CLIENT_ID,
            client_secret=s.AMAZON_SP_API_CLIENT_SECRET,
            refresh_token=s.AMAZON_SP_API_REFRESH_TOKEN,
            marketplace_id=s.AMAZON_SP_API_MARKETPLACE_ID,
            region=(s.AMAZON_SP_API_REGION or "").lower() or None,
            app_id=s.AMAZON_SP_API_APP_ID,
            selling_partner_id=s.AMAZON_SP_API_SELLING_PARTNER_ID,
            timezone=s.AMAZON_SP_API_TIMEZONE,
            max_range_days=s.AMAZON_SP_API_MAX_RANGE_DAYS,
        )

    def missing(self) -> list[str]:
        out = [env for env, attr in _REQUIRED.items() if not getattr(self, attr)]
        if self.region and self.region not in ENDPOINTS:
            out.append("AMAZON_SP_API_REGION (must be na, eu or fe)")
        return out

    @property
    def configured(self) -> bool:
        return not self.missing()

    @property
    def base_url(self) -> str:
        return ENDPOINTS[self.region or ""]
