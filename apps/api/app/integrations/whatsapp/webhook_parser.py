"""Parses WhatsApp Cloud API webhook payloads. Field names verified
against Meta's official docs this session:
https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages
(status callbacks: `entry[].changes[].value.statuses[]`) and
https://developers.facebook.com/docs/whatsapp/cloud-api/get-started
(inbound messages: `entry[].changes[].value.messages[]`/`.contacts[]`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatusUpdate:
    message_id: str
    status: str  # sent | delivered | read | failed | played
    recipient_wa_id: str
    timestamp: str
    error_message: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    from_wa_id: str
    message_id: str
    timestamp: str
    message_type: str
    text_body: str | None
    contact_name: str | None


def parse_webhook_payload(payload: dict) -> tuple[list[StatusUpdate], list[InboundMessage]]:
    """A single webhook delivery can carry either `statuses` or
    `messages` per `value` (never both, per Meta's docs), across
    possibly multiple `entry`/`changes` -- collects everything found.
    """
    statuses: list[StatusUpdate] = []
    inbound: list[InboundMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            contacts_by_wa_id = {
                c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])
            }
            for s in value.get("statuses", []):
                errors = s.get("errors") or []
                statuses.append(
                    StatusUpdate(
                        message_id=s.get("id", ""),
                        status=s.get("status", ""),
                        recipient_wa_id=s.get("recipient_id", ""),
                        timestamp=s.get("timestamp", ""),
                        error_message=errors[0].get("message") if errors else None,
                    )
                )
            for m in value.get("messages", []):
                from_id = m.get("from", "")
                inbound.append(
                    InboundMessage(
                        from_wa_id=from_id,
                        message_id=m.get("id", ""),
                        timestamp=m.get("timestamp", ""),
                        message_type=m.get("type", "unknown"),
                        text_body=(m.get("text") or {}).get("body"),
                        contact_name=contacts_by_wa_id.get(from_id),
                    )
                )
    return statuses, inbound
