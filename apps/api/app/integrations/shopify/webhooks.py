"""Shopify webhook signature verification (mandatory — spec §17).

HMAC-SHA256 over the *raw* request body, base64-encoded, compared with
the `X-Shopify-Hmac-Sha256` header using a constant-time comparison.
Never trust a webhook because it reached the right URL — verify first.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

# entity_type ("customers"/"products"/"orders") is `topic.split("/", 1)[0]`
# — see app.integrations.shopify.adapter.ShopifyAdapter.process_webhook.
SUPPORTED_WEBHOOK_TOPICS: frozenset[str] = frozenset(
    {
        "orders/create",
        "orders/updated",
        "orders/cancelled",
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
