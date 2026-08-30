"""Shiprocket webhook secret verification.

UNVERIFIED transport (see docs/integrations/shiprocket.md's Webhooks
section): Shiprocket's dashboard (Settings > API > Webhook) has a single
"Webhook Secret" field alongside the callback URL, with no separate
HMAC-signing key documented anywhere this integration's research could
confirm — unlike Shopify, which has a published, confirmed
`X-Shopify-Hmac-Sha256` scheme (see `app.integrations.shopify.webhooks`).
Community integration guides most consistently describe that shared
secret arriving either as an `X-Api-Key` request header, or as a `token`/
`secret` field inside the JSON body itself (the convention Shiprocket's
other webhook products — e.g. Return, Abandoned Cart — are documented to
use for the same dashboard-configured secret). This checks both
locations against the one configured value; whichever the real account
turns out to use, verification still succeeds. MUST be reconfirmed
against a real webhook delivery before this is relied on in production.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

_FINGERPRINT_HEX_LENGTH = 8

# UNVERIFIED — see module docstring. Checked in this order against
# `payload`; the first present value wins.
_BODY_TOKEN_KEYS = ("token", "secret", "webhook_secret", "webhook_token")


def extract_body_token(payload: dict[str, Any]) -> str | None:
    for key in _BODY_TOKEN_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def verify_webhook_token(*, header_token: str | None, body_token: str | None, secret: str) -> bool:
    """Constant-time compare against `secret`, tried against whichever of
    `header_token`/`body_token` is present. Returns False (never raises)
    when `secret` isn't configured — an unconfigured secret must never be
    treated as "skip verification," exactly like
    `app.integrations.shopify.webhooks.verify_webhook_hmac`.
    """
    if not secret:
        return False
    for candidate in (header_token, body_token):
        if candidate and hmac.compare_digest(candidate, secret):
            return True
    return False


def webhook_secret_fingerprint(secret: str) -> str | None:
    """Short, non-reversible fingerprint — same purpose and shape as
    `app.integrations.shopify.webhooks.webhook_secret_fingerprint`: lets a
    human confirm the value configured here matches Shiprocket's
    dashboard without ever typing the real secret into a log or chat.
    """
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_LENGTH]
