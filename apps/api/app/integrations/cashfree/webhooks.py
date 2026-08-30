"""Cashfree webhook signature verification (mandatory).

Cashfree signs `x-webhook-timestamp + RAW_REQUEST_BODY` with HMAC-SHA256,
base64-encoded, compared against the `x-webhook-signature` header using a
constant-time comparison — confirmed via Cashfree's official webhook
security documentation (see docs/integrations/cashfree.md for sources).
The signing key is the Payments API client secret
(`CASHFREE_CLIENT_SECRET`) — confirmed consistently across Cashfree's own
docs and SDK examples; no separate webhook-specific key is issued for
most accounts. `CASHFREE_WEBHOOK_SECRET`, if explicitly configured, takes
precedence (for the small number of accounts that do have a distinct
one) — see `resolve_webhook_secret`.

CRITICAL: verification MUST run against the exact raw bytes Cashfree
sent — never a value reconstructed from a parsed-then-reserialized JSON
object (re-serializing can silently change decimal formatting/whitespace
and would make every signature check fail, or worse, could theoretically
be made to pass against a *different* byte sequence than what Cashfree
actually signed). The caller is responsible for reading `request.body()`
before ever calling `request.json()`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

_FINGERPRINT_HEX_LENGTH = 8


def resolve_webhook_secret(*, client_secret: str | None, webhook_secret: str | None) -> str:
    """`webhook_secret` (CASHFREE_WEBHOOK_SECRET) wins when explicitly
    configured; otherwise falls back to `client_secret`
    (CASHFREE_CLIENT_SECRET) — Cashfree's actual documented signing key.
    Returns `""` (never `None`) when neither is configured, so callers
    can pass the result straight to `verify_webhook_signature` and get a
    clean "always reject" rather than a `None`-handling branch.
    """
    return (webhook_secret or client_secret or "").strip()


def compute_webhook_signature(*, timestamp: str, raw_body: bytes, secret: str) -> str:
    signed_payload = timestamp.encode("utf-8") + raw_body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_webhook_signature(
    *, raw_body: bytes, timestamp: str | None, signature: str | None, secret: str
) -> bool:
    """Returns `False` (never raises) for any missing/malformed input —
    a caller that only acts on `True` can't be tricked by an exception
    path. `secret` must already be resolved via `resolve_webhook_secret`.
    """
    if not timestamp or not signature or not secret:
        return False
    computed = compute_webhook_signature(timestamp=timestamp, raw_body=raw_body, secret=secret)
    return hmac.compare_digest(computed, signature)


def webhook_secret_fingerprint(secret: str) -> str | None:
    """Short, non-reversible fingerprint — same purpose/shape as
    `app.integrations.shopify.webhooks.webhook_secret_fingerprint`.
    """
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_LENGTH]
