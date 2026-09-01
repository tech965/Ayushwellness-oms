"""Shopify webhook endpoint: HMAC verification, idempotent ingestion,
and processing through the same `ENTITY_UPSERT_HANDLERS` dispatch table
`SyncService` uses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from app.core.config import settings
from app.integrations.entity_sync import ENTITY_UPSERT_HANDLERS
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.webhooks import (
    content_length_matches_body,
    verify_webhook_hmac,
    verify_webhook_hmac_with_rotation,
    webhook_hmac_debug_info,
    webhook_secret_fingerprint,
)
from app.models.enums import IntegrationStatus, IntegrationType, OrderStatus, PaymentStatus
from app.models.integration import IntegrationCode, WebhookEvent
from app.models.order import Order
from app.repositories.customer import CustomerRepository
from app.repositories.integration import IntegrationRepository
from app.services.webhook_service import WebhookService
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SECRET = "test-shopify-webhook-secret"


@pytest.fixture(autouse=True)
def _configure_webhook_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET", _SECRET)
    yield
    clear_adapters()


def _sign(body: bytes, secret: str = _SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode(
        "utf-8"
    )


async def _make_shopify_integration(session: AsyncSession):
    integration = await IntegrationRepository(session).create(
        name="Shopify",
        code=IntegrationCode.SHOPIFY,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


# 21. Webhook signature validation
async def test_verify_webhook_hmac_accepts_correct_signature() -> None:
    body = b'{"id": 1}'
    signature = _sign(body)
    assert verify_webhook_hmac(raw_body=body, signature_header=signature, secret=_SECRET) is True


async def test_verify_webhook_hmac_rejects_tampered_body() -> None:
    body = b'{"id": 1}'
    signature = _sign(body)
    tampered = b'{"id": 2}'
    assert (
        verify_webhook_hmac(raw_body=tampered, signature_header=signature, secret=_SECRET) is False
    )


async def test_verify_webhook_hmac_rejects_missing_header_or_secret() -> None:
    body = b'{"id": 1}'
    assert verify_webhook_hmac(raw_body=body, signature_header=None, secret=_SECRET) is False
    assert verify_webhook_hmac(raw_body=body, signature_header=_sign(body), secret="") is False


async def test_verify_webhook_hmac_is_sensitive_to_byte_formatting_shopify_would_never_change() -> (
    None
):
    """Guards against a future regression where the body is parsed and
    re-serialized (e.g. `json.dumps(json.loads(raw_body))`) before hashing
    instead of hashed as the exact bytes received. `json.loads` treats
    these two payloads as equal, but they are different byte sequences —
    a signature computed over one must never verify against the other,
    which is only true if verification genuinely hashes raw bytes end to
    end rather than a value derived from parsing them.
    """
    canonical = json.dumps({"id": 1, "email": "a@example.com"}).encode()
    # Same JSON value, different bytes: extra whitespace + key order +
    # a trailing newline, all of which `json.loads` ignores/normalizes
    # but a byte-for-byte HMAC must not.
    reformatted = b'{\n  "email":  "a@example.com",\n  "id":   1\n}\n'
    assert json.loads(canonical) == json.loads(reformatted)
    assert canonical != reformatted

    signature_for_reformatted = _sign(reformatted)
    assert (
        verify_webhook_hmac(
            raw_body=reformatted, signature_header=signature_for_reformatted, secret=_SECRET
        )
        is True
    )
    # The same signature must NOT verify against the canonical
    # re-serialization of the identical JSON value.
    assert (
        verify_webhook_hmac(
            raw_body=canonical, signature_header=signature_for_reformatted, secret=_SECRET
        )
        is False
    )


# Client-secret rotation: Shopify signs with the oldest unrevoked secret
# during a rotation window, so verification must accept either.
async def test_rotation_accepts_signature_from_current_secret() -> None:
    body = b'{"id": 1}'
    signature = _sign(body, secret=_SECRET)
    assert (
        verify_webhook_hmac_with_rotation(
            raw_body=body, signature_header=signature, secret=_SECRET, old_secret="old-secret-value"
        )
        is True
    )


async def test_rotation_accepts_signature_from_old_secret_when_current_fails() -> None:
    old_secret = "old-secret-value"
    body = b'{"id": 1}'
    signature = _sign(body, secret=old_secret)
    assert (
        verify_webhook_hmac_with_rotation(
            raw_body=body, signature_header=signature, secret=_SECRET, old_secret=old_secret
        )
        is True
    )


async def test_rotation_rejects_when_signature_matches_neither_secret() -> None:
    body = b'{"id": 1}'
    signature = _sign(body, secret="some-unrelated-secret")
    assert (
        verify_webhook_hmac_with_rotation(
            raw_body=body,
            signature_header=signature,
            secret=_SECRET,
            old_secret="old-secret-value",
        )
        is False
    )


async def test_rotation_with_no_old_secret_configured_still_verifies_current() -> None:
    body = b'{"id": 1}'
    signature = _sign(body, secret=_SECRET)
    # `old_secret` omitted entirely (defaults to None) — the common case
    # outside a rotation window, and must behave identically to
    # `verify_webhook_hmac` on its own.
    assert (
        verify_webhook_hmac_with_rotation(raw_body=body, signature_header=signature, secret=_SECRET)
        is True
    )
    # And a wrong signature must still be rejected — no old secret must
    # never mean "skip verification."
    assert (
        verify_webhook_hmac_with_rotation(
            raw_body=body, signature_header="not-a-valid-signature", secret=_SECRET
        )
        is False
    )


async def test_valid_webhook_signature_is_accepted(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 123456, "email": "customer@example.com"}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "customers/update",
            "X-Shopify-Hmac-Sha256": _sign(body),
            "X-Shopify-Webhook-Id": "wh_1",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


async def test_webhook_endpoint_verifies_against_the_true_raw_body_not_a_reparsed_one(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """End-to-end version of
    `test_verify_webhook_hmac_is_sensitive_to_byte_formatting_shopify_would_never_change`
    — proves `receive_shopify_webhook` itself (not just the helper
    function in isolation) hashes `await request.body()`'s exact bytes,
    with no parse/reformat step in between. Uses a body with irregular
    whitespace and non-canonical key order that `json.loads` normalizes
    away but a byte-for-byte HMAC must not — a body-reformatting
    regression would make this 401 even though the JSON *value* is
    unchanged.
    """
    await _make_shopify_integration(db_session)
    body = b'{\n  "email":  "spacing@example.com",\n  "id":   987654\n}\n'

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "customers/update",
            "X-Shopify-Hmac-Sha256": _sign(body),
            "X-Shopify-Webhook-Id": "wh_raw_body_fidelity",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True


async def test_webhook_signed_with_new_secret_is_accepted_during_rotation(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET_OLD", "old-shopify-client-secret")
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 501, "email": "rotation-new@example.com"}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": _sign(body, secret=_SECRET),
            "X-Shopify-Webhook-Id": "wh_rotation_new",
        },
    )
    assert response.status_code == 200


async def test_webhook_signed_with_old_secret_is_accepted_during_rotation(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_secret = "old-shopify-client-secret"
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET_OLD", old_secret)
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 502, "email": "rotation-old@example.com"}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            # Signed with the OLD secret — this is exactly what Shopify
            # sends for the remainder of a rotation window per their
            # documented behavior.
            "X-Shopify-Hmac-Sha256": _sign(body, secret=old_secret),
            "X-Shopify-Webhook-Id": "wh_rotation_old",
        },
    )
    assert response.status_code == 200


async def test_webhook_rejected_when_signature_matches_neither_secret(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET_OLD", "old-shopify-client-secret")
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 503}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": _sign(body, secret="some-completely-unrelated-secret"),
            "X-Shopify-Webhook-Id": "wh_rotation_neither",
        },
    )
    assert response.status_code == 401


async def test_webhook_still_verifies_when_old_secret_is_not_configured(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """No `SHOPIFY_WEBHOOK_SECRET_OLD` set at all (the default, outside a
    rotation window) — behavior must be unchanged from before rotation
    support existed.
    """
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 504, "email": "no-rotation@example.com"}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": _sign(body, secret=_SECRET),
            "X-Shopify-Webhook-Id": "wh_no_rotation",
        },
    )
    assert response.status_code == 200


# Client Credentials Grant migration: SHOPIFY_CLIENT_SECRET is a safe
# fallback webhook-signing secret when SHOPIFY_WEBHOOK_SECRET itself
# isn't configured (for this app, they're the same underlying Shopify
# value) — but an explicitly configured SHOPIFY_WEBHOOK_SECRET must
# always win, never be silently overridden by the client secret.
async def test_webhook_falls_back_to_client_secret_when_webhook_secret_is_not_configured(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET", None)
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_SECRET", "the-app-client-secret")
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 601, "email": "fallback@example.com"}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": _sign(body, secret="the-app-client-secret"),
            "X-Shopify-Webhook-Id": "wh_client_secret_fallback",
        },
    )
    assert response.status_code == 200


async def test_webhook_prefers_explicit_webhook_secret_over_client_secret_fallback(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SHOPIFY_CLIENT_SECRET", "a-different-client-secret")
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 602}).encode()

    # Signed with SHOPIFY_WEBHOOK_SECRET (_SECRET, set by the module
    # fixture) -- must succeed even though a different SHOPIFY_CLIENT_SECRET
    # is also configured.
    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": _sign(body, secret=_SECRET),
            "X-Shopify-Webhook-Id": "wh_prefers_explicit_secret",
        },
    )
    assert response.status_code == 200

    # And a signature made with the (unused, fallback-only) client secret
    # must NOT be accepted while SHOPIFY_WEBHOOK_SECRET is explicitly set.
    body2 = json.dumps({"id": 603}).encode()
    response2 = await client.post(
        "/api/v1/webhooks/shopify",
        content=body2,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": _sign(body2, secret="a-different-client-secret"),
            "X-Shopify-Webhook-Id": "wh_client_secret_not_used_when_explicit",
        },
    )
    assert response2.status_code == 401


# 22. Invalid webhook rejection
async def test_invalid_webhook_signature_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 1}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": "not-a-valid-signature",
        },
    )

    assert response.status_code == 401

    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 0


async def test_trailing_whitespace_on_configured_secret_still_verifies(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the fix for a real production bug: a `SHOPIFY_WEBHOOK_SECRET`
    env var containing an accidental trailing newline/space (a common
    copy-paste artifact in Render's dashboard) must not break verification
    of an otherwise-correct signature — the raw env value is stripped
    before it's used as the HMAC key. Shopify itself signs with the clean
    secret (`_SECRET`), exactly as it would in production.
    """
    monkeypatch.setattr(settings, "SHOPIFY_WEBHOOK_SECRET", f"{_SECRET}\n")
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 42}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": _sign(body, secret=_SECRET),
        },
    )
    assert response.status_code == 200


async def test_webhook_hmac_debug_info_never_exposes_secret_or_digest() -> None:
    body = b'{"id": 1}'
    signature = _sign(body)

    info = webhook_hmac_debug_info(raw_body=body, signature_header=signature, secret=_SECRET)

    assert info == {
        "hmac_header_present": True,
        "hmac_header_length": len(signature),
        "raw_body_length": len(body),
        "webhook_secret_configured": True,
        "webhook_secret_length": len(_SECRET),
        "webhook_secret_fingerprint": webhook_secret_fingerprint(_SECRET),
        "old_webhook_secret_configured": False,
        "old_webhook_secret_length": 0,
        "old_webhook_secret_fingerprint": None,
        "computed_hmac_length": len(signature),
        "hmac_matched_with": "current",
        "hmac_valid": True,
    }
    for value in info.values():
        assert _SECRET not in str(value)
        assert signature not in str(value)


async def test_webhook_hmac_debug_info_reports_missing_header_and_secret() -> None:
    body = b'{"id": 1}'

    missing_header = webhook_hmac_debug_info(raw_body=body, signature_header=None, secret=_SECRET)
    assert missing_header["hmac_header_present"] is False
    assert missing_header["hmac_valid"] is False
    assert missing_header["hmac_matched_with"] is None

    missing_secret = webhook_hmac_debug_info(raw_body=body, signature_header=_sign(body), secret="")
    assert missing_secret["webhook_secret_configured"] is False
    assert missing_secret["computed_hmac_length"] == 0
    assert missing_secret["webhook_secret_fingerprint"] is None
    assert missing_secret["hmac_valid"] is False
    assert missing_secret["hmac_matched_with"] is None


async def test_webhook_hmac_debug_info_reports_match_against_old_secret() -> None:
    old_secret = "previous-shopify-client-secret"
    body = b'{"id": 1}'
    signed_with_old = _sign(body, secret=old_secret)

    info = webhook_hmac_debug_info(
        raw_body=body, signature_header=signed_with_old, secret=_SECRET, old_secret=old_secret
    )
    assert info["hmac_valid"] is True
    assert info["hmac_matched_with"] == "old"
    assert info["old_webhook_secret_configured"] is True
    assert info["old_webhook_secret_length"] == len(old_secret)
    assert info["old_webhook_secret_fingerprint"] == webhook_secret_fingerprint(old_secret)
    for value in info.values():
        assert old_secret not in str(value)
        assert _SECRET not in str(value)


async def test_webhook_secret_fingerprint_is_stable_and_distinguishes_values() -> None:
    # Same input -> same fingerprint every time (so a human can compare
    # today's log line against a value they compute locally tomorrow).
    assert webhook_secret_fingerprint(_SECRET) == webhook_secret_fingerprint(_SECRET)

    # Different secrets -> (overwhelmingly likely) different fingerprints —
    # this is what makes the fingerprint useful for spotting a wrong-app
    # Client Secret mix-up.
    other = "a-completely-different-secret-value"
    assert webhook_secret_fingerprint(_SECRET) != webhook_secret_fingerprint(other)

    # The fingerprint is short and hex — nowhere near enough information
    # to reconstruct a 38-character high-entropy secret, and the secret
    # itself never appears as a substring of it.
    fingerprint = webhook_secret_fingerprint(_SECRET)
    assert fingerprint is not None
    assert len(fingerprint) == 8
    assert all(c in "0123456789abcdef" for c in fingerprint)
    assert _SECRET not in fingerprint

    assert webhook_secret_fingerprint("") is None


async def test_content_length_matches_body_true_when_lengths_agree() -> None:
    assert content_length_matches_body("9", 9) is True


async def test_content_length_matches_body_false_on_mismatch_or_malformed_header() -> None:
    # Proxy/edge truncation or alteration in transit: declared length
    # disagrees with what we actually received.
    assert content_length_matches_body("100", 9) is False
    # Missing header entirely.
    assert content_length_matches_body(None, 9) is False
    # Not a valid integer (should never happen for a real HTTP request,
    # but must not raise or produce a false positive).
    assert content_length_matches_body("not-a-number", 9) is False


async def test_webhook_endpoint_logs_content_length_and_body_hash_diagnostics(
    db_session: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the new transit-integrity diagnostics: on every webhook
    attempt, the endpoint must log whether the declared Content-Length
    matches the bytes actually read, plus a non-reversible hash of those
    exact bytes — the two signals needed to prove or rule out a
    proxy/edge layer altering the body before it reaches HMAC
    verification, a class of bug no in-process test can otherwise catch.
    """
    from app.api.v1.webhooks import shopify as shopify_endpoint

    logged_calls: list[dict] = []
    original_info = shopify_endpoint.logger.info

    def _capture_info(event, **kwargs):  # noqa: ANN001, ANN002
        logged_calls.append({"event": event, **kwargs})
        return original_info(event, **kwargs)

    monkeypatch.setattr(shopify_endpoint.logger, "info", _capture_info)

    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 777, "email": "loggedtest@example.com"}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "customers/update",
            "X-Shopify-Hmac-Sha256": _sign(body),
            "X-Shopify-Webhook-Id": "wh_logged_1",
        },
    )
    assert response.status_code == 200

    hmac_check_calls = [c for c in logged_calls if c["event"] == "shopify_webhook_hmac_check"]
    assert len(hmac_check_calls) == 1
    entry = hmac_check_calls[0]

    assert entry["content_length_matches_body"] is True
    assert entry["content_length_header"] == str(len(body))
    assert entry["raw_body_sha256"] == hashlib.sha256(body).hexdigest()
    # The logged hash must never equal (or trivially reveal) the payload.
    assert b"loggedtest@example.com" not in bytes.fromhex(entry["raw_body_sha256"])


async def test_missing_signature_header_is_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=b'{"id": 1}',
        headers={"X-Shopify-Topic": "orders/create"},
    )
    assert response.status_code == 401


# 24. Duplicate webhook handling
async def test_duplicate_webhook_delivery_creates_only_one_event(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": "gid://shopify/Customer/600", "email": "dup@example.com"}).encode()
    headers = {
        "X-Shopify-Topic": "customers/update",
        "X-Shopify-Hmac-Sha256": _sign(body),
        "X-Shopify-Webhook-Id": "wh_dup",
    }

    first = await client.post("/api/v1/webhooks/shopify", content=body, headers=headers)
    second = await client.post("/api/v1/webhooks/shopify", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200

    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 1


# 23. Webhook processing
async def test_webhook_processing_persists_the_normalized_customer(
    db_session: AsyncSession,
) -> None:
    """Exercises the same pipeline `app.tasks.webhook_processing` runs
    (WebhookService -> adapter.process_webhook -> ENTITY_UPSERT_HANDLERS)
    directly against the test's session, since the Celery task itself
    opens its own production session factory and isn't unit-testable
    against an isolated in-memory database.

    Payload is REST-shaped (snake_case, plain int id) — the actual shape
    a Shopify webhook delivers, not the GraphQL node shape the pull-sync
    adapter fetches. Round 4 found this distinction matters: the old
    version of this test used a hand-crafted GraphQL-shaped payload,
    which is why the REST-shape crash (see `webhook_shapes.py`) went
    undetected until it was checked against a real webhook delivery.
    """
    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)

    payload = {
        "id": 500,
        "first_name": "Sam",
        "last_name": "Reyes",
        "email": "sam@example.com",
        "phone": None,
        "state": "enabled",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "default_address": None,
        "addresses": [],
    }

    webhook_service = WebhookService(db_session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="customers/create",
        payload=payload,
        external_event_id="wh_evt_1",
    )
    assert created is True

    await webhook_service.mark_processing(event.id)
    from app.integrations.registry import get_adapter

    adapter = get_adapter(integration.code)
    result = await adapter.process_webhook(event.event_type, event.payload)
    handler = ENTITY_UPSERT_HANDLERS[result["entity_type"]]
    await handler(db_session, result["normalized"])
    await webhook_service.mark_processed(event.id)

    customer = await CustomerRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="500"
    )
    assert customer is not None
    assert customer.email == "sam@example.com"

    refreshed_event = await webhook_service.get_event(event.id)
    assert refreshed_event.status == "processed"
    assert refreshed_event.processed_at is not None


# Round 4 — full order lifecycle through the real webhook->processing
# pipeline, using REST-shaped payloads (the real Shopify shape) end to
# end: orders/create, orders/updated, orders/cancelled must all resolve
# to the SAME OMS order (same external_id), never create a second one.
async def test_order_webhook_lifecycle_create_update_cancel_touches_exactly_one_order(
    db_session: AsyncSession,
) -> None:
    from app.repositories.order import OrderRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    async def _deliver(topic: str, payload: dict, webhook_id: str) -> None:
        webhook_service = WebhookService(db_session)
        event, created = await webhook_service.ingest(
            integration_id=integration.id,
            event_type=topic,
            payload=payload,
            external_event_id=webhook_id,
        )
        assert created is True
        await webhook_service.mark_processing(event.id)
        result = await adapter.process_webhook(event.event_type, event.payload)
        handler = ENTITY_UPSERT_HANDLERS[result["entity_type"]]
        await handler(db_session, result["normalized"])
        await webhook_service.mark_processed(event.id)

    base_order = {
        "id": 900123,
        "name": "#WEBHOOK-LIFECYCLE-1",
        "created_at": "2026-08-27T10:00:00+05:30",
        "updated_at": "2026-08-27T10:00:00+05:30",
        "cancelled_at": None,
        "currency": "INR",
        "financial_status": "pending",
        "fulfillment_status": None,
        "subtotal_price": "500.00",
        "total_tax": "0.00",
        "total_discounts": "0.00",
        "total_price": "500.00",
        "payment_gateway_names": ["cash on delivery (COD)"],
        "customer": {},
        "line_items": [
            {
                "id": 1,
                "sku": "SKU-1",
                "title": "Item",
                "quantity": 1,
                "price": "500.00",
                "total_discount": "0.00",
                "variant_id": None,
            },
        ],
        "shipping_lines": [],
        "shipping_address": None,
        "billing_address": None,
    }

    # 1. orders/create
    await _deliver("orders/create", base_order, "wh_lifecycle_create")
    orders_repo = OrderRepository(db_session)
    order = await orders_repo.get_by_source_external_id(
        source_system="shopify", external_id="900123"
    )
    assert order is not None
    assert order.total_amount == Decimal("500.00")
    assert order.status != OrderStatus.CANCELLED
    order_pk = order.id

    total_after_create = await db_session.execute(
        select(func.count()).select_from(Order).where(Order.external_id == "900123")
    )
    assert total_after_create.scalar_one() == 1

    # 2. orders/updated — financial status changes, order count must NOT increase
    updated_order = {
        **base_order,
        "financial_status": "paid",
        "updated_at": "2026-08-27T10:05:00+05:30",
    }
    await _deliver("orders/updated", updated_order, "wh_lifecycle_update")

    total_after_update = await db_session.execute(
        select(func.count()).select_from(Order).where(Order.external_id == "900123")
    )
    assert total_after_update.scalar_one() == 1
    order = await orders_repo.get_by_source_external_id(
        source_system="shopify", external_id="900123"
    )
    assert order.id == order_pk  # same row, not a new one
    assert order.payment_status == PaymentStatus.PAID

    # 3. orders/cancelled — must update the SAME order, not create a second one
    cancelled_order = {
        **base_order,
        "cancelled_at": "2026-08-27T10:10:00+05:30",
        "updated_at": "2026-08-27T10:10:00+05:30",
    }
    await _deliver("orders/cancelled", cancelled_order, "wh_lifecycle_cancel")

    total_after_cancel = await db_session.execute(
        select(func.count()).select_from(Order).where(Order.external_id == "900123")
    )
    assert total_after_cancel.scalar_one() == 1
    order = await orders_repo.get_by_source_external_id(
        source_system="shopify", external_id="900123"
    )
    assert order.id == order_pk
    assert order.status == OrderStatus.CANCELLED


async def test_product_webhook_payload_normalizes_without_crashing(
    db_session: AsyncSession,
) -> None:
    """REST-shaped `variants`/`options` (flat, `option1`/`option2`, not
    `selectedOptions`) — the same shape gap that broke orders/customers.
    """
    from app.repositories.product import ProductRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    payload = {
        "id": 700555,
        "title": "Herbal Masala",
        "body_html": "<p>desc</p>",
        "vendor": "Aayush",
        "product_type": "Wellness",
        "tags": "ayurveda, wellness",
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "options": [{"name": "Size", "position": 1, "values": ["60"]}],
        "variants": [
            {
                "id": 8001,
                "sku": "AW-HM-PN-60",
                "title": "60",
                "price": "649.00",
                "compare_at_price": None,
                "inventory_quantity": 10,
                "weight": 0.5,
                "barcode": None,
                "option1": "60",
            },
        ],
    }

    webhook_service = WebhookService(db_session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="products/create",
        payload=payload,
        external_event_id="wh_product_1",
    )
    assert created is True
    result = await adapter.process_webhook(event.event_type, event.payload)
    handler = ENTITY_UPSERT_HANDLERS[result["entity_type"]]
    await handler(db_session, result["normalized"])

    product = await ProductRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="700555"
    )
    assert product is not None
    assert product.title == "Herbal Masala"


async def test_webhook_events_endpoint_never_returns_raw_payload(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    integration = await _make_shopify_integration(db_session)
    await WebhookService(db_session).ingest(
        integration_id=integration.id,
        event_type="orders/create",
        payload={"id": "1", "sensitive": "should-not-leak"},
        external_event_id="wh_evt_2",
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["webhooks.read"]
    ) as authed_client:
        response = await authed_client.get("/api/v1/webhook-events")
        assert response.status_code == 200
        assert "should-not-leak" not in response.text


# --- orders/fulfilled, orders/partially_fulfilled, refunds/create -----------
# These three topics reuse the existing generic order-webhook pipeline
# (orders/fulfilled and orders/partially_fulfilled resolve to entity_type
# "orders" exactly like orders/create/updated/cancelled) plus, for
# refunds/create, the new "refunds" normalizer/upsert handler.

_BASE_ORDER_PAYLOAD = {
    "id": 910000,
    "name": "#TOPIC-TEST",
    "created_at": "2026-08-27T10:00:00+05:30",
    "updated_at": "2026-08-27T10:00:00+05:30",
    "cancelled_at": None,
    "currency": "INR",
    "financial_status": "paid",
    "fulfillment_status": None,
    "subtotal_price": "500.00",
    "total_tax": "0.00",
    "total_discounts": "0.00",
    "total_price": "500.00",
    "payment_gateway_names": ["razorpay"],
    "customer": {},
    "line_items": [],
    "shipping_lines": [],
    "shipping_address": None,
    "billing_address": None,
}


async def _deliver_webhook(
    db_session: AsyncSession,
    adapter: ShopifyAdapter,
    integration_id,
    topic: str,
    payload: dict,
    webhook_id: str,
):
    webhook_service = WebhookService(db_session)
    event, created = await webhook_service.ingest(
        integration_id=integration_id,
        event_type=topic,
        payload=payload,
        external_event_id=webhook_id,
    )
    assert created is True
    result = await adapter.process_webhook(event.event_type, event.payload)
    handler = ENTITY_UPSERT_HANDLERS[result["entity_type"]]
    return await handler(db_session, result["normalized"])


async def test_orders_fulfilled_webhook_updates_fulfillment_status(
    db_session: AsyncSession,
) -> None:
    from app.models.enums import FulfillmentStatus
    from app.repositories.order import OrderRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    payload = {
        **_BASE_ORDER_PAYLOAD,
        "id": 910001,
        "name": "#FULFILLED-1",
        "fulfillment_status": "fulfilled",
    }
    await _deliver_webhook(
        db_session, adapter, integration.id, "orders/fulfilled", payload, "wh_fulfilled_1"
    )

    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="910001"
    )
    assert order is not None
    assert order.fulfillment_status == FulfillmentStatus.FULFILLED


async def test_orders_partially_fulfilled_webhook_updates_fulfillment_status(
    db_session: AsyncSession,
) -> None:
    from app.models.enums import FulfillmentStatus
    from app.repositories.order import OrderRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    payload = {
        **_BASE_ORDER_PAYLOAD,
        "id": 910002,
        "name": "#PARTIAL-1",
        "fulfillment_status": "partial",
    }
    await _deliver_webhook(
        db_session, adapter, integration.id, "orders/partially_fulfilled", payload, "wh_partial_1"
    )

    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="910002"
    )
    assert order is not None
    assert order.fulfillment_status == FulfillmentStatus.PARTIAL


async def test_orders_fulfilled_webhook_returns_200_via_http_endpoint(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 940002, "name": "#F-HTTP-1"}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "orders/fulfilled",
            "X-Shopify-Hmac-Sha256": _sign(body),
            "X-Shopify-Webhook-Id": "wh_fulfilled_http_1",
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True


async def test_refunds_create_webhook_creates_refund_linked_to_order(
    db_session: AsyncSession,
) -> None:
    from app.models.enums import RefundStatus
    from app.repositories.order import OrderRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    order_payload = {**_BASE_ORDER_PAYLOAD, "id": 920001, "name": "#REFUND-ORDER-1"}
    await _deliver_webhook(
        db_session, adapter, integration.id, "orders/create", order_payload, "wh_refund_order_1"
    )

    refund_payload = {
        "id": 55001,
        "order_id": 920001,
        "created_at": "2026-08-27T12:00:00+05:30",
        "processed_at": "2026-08-27T12:00:05+05:30",
        "note": "Customer requested refund",
        "restock": True,
        "transactions": [{"id": 1, "amount": "200.00", "status": "success", "kind": "refund"}],
        "refund_line_items": [],
    }
    refund, refund_created = await _deliver_webhook(
        db_session, adapter, integration.id, "refunds/create", refund_payload, "wh_refund_1"
    )

    assert refund_created is True
    assert refund.amount == Decimal("200.00")
    assert refund.status == RefundStatus.COMPLETED
    assert refund.reason == "Customer requested refund"

    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="920001"
    )
    assert refund.order_id == order.id
    assert refund.payment_id is not None


async def test_refund_upsert_is_idempotent(db_session: AsyncSession) -> None:
    """Redelivering the same refunds/create event (e.g. via the stuck-
    webhook recovery task) must update the same `Refund` row, never create
    a second one — mirrors the resync-does-not-duplicate guarantee every
    other Shopify entity already has (see test_shopify_sync.py).
    """
    from app.models.refund import Refund
    from app.repositories.order import OrderRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    order_payload = {**_BASE_ORDER_PAYLOAD, "id": 920002, "name": "#REFUND-ORDER-2"}
    await _deliver_webhook(
        db_session, adapter, integration.id, "orders/create", order_payload, "wh_refund_order_2"
    )
    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="920002"
    )
    assert order is not None

    refund_payload = {
        "id": 55002,
        "order_id": 920002,
        "created_at": "2026-08-27T12:00:00+05:30",
        "transactions": [{"id": 1, "amount": "100.00", "status": "success"}],
    }
    normalized = adapter.normalize("refunds", refund_payload)

    first, created_first = await ENTITY_UPSERT_HANDLERS["refunds"](db_session, dict(normalized))
    second, created_second = await ENTITY_UPSERT_HANDLERS["refunds"](db_session, dict(normalized))

    assert created_first is True
    assert created_second is False
    assert first.id == second.id

    total = await db_session.execute(
        select(func.count()).select_from(Refund).where(Refund.external_id == "55002")
    )
    assert total.scalar_one() == 1


async def test_refund_for_unknown_order_raises_instead_of_being_dropped(
    db_session: AsyncSession,
) -> None:
    """A refund for an order this OMS hasn't synced yet must not be
    silently ignored — the handler raises `NotFoundError` so the caller
    (SyncService / the webhook-processing task) records/retries it instead
    of fabricating an orphaned refund. Mirrors
    `entity_sync._upsert_shipment`'s unmatched-order behavior.
    """
    from app.core.exceptions import NotFoundError

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    refund_payload = {
        "id": 55099,
        "order_id": 999999,
        "created_at": "2026-08-27T12:00:00+05:30",
        "transactions": [{"id": 1, "amount": "50.00", "status": "success"}],
    }
    webhook_service = WebhookService(db_session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="refunds/create",
        payload=refund_payload,
        external_event_id="wh_refund_orphan",
    )
    assert created is True

    result = await adapter.process_webhook(event.event_type, event.payload)
    with pytest.raises(NotFoundError):
        await ENTITY_UPSERT_HANDLERS[result["entity_type"]](db_session, result["normalized"])


async def test_webhook_processing_task_marks_event_failed_when_handler_raises(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full task-level failure path (`app.tasks.webhook_processing.
    _process_webhook_event`): an unresolvable refund must leave the
    `WebhookEvent` at FAILED with the error recorded and its retry count
    incremented, and the exception must still propagate so Celery's own
    retry mechanism fires — never a silent swallow.
    """
    from app.models.enums import WebhookEventStatus
    from app.tasks import webhook_processing

    class _db_session_cm:
        def __init__(self, session: AsyncSession) -> None:
            self._session = session

        async def __aenter__(self) -> AsyncSession:
            return self._session

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)

    refund_payload = {
        "id": 55098,
        "order_id": 888888,
        "created_at": "2026-08-27T12:00:00+05:30",
        "transactions": [{"id": 1, "amount": "50.00", "status": "success"}],
    }
    webhook_service = WebhookService(db_session)
    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="refunds/create",
        payload=refund_payload,
        external_event_id="wh_refund_task_fail",
    )
    assert created is True
    # Captured before `_process_webhook_event` runs: its `session.rollback()`
    # expires every object in the shared test session, and a plain
    # attribute access on an expired object (e.g. `event.id` below) outside
    # an awaited SQLAlchemy call tries a synchronous lazy-load with no
    # active async bridge — this id is a plain UUID value, unaffected by
    # the later expiry.
    event_id = event.id

    monkeypatch.setattr(webhook_processing, "AsyncSessionLocal", lambda: _db_session_cm(db_session))

    from app.core.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        await webhook_processing._process_webhook_event(str(event_id))

    refreshed = (
        await db_session.execute(select(WebhookEvent).where(WebhookEvent.id == event_id))
    ).scalar_one()
    assert refreshed.status == WebhookEventStatus.FAILED
    assert refreshed.retry_count == 1
    assert refreshed.error_message is not None


async def test_refunds_create_webhook_returns_200_via_http_endpoint(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    await _make_shopify_integration(db_session)
    body = json.dumps({"id": 55100, "order_id": 940001, "transactions": []}).encode()

    response = await client.post(
        "/api/v1/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Topic": "refunds/create",
            "X-Shopify-Hmac-Sha256": _sign(body),
            "X-Shopify-Webhook-Id": "wh_refund_http_1",
        },
    )
    assert response.status_code == 200
    assert response.json()["success"] is True


# --- Out-of-order delivery ---------------------------------------------


async def test_out_of_order_orders_updated_webhook_does_not_overwrite_newer_data(
    db_session: AsyncSession,
) -> None:
    """Shopify (like most webhook providers) guarantees at-least-once
    delivery, not ordering — a delayed retry of an OLDER orders/updated
    delivery arriving AFTER a newer one must not roll back already-applied
    newer data.
    """
    from app.repositories.order import OrderRepository

    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    adapter = ShopifyAdapter(client=None)

    base_order = {
        **_BASE_ORDER_PAYLOAD,
        "id": 930001,
        "name": "#OUT-OF-ORDER-1",
        "financial_status": "pending",
    }
    await _deliver_webhook(
        db_session, adapter, integration.id, "orders/create", base_order, "wh_ooo_create"
    )

    newer = {**base_order, "financial_status": "paid", "updated_at": "2026-08-27T12:00:00+05:30"}
    await _deliver_webhook(
        db_session, adapter, integration.id, "orders/updated", newer, "wh_ooo_newer"
    )

    # A stale retry of an OLDER update, delivered late.
    stale = {**base_order, "financial_status": "pending", "updated_at": "2026-08-27T11:00:00+05:30"}
    await _deliver_webhook(
        db_session, adapter, integration.id, "orders/updated", stale, "wh_ooo_stale"
    )

    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="930001"
    )
    # The stale (older) delivery must NOT have rolled payment_status back.
    assert order.payment_status == PaymentStatus.PAID
