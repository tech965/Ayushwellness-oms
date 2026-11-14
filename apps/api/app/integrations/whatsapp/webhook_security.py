"""WhatsApp/Meta webhook security: GET verification handshake + POST
signature check. The GET handshake (`hub.mode`/`hub.verify_token`/
`hub.challenge`) is Meta's standard Graph API webhook-subscription
pattern used across all Meta webhook products, not something this
session fetched a dedicated WhatsApp-specific page for -- flagged here
so it isn't mistaken for a verified-against-docs fact the way the
message-send/webhook-payload shapes in `client.py`/`webhook_parser.py`
are. The `X-Hub-Signature-256` HMAC check follows the same long-standing
Meta convention (HMAC-SHA256 of the raw request body, keyed by the
app's secret) for the same reason -- verify against Meta's current docs
before relying on this in production if that convention has changed.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_webhook_challenge(
    *, mode: str | None, token: str | None, challenge: str | None, expected_token: str | None
) -> str | None:
    """Returns `challenge` to echo back (200 response body) if the
    handshake is valid, else `None` (caller returns 403).
    """
    if not expected_token:
        return None
    if mode == "subscribe" and token == expected_token and challenge:
        return challenge
    return None


def verify_signature(
    *, raw_body: bytes, signature_header: str | None, app_secret: str | None
) -> bool:
    """`signature_header` is the raw `X-Hub-Signature-256` header value,
    e.g. "sha256=<hex>". Returns False (reject) if the app secret isn't
    configured -- never treats "can't verify" as "verified".
    """
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256=") :]
    return hmac.compare_digest(expected, provided)
