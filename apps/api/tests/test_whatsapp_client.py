"""WhatsApp Cloud API client -- all traffic mocked (httpx.MockTransport).
Request/response shapes asserted here match what this session verified
against Meta's official docs (see `app.integrations.whatsapp.client`'s
module docstring for the exact pages).
"""

from __future__ import annotations

import httpx
import pytest
from app.integrations.whatsapp.client import WhatsAppClient
from app.integrations.whatsapp.config import WhatsAppConfig
from app.integrations.whatsapp.errors import (
    WhatsAppNotConfiguredError,
    WhatsAppPermissionError,
    WhatsAppRateLimitError,
)

pytestmark = pytest.mark.asyncio

CFG = WhatsAppConfig(
    access_token="tok123",
    phone_number_id="106540352242922",
    business_account_id="waba1",
    api_version="v21.0",
    webhook_verify_token="verify123",
    app_secret="appsecret",
)


class FakeWhatsApp:
    def __init__(self, *, status=200, rate_limited_times=0):
        self.status = status
        self.rate_limited_times = rate_limited_times
        self.calls: list[httpx.Request] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if self.rate_limited_times > 0:
            self.rate_limited_times -= 1
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        if self.status != 200:
            return httpx.Response(
                self.status, json={"error": {"message": "denied", "code": 190}}
            )
        return httpx.Response(
            200,
            json={
                "messaging_product": "whatsapp",
                "contacts": [{"input": "919991234567", "wa_id": "919991234567"}],
                "messages": [{"id": "wamid.ABC123"}],
            },
        )


def make_client(fake: FakeWhatsApp, cfg=CFG) -> WhatsAppClient:
    async def nosleep(_):
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return WhatsAppClient(cfg, http_client=http, sleeper=nosleep)


async def test_not_configured_raises_before_any_request():
    cfg = WhatsAppConfig(
        access_token=None,
        phone_number_id=None,
        business_account_id=None,
        api_version="v21.0",
        webhook_verify_token=None,
        app_secret=None,
    )
    client = make_client(FakeWhatsApp(), cfg)
    with pytest.raises(WhatsAppNotConfiguredError):
        await client.send_text("919991234567", "hi")


async def test_send_text_sends_documented_shape_and_parses_message_id():
    fake = FakeWhatsApp()
    client = make_client(fake)
    resp = await client.send_text("919991234567", "hello there", preview_url=False)
    assert resp["messages"][0]["id"] == "wamid.ABC123"
    body = fake.calls[0].content
    import json

    parsed = json.loads(body)
    assert parsed["messaging_product"] == "whatsapp"
    assert parsed["type"] == "text"
    assert parsed["text"] == {"preview_url": False, "body": "hello there"}
    assert fake.calls[0].headers["Authorization"] == "Bearer tok123"
    assert "/v21.0/106540352242922/messages" in str(fake.calls[0].url)


async def test_send_template_with_body_and_image_header():
    fake = FakeWhatsApp()
    client = make_client(fake)
    await client.send_template(
        "919991234567",
        template_name="daily_report",
        language_code="en_US",
        body_parameters=["24", "₹18,844"],
        header_image_id="MEDIA123",
    )
    import json

    parsed = json.loads(fake.calls[0].content)
    assert parsed["type"] == "template"
    assert parsed["template"]["name"] == "daily_report"
    assert parsed["template"]["language"] == {"code": "en_US"}
    components = parsed["template"]["components"]
    header = next(c for c in components if c["type"] == "header")
    assert header["parameters"][0]["image"] == {"id": "MEDIA123"}
    body = next(c for c in components if c["type"] == "body")
    assert body["parameters"] == [
        {"type": "text", "text": "24"},
        {"type": "text", "text": "₹18,844"},
    ]


async def test_send_image_requires_id_or_link():
    client = make_client(FakeWhatsApp())
    with pytest.raises(ValueError):
        await client.send_image("919991234567")


async def test_send_image_with_link_and_caption():
    fake = FakeWhatsApp()
    client = make_client(fake)
    await client.send_image("919991234567", link="https://example.com/r.png", caption="Report")
    import json

    parsed = json.loads(fake.calls[0].content)
    assert parsed["image"] == {"link": "https://example.com/r.png", "caption": "Report"}


async def test_permission_denied_raises_clear_error():
    client = make_client(FakeWhatsApp(status=403))
    with pytest.raises(WhatsAppPermissionError):
        await client.send_text("919991234567", "hi")


async def test_rate_limit_retries_then_succeeds():
    fake = FakeWhatsApp(rate_limited_times=2)
    client = make_client(fake)
    resp = await client.send_text("919991234567", "hi")
    assert resp["messages"][0]["id"] == "wamid.ABC123"
    assert len(fake.calls) == 3


async def test_rate_limit_exhausts_retries_and_raises():
    fake = FakeWhatsApp(rate_limited_times=99)
    client = make_client(fake)
    with pytest.raises(WhatsAppRateLimitError):
        await client.send_text("919991234567", "hi")
