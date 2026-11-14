"""Thin WhatsApp Cloud API HTTP client. Request/response shapes verified
against Meta's official docs this session:
https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages
https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages
https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/text-messages
https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/image-messages
https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/overview

No per-second rate limit is documented (only a rolling 24h unique-
recipient "messaging limit" tied to the business portfolio, which this
client can't meaningfully enforce per-call) -- like the Flipkart client,
this backs off on an actual 429 response rather than running a
token-bucket limiter against an invented number.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.integrations.whatsapp.config import WhatsAppConfig
from app.integrations.whatsapp.errors import (
    WhatsAppError,
    WhatsAppNotConfiguredError,
    WhatsAppPermissionError,
    WhatsAppRateLimitError,
)

MAX_RETRIES = 3
MAX_BACKOFF_SECONDS = 15.0


def _msg(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    return str(body)[:300]


class WhatsAppClient:
    def __init__(
        self,
        config: WhatsAppConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._sleep = sleeper

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post_messages(self, body: dict[str, Any]) -> dict:
        if not self._config.configured:
            raise WhatsAppNotConfiguredError(
                "WhatsApp Cloud API credentials are not configured.",
                details={"missing": self._config.missing()},
            )
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self._http.post(
                    self._config.messages_url,
                    json=body,
                    headers={"Authorization": f"Bearer {self._config.access_token}"},
                )
            except httpx.HTTPError as exc:
                raise WhatsAppError(f"WhatsApp request failed: {exc}") from exc
            code = resp.status_code
            if code == 429:
                if attempt >= MAX_RETRIES:
                    raise WhatsAppRateLimitError(
                        "WhatsApp messaging limit hit; please retry shortly."
                    )
                ra = resp.headers.get("Retry-After")
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2.0 * 2**attempt
                await self._sleep(min(delay, MAX_BACKOFF_SECONDS))
                continue
            if code in (401, 403):
                raise WhatsAppPermissionError(
                    "WhatsApp denied access: the token is invalid/expired, or the app is "
                    "missing a required permission. " + _msg(resp)
                )
            if code >= 400:
                raise WhatsAppError(f"WhatsApp API error {code}: {_msg(resp)}")
            return resp.json()
        raise WhatsAppRateLimitError("WhatsApp messaging limit hit.")  # pragma: no cover

    async def send_text(self, to: str, body: str, *, preview_url: bool = False) -> dict:
        """Free-form text -- only valid inside the recipient's 24h
        window; callers must check that themselves (see
        `app.services.whatsapp_send_service.window_is_open`), this
        client does not enforce it.
        """
        return await self._post_messages(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": preview_url, "body": body},
            }
        )

    async def send_template(
        self,
        to: str,
        *,
        template_name: str,
        language_code: str,
        body_parameters: list[str] | None = None,
        header_image_id: str | None = None,
        header_image_link: str | None = None,
    ) -> dict:
        """Approved template message -- the only type Meta allows outside
        the 24h window, and the only type that can carry an image header
        for the daily report (per the spec: "prefer an approved media
        template that sends the generated report image directly at 9
        AM"). Positional `{{1}}`/`{{2}}`... parameters, per Meta's docs.
        """
        components: list[dict[str, Any]] = []
        if header_image_id or header_image_link:
            image: dict[str, Any] = (
                {"id": header_image_id} if header_image_id else {"link": header_image_link}
            )
            components.append({"type": "header", "parameters": [{"type": "image", "image": image}]})
        if body_parameters:
            components.append(
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in body_parameters],
                }
            )
        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }
        if components:
            template["components"] = components
        return await self._post_messages(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": template,
            }
        )

    async def send_image(
        self,
        to: str,
        *,
        media_id: str | None = None,
        link: str | None = None,
        caption: str | None = None,
    ) -> dict:
        """Free-form image -- only valid inside the 24h window, same
        caveat as `send_text`. Meta: "For better performance, we
        recommend using `id`" over `link`.
        """
        if not media_id and not link:
            raise ValueError("send_image requires media_id or link")
        image: dict[str, Any] = {"id": media_id} if media_id else {"link": link}
        if caption:
            image["caption"] = caption
        return await self._post_messages(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "image",
                "image": image,
            }
        )
