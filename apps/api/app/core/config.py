"""Application settings loaded from environment variables.

Every external integration is OPTIONAL at the settings level — a missing
Shopify/Shiprocket/courier credential must never prevent the API from
starting. Integration modules check for their own required settings and
report themselves as DISCONNECTED when credentials are absent.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    APP_NAME: str = "AyushWellness OMS API"
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # --- Server ---
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # --- CORS ---
    # NoDecode: without it, pydantic-settings tries to JSON-decode this env
    # var before the comma-split validator below ever runs, so a plain
    # comma-separated value (the format the validator is written for)
    # crashes app startup with a JSON parse error.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    # --- Database ---
    DATABASE_URL: str = (
        "postgresql+asyncpg://oms_user:oms_password@localhost:5432/ayushwellness_oms"
    )
    DATABASE_ECHO: bool = False
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # --- Redis / Celery ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    # --- Auth / JWT ---
    JWT_SECRET: str = "CHANGE_ME_IN_PRODUCTION"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE: int = 900  # seconds (15 min)
    JWT_REFRESH_TOKEN_EXPIRE: int = 1209600  # seconds (14 days)

    # --- Rate limiting ---
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "100/minute"

    # --- Logging ---
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # --- Shopify (optional — connected starting Phase 2.2 when credentials
    # are present; the API never fails to start without them) ---
    SHOPIFY_API_KEY: str | None = None
    SHOPIFY_API_SECRET: str | None = None
    SHOPIFY_ACCESS_TOKEN: str | None = None
    SHOPIFY_WEBHOOK_SECRET: str | None = None
    SHOPIFY_STORE_DOMAIN: str | None = None
    # GraphQL Admin API — REST is legacy as of April 2025; new integrations
    # must use GraphQL. Quarterly release, e.g. "2026-01". Verify against
    # https://shopify.dev/docs/api/admin-graphql before bumping.
    SHOPIFY_API_VERSION: str = "2026-01"

    # --- Shiprocket (optional — connected starting Phase 2.3 when
    # credentials are present; the API never fails to start without them) ---
    SHIPROCKET_EMAIL: str | None = None
    SHIPROCKET_PASSWORD: str | None = None
    SHIPROCKET_API_URL: str = "https://apiv2.shiprocket.in/v1/external"
    SHIPROCKET_WEBHOOK_SECRET: str | None = None
    # Pickup location nickname exactly as configured in the Shiprocket
    # dashboard — required by the order-create API, not guessable/optional.
    SHIPROCKET_PICKUP_LOCATION: str | None = None

    # --- Blue Dart (optional) ---
    BLUE_DART_API_URL: str | None = None
    BLUE_DART_API_KEY: str | None = None
    BLUE_DART_CLIENT_ID: str | None = None
    BLUE_DART_CLIENT_SECRET: str | None = None

    # --- WhatsApp (optional, V2) ---
    WHATSAPP_API_URL: str | None = None
    WHATSAPP_ACCESS_TOKEN: str | None = None

    # --- Meta (optional, V3) ---
    META_APP_ID: str | None = None
    META_APP_SECRET: str | None = None
    META_ACCESS_TOKEN: str | None = None

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def celery_broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def celery_result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
