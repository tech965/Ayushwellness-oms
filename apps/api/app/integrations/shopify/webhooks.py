"""Shopify webhook signature verification (mandatory — spec §17).

HMAC-SHA256 over the *raw* request body, base64-encoded, compared with
the `X-Shopify-Hmac-Sha256` header using a constant-time comparison.
Never trust a webhook because it reached the right URL — verify first.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

# Length of the fingerprint below, in hex characters (32 bits) — enough
# to compare two *specific* known values against each other with
# negligible accidental-collision risk, while being extra conservative
# about not exposing anything resembling the real secret.
_FINGERPRINT_HEX_LENGTH = 8

# entity_type ("customers"/"products"/"orders"/"refunds") is
# `topic.split("/", 1)[0]` — see
# app.integrations.shopify.adapter.ShopifyAdapter.process_webhook. This is
# the single source of truth for which topics this app registers with
# Shopify (see scripts/register_shopify_webhooks.py) — the receiving
# endpoint itself stays topic-agnostic (any topic whose entity_type has a
# registered normalizer is processed; anything else is marked IGNORED,
# never a hard failure, so an unexpected/legacy subscription can never
# break the webhook ack), but registration is always driven by this
# explicit list, never a vague "everything fulfillment-related" wildcard.
SUPPORTED_WEBHOOK_TOPICS: frozenset[str] = frozenset(
    {
        "orders/create",
        "orders/updated",
        "orders/cancelled",
        "orders/fulfilled",
        "orders/partially_fulfilled",
        "refunds/create",
        "customers/create",
        "customers/update",
        "products/create",
        "products/update",
    }
)


def verify_webhook_hmac(*, raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    """Returns False (never raises) for any missing/malformed input — a
    caller that only acts on `True` can't be tricked by an exception path.
    """
    if not signature_header or not secret:
        return False

    computed = base64.b64encode(
        hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    ).decode("utf-8")
    return hmac.compare_digest(computed, signature_header)


def verify_webhook_hmac_with_rotation(
    *,
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
    old_secret: str | None = None,
) -> bool:
    """Same guarantee as `verify_webhook_hmac` — tried against the current
    secret first and, only if that fails and only when a value is
    actually configured, against `old_secret` too.

    Exists for the temporary window during a Shopify Client Secret
    rotation: Shopify's own documentation states it signs webhooks with
    the OLDEST unrevoked secret until that old one is explicitly revoked,
    so a deployment must accept both during that window. Never weakens
    verification — a webhook that matches neither secret (or arrives
    unsigned) is still rejected exactly as before; there is no code path
    here that accepts a webhook without a valid signature against at
    least one configured secret.
    """
    if verify_webhook_hmac(raw_body=raw_body, signature_header=signature_header, secret=secret):
        return True
    return bool(old_secret) and verify_webhook_hmac(
        raw_body=raw_body, signature_header=signature_header, secret=old_secret or ""
    )


def content_length_matches_body(content_length_header: str | None, raw_body_length: int) -> bool:
    """True only when the `Content-Length` Shopify's request declared
    matches the number of bytes we actually read for that request.

    A mismatch is direct, unambiguous proof that something between
    Shopify and this endpoint (a proxy, an edge/CDN layer, ...) altered
    the body before it reached `request.body()` — a category of bug that
    no in-process test (this app's included — every existing test drives
    the FastAPI app directly via `ASGITransport`, never over a real
    network hop) can ever exercise or catch on its own.
    """
    return (
        content_length_header is not None
        and content_length_header.isdigit()
        and int(content_length_header) == raw_body_length
    )


def webhook_secret_fingerprint(secret: str) -> str | None:
    """A short, non-reversible fingerprint of a secret value — SHA-256,
    truncated to `_FINGERPRINT_HEX_LENGTH` hex characters. Cannot be used
    to recover the secret (preimage-resistant hash, further shortened),
    but is stable and comparable: computing this same function locally
    over the value shown in Shopify's own dashboard lets a human confirm,
    without ever typing the real secret into a chat, a log, or any shared
    channel, whether the value configured here is byte-for-byte the same
    one Shopify is signing with.
    """
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:_FINGERPRINT_HEX_LENGTH]


def webhook_hmac_debug_info(
    *,
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
    old_secret: str | None = None,
) -> dict[str, int | bool | str | None]:
    """Safe-to-log snapshot of a verification attempt — lengths, booleans,
    and non-reversible fingerprints only, NEVER a secret, the header, or
    the computed digest itself. `verify_webhook_hmac`/
    `verify_webhook_hmac_with_rotation` alone give no visibility into
    *why* a webhook was rejected, or *which* configured secret (current
    vs. old, during a rotation window) actually matched; this exists so a
    production log line can distinguish those cases without ever risking
    a credential or signature value leaking into structured logs.

    `hmac_matched_with` is `"current"`, `"old"`, or `None` — watching this
    settle to `"current"` on every request is exactly the signal that
    tells you it's safe to remove `SHOPIFY_WEBHOOK_SECRET_OLD` once a
    rotation is complete.
    """
    computed_length = 0
    if secret:
        computed = base64.b64encode(
            hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
        ).decode("utf-8")
        computed_length = len(computed)

    matched_with: str | None = None
    if verify_webhook_hmac(raw_body=raw_body, signature_header=signature_header, secret=secret):
        matched_with = "current"
    elif old_secret and verify_webhook_hmac(
        raw_body=raw_body, signature_header=signature_header, secret=old_secret
    ):
        matched_with = "old"

    return {
        "hmac_header_present": signature_header is not None,
        "hmac_header_length": len(signature_header) if signature_header else 0,
        "raw_body_length": len(raw_body),
        "webhook_secret_configured": bool(secret),
        "webhook_secret_length": len(secret),
        "webhook_secret_fingerprint": webhook_secret_fingerprint(secret),
        "old_webhook_secret_configured": bool(old_secret),
        "old_webhook_secret_length": len(old_secret) if old_secret else 0,
        "old_webhook_secret_fingerprint": (
            webhook_secret_fingerprint(old_secret) if old_secret else None
        ),
        "computed_hmac_length": computed_length,
        "hmac_matched_with": matched_with,
        "hmac_valid": matched_with is not None,
    }
