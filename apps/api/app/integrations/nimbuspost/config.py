"""NimbusPost configuration — reads `NIMBUSPOST_*` settings only, never
hardcoded. `NimbusPostConfig.from_settings()` returns `None` when the
account isn't configured (missing email/password) so the adapter can
report "not configured" instead of attempting a login with empty
credentials — mirrors `ShiprocketConfig.from_settings()`.

NimbusPost's official SDK (github.com/tech-nimbuspost/nimbuspost-api)
exposes two distinct auth schemes: a legacy static `NP-API-KEY` header
against `https://ship.nimbuspost.com/api/`, and a `POST users/login`
(email+password -> bearer token) scheme against
`https://api.nimbuspost.com/v1/`. This account's generated credential is
`NIMBUSPOST_API_KEY` + `NIMBUSPOST_PASSWORD` (no email), which does not
cleanly match either documented scheme on its own -- confirmed with the
business (2026-11-20) that the live flow is email+password -> JWT, so
`NIMBUSPOST_EMAIL` is required too. `NIMBUSPOST_API_KEY` is read and kept
available (never deleted, per instruction) but is NOT used by this login
flow; nothing in this module consumes it yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class NimbusPostConfig:
    email: str
    password: str
    api_base_url: str

    @classmethod
    def from_settings(cls) -> NimbusPostConfig | None:
        if not settings.NIMBUSPOST_EMAIL or not settings.NIMBUSPOST_PASSWORD:
            return None
        return cls(
            email=settings.NIMBUSPOST_EMAIL,
            password=settings.NIMBUSPOST_PASSWORD,
            api_base_url=settings.NIMBUSPOST_API_URL,
        )
