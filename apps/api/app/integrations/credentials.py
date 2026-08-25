"""Integration credential resolution.

No integration credential is ever stored in the database, in plain text
or otherwise — `Integration.configuration` (see `app.models.integration`)
holds only non-secret metadata (store domain, sync cadence, ...).
Secrets are resolved at call time through a `CredentialProvider`, so a
future production deployment can swap the environment-variable-backed
implementation below for a real secret manager (AWS Secrets Manager,
Vault, ...) without touching any adapter or service code.

Phase 2 adapters (`ShopifyAdapter`, `ShiprocketAdapter`, ...) are the
only expected callers of `get_credential`; nothing in this phase reads a
credential value, since no adapter is registered yet.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


class CredentialProvider(ABC):
    """Resolves a named credential for a given integration code.

    `key` is a short logical name (e.g. "access_token", "api_secret"),
    not an environment variable name — implementations decide how a
    (integration_code, key) pair maps to an actual secret.
    """

    @abstractmethod
    def get_credential(self, integration_code: str, key: str) -> str | None: ...


class EnvCredentialProvider(CredentialProvider):
    """Development/staging default: reads `{INTEGRATION_CODE}_{KEY}` from
    the process environment (the same `SHOPIFY_*`/`SHIPROCKET_*`/
    `BLUE_DART_*` variables already declared, unused, in
    `app.core.config.Settings`).
    """

    def get_credential(self, integration_code: str, key: str) -> str | None:
        env_var = f"{integration_code.upper()}_{key.upper()}"
        return os.environ.get(env_var) or None


def get_credential_provider() -> CredentialProvider:
    """Single seam a production deployment overrides to plug in a real
    secret manager — every adapter resolves credentials through this
    function rather than reading `os.environ` directly.
    """
    return EnvCredentialProvider()
