"""NimbusPost webhook signature verification (New/v2 format only).

Confirmed from NimbusPost's official webhook reference ("Verify the
signature" section, 2026-11-26): `HMAC-SHA256(raw_request_body_bytes,
subscription_secret)`, hex-encoded (lower case), prefixed `sha256=`,
compared against the `x-nimbus-signature` header. The raw body bytes
MUST be hashed exactly as received -- never a parsed-then-re-serialized
JSON string, since key order/spacing/escaping would no longer match what
NimbusPost actually signed. Only the body is signed; headers (including
`x-nimbus-delivery`) are not part of the signature.

The Legacy (v1-shipment-status) format's `X-Hmac-SHA256: <base64>`
scheme is intentionally NOT implemented here -- the confirmed dashboard
configuration for this account is New/v2 (see
`app.api.v1.webhooks.couriers.receive_nimbuspost_webhook`'s docstring).
"""

from __future__ import annotations

import hashlib
import hmac

_SIGNATURE_PREFIX = "sha256="


def verify_webhook_signature(*, raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    """Constant-time verification. Returns False (never raises) when
    `secret` isn't configured or `signature_header` is missing/malformed
    -- an unconfigured secret must never be treated as "skip
    verification," matching every other provider's webhook verifier in
    this codebase (see `app.integrations.shiprocket.webhooks.
    verify_webhook_token`/`app.integrations.shopify.webhooks.
    verify_webhook_hmac`).
    """
    if not secret or not signature_header:
        return False
    if not signature_header.startswith(_SIGNATURE_PREFIX):
        return False
    received_hex = signature_header[len(_SIGNATURE_PREFIX) :]
    expected_hex = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received_hex, expected_hex)
