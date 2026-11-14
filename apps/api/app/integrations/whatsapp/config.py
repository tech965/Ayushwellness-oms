"""WhatsApp Cloud API configuration.

Verified against Meta's official Cloud API docs this session
(developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages,
.../reference/messages, .../get-started). `WHATSAPP_ACCESS_TOKEN` must
be a **permanent** System User token -- Meta's own get-started guide:
"[a temporary token] expires quickly and is not suitable for
development purposes. So you should create a permanent token" via
Business Settings > System users, with `whatsapp_business_messaging`
and `whatsapp_business_management` permissions granted. Required
system-user asset access: "Manage app" and "Manage WhatsApp Business
accounts" (Full control), per that same page.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.config import settings as default_settings

GRAPH_HOST = "https://graph.facebook.com"

_REQUIRED = {
    "WHATSAPP_ACCESS_TOKEN": "access_token",
    "WHATSAPP_PHONE_NUMBER_ID": "phone_number_id",
}


@dataclass(frozen=True)
class WhatsAppConfig:
    access_token: str | None
    phone_number_id: str | None
    business_account_id: str | None
    api_version: str
    webhook_verify_token: str | None
    app_secret: str | None

    @classmethod
    def from_settings(cls, s: Settings = default_settings) -> WhatsAppConfig:
        return cls(
            access_token=s.WHATSAPP_ACCESS_TOKEN,
            phone_number_id=s.WHATSAPP_PHONE_NUMBER_ID,
            business_account_id=s.WHATSAPP_BUSINESS_ACCOUNT_ID,
            api_version=s.WHATSAPP_API_VERSION,
            webhook_verify_token=s.WHATSAPP_WEBHOOK_VERIFY_TOKEN,
            app_secret=s.META_APP_SECRET,
        )

    def missing(self) -> list[str]:
        return [env for env, attr in _REQUIRED.items() if not getattr(self, attr)]

    @property
    def configured(self) -> bool:
        return not self.missing()

    @property
    def messages_url(self) -> str:
        return f"{GRAPH_HOST}/{self.api_version}/{self.phone_number_id}/messages"
