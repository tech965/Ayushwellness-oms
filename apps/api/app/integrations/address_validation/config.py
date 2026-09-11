"""Reads only settings, never hardcoded credentials -- both `from_settings()`
classmethods below return `None` when their provider isn't configured,
exactly mirroring `ShiprocketConfig.from_settings()`'s "not configured ->
None, never a login attempt with empty credentials" contract. See
`get_address_validation_provider()` for the priority order between the
two real configs and the always-available heuristic fallback.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.integrations.address_validation.provider import AddressValidationProvider


@dataclass(frozen=True)
class GoogleAddressValidationConfig:
    api_key: str
    api_url: str

    @classmethod
    def from_settings(cls) -> GoogleAddressValidationConfig | None:
        if not settings.GOOGLE_ADDRESS_VALIDATION_API_KEY:
            return None
        return cls(
            api_key=settings.GOOGLE_ADDRESS_VALIDATION_API_KEY,
            api_url=settings.GOOGLE_ADDRESS_VALIDATION_API_URL,
        )


@dataclass(frozen=True)
class AddressValidationConfig:
    api_url: str
    api_key: str | None

    @classmethod
    def from_settings(cls) -> AddressValidationConfig | None:
        if not settings.ADDRESS_VALIDATION_API_URL:
            return None
        return cls(
            api_url=settings.ADDRESS_VALIDATION_API_URL,
            api_key=settings.ADDRESS_VALIDATION_API_KEY,
        )


def stale_after_days() -> int:
    return settings.ADDRESS_VALIDATION_STALE_DAYS


def get_address_validation_provider() -> AddressValidationProvider:
    """The single place provider selection happens, in priority order:

    1. Google Address Validation API (`GOOGLE_ADDRESS_VALIDATION_API_KEY`
       set) -- the real, primary provider in production.
    2. The generic configurable HTTP provider (`ADDRESS_VALIDATION_API_
       URL` set) -- kept for a non-Google vendor account, never used
       once Google is configured.
    3. The heuristic provider -- always available, zero configuration,
       used only when neither real provider above is configured (e.g.
       local development) or reached in production.

    Local imports (not top-of-file) so importing this module never pulls
    in `httpx` unless a provider that actually needs it is selected.
    """
    google_config = GoogleAddressValidationConfig.from_settings()
    if google_config is not None:
        from app.integrations.address_validation.google_provider import (
            GoogleAddressValidationProvider,
        )

        return GoogleAddressValidationProvider(google_config)

    config = AddressValidationConfig.from_settings()
    if config is not None:
        from app.integrations.address_validation.http_provider import HttpAddressValidationProvider

        return HttpAddressValidationProvider(config)

    from app.integrations.address_validation.heuristic_provider import (
        HeuristicAddressValidationProvider,
    )

    return HeuristicAddressValidationProvider()
