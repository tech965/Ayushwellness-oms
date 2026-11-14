from __future__ import annotations

import hashlib
import hmac

from app.integrations.whatsapp.webhook_parser import parse_webhook_payload
from app.integrations.whatsapp.webhook_security import verify_signature, verify_webhook_challenge


def test_challenge_verification_succeeds_with_matching_token():
    result = verify_webhook_challenge(
        mode="subscribe", token="secret", challenge="12345", expected_token="secret"
    )
    assert result == "12345"


def test_challenge_verification_fails_with_wrong_token():
    assert (
        verify_webhook_challenge(
            mode="subscribe", token="wrong", challenge="12345", expected_token="secret"
        )
        is None
    )


def test_challenge_verification_fails_when_not_configured():
    assert (
        verify_webhook_challenge(
            mode="subscribe", token="secret", challenge="12345", expected_token=None
        )
        is None
    )


def test_signature_verification_accepts_valid_hmac():
    secret = "appsecret123"
    body = b'{"object":"whatsapp_business_account"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(raw_body=body, signature_header=sig, app_secret=secret) is True


def test_signature_verification_rejects_tampered_body():
    secret = "appsecret123"
    sig = "sha256=" + hmac.new(secret.encode(), b"original", hashlib.sha256).hexdigest()
    assert verify_signature(raw_body=b"tampered", signature_header=sig, app_secret=secret) is False


def test_signature_verification_rejects_when_secret_not_configured():
    assert verify_signature(raw_body=b"x", signature_header="sha256=abc", app_secret=None) is False


def test_signature_verification_rejects_malformed_header():
    assert (
        verify_signature(raw_body=b"x", signature_header="not-sha256-format", app_secret="s")
        is False
    )


def test_parse_status_update():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.ABC",
                                    "status": "delivered",
                                    "recipient_id": "919991234567",
                                    "timestamp": "1234567890",
                                }
                            ]
                        },
                    }
                ]
            }
        ]
    }
    statuses, inbound = parse_webhook_payload(payload)
    assert len(statuses) == 1
    assert statuses[0].message_id == "wamid.ABC"
    assert statuses[0].status == "delivered"
    assert statuses[0].recipient_wa_id == "919991234567"
    assert inbound == []


def test_parse_failed_status_includes_error_message():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.X",
                                    "status": "failed",
                                    "recipient_id": "91999",
                                    "timestamp": "1",
                                    "errors": [{"message": "Template not approved"}],
                                }
                            ]
                        },
                    }
                ]
            }
        ]
    }
    statuses, _ = parse_webhook_payload(payload)
    assert statuses[0].error_message == "Template not approved"


def test_parse_inbound_message_with_contact_name():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [
                                {"profile": {"name": "Sheena Nelson"}, "wa_id": "919991234567"}
                            ],
                            "messages": [
                                {
                                    "from": "919991234567",
                                    "id": "wamid.IN1",
                                    "timestamp": "123",
                                    "type": "text",
                                    "text": {"body": "Hi there"},
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }
    statuses, inbound = parse_webhook_payload(payload)
    assert statuses == []
    assert len(inbound) == 1
    assert inbound[0].from_wa_id == "919991234567"
    assert inbound[0].text_body == "Hi there"
    assert inbound[0].contact_name == "Sheena Nelson"


def test_parse_ignores_non_messages_field():
    payload = {"entry": [{"changes": [{"field": "something_else", "value": {}}]}]}
    statuses, inbound = parse_webhook_payload(payload)
    assert statuses == [] and inbound == []
