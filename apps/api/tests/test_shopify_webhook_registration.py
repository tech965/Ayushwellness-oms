"""scripts/register_shopify_webhooks.py: idempotent webhook registration.

Uses `httpx.MockTransport` (same style as test_shopify_client.py) so the
create/update/skip decisions are verified without any real Shopify
account or network call.
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.config import ShopifyConfig
from app.integrations.shopify.webhooks import SUPPORTED_WEBHOOK_TOPICS
from scripts import register_shopify_webhooks as registration
from scripts.register_shopify_webhooks import _list_existing_subscriptions, _register, _topic_enum

pytestmark = pytest.mark.asyncio

_CONFIG = ShopifyConfig(
    shop_domain="test-shop.myshopify.com", access_token="tok_123", api_version="2026-01"
)
_CALLBACK_URL = "https://oms.example.com/api/v1/webhooks/shopify"
_WRONG_URL = "https://old-deploy.example.com/api/v1/webhooks/shopify"


def _client_with(handler) -> ShopifyClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return ShopifyClient(_CONFIG, http_client=http_client)


def test_topic_enum_conversion_matches_shopifys_naming_convention() -> None:
    assert _topic_enum("orders/create") == "ORDERS_CREATE"
    assert _topic_enum("orders/partially_fulfilled") == "ORDERS_PARTIALLY_FULFILLED"
    assert _topic_enum("refunds/create") == "REFUNDS_CREATE"


def _subscription_edge(index: int, topic: str, *, callback_url: str) -> dict:
    return {
        "node": {
            "id": f"gid://shopify/WebhookSubscription/{index}",
            "topic": _topic_enum(topic),
            "endpoint": {"__typename": "WebhookHttpEndpoint", "callbackUrl": callback_url},
        }
    }


async def test_list_existing_subscriptions_paginates_and_reads_the_callback_url() -> None:
    page_1 = {
        "data": {
            "webhookSubscriptions": {
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor_1"},
                "edges": [_subscription_edge(1, "orders/create", callback_url=_CALLBACK_URL)],
            }
        }
    }
    page_2 = {
        "data": {
            "webhookSubscriptions": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [_subscription_edge(2, "refunds/create", callback_url=_CALLBACK_URL)],
            }
        }
    }
    pages = iter([page_1, page_2])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(pages))

    client = _client_with(handler)
    existing = await _list_existing_subscriptions(client)

    assert existing == {
        "ORDERS_CREATE": ("gid://shopify/WebhookSubscription/1", _CALLBACK_URL),
        "REFUNDS_CREATE": ("gid://shopify/WebhookSubscription/2", _CALLBACK_URL),
    }


async def test_register_is_a_no_op_when_every_topic_already_points_at_the_target_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The idempotent-rerun guarantee the script's docstring promises:
    every topic already correctly subscribed must trigger zero mutation
    calls.
    """
    existing_edges = [
        _subscription_edge(i, topic, callback_url=_CALLBACK_URL)
        for i, topic in enumerate(sorted(SUPPORTED_WEBHOOK_TOPICS))
    ]
    mutation_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if "ListWebhookSubscriptions" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptions": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "edges": existing_edges,
                        }
                    }
                },
            )
        mutation_calls.append(query)
        return httpx.Response(200, json={"data": {}})

    client = _client_with(handler)
    monkeypatch.setattr(registration, "ShopifyClient", lambda config: client)
    monkeypatch.setattr(ShopifyConfig, "from_settings", classmethod(lambda cls: _CONFIG))

    await _register(callback_url=_CALLBACK_URL, dry_run=False)

    assert mutation_calls == []
    output = capsys.readouterr().out
    assert "none" in output.lower()


async def test_register_creates_missing_and_updates_misdirected_never_duplicating(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    topics = sorted(SUPPORTED_WEBHOOK_TOPICS)
    missing_topic = topics[0]
    misdirected_topic = topics[1]
    already_correct_topics = topics[2:]

    existing_edges = [
        _subscription_edge(1, misdirected_topic, callback_url=_WRONG_URL),
        *(
            _subscription_edge(i + 2, topic, callback_url=_CALLBACK_URL)
            for i, topic in enumerate(already_correct_topics)
        ),
    ]

    create_calls: list[dict] = []
    update_calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query = body["query"]
        if "ListWebhookSubscriptions" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptions": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "edges": existing_edges,
                        }
                    }
                },
            )
        if "CreateWebhookSubscription" in query:
            create_calls.append(body["variables"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptionCreate": {
                            "webhookSubscription": {
                                "id": "gid://new",
                                "topic": body["variables"]["topic"],
                            },
                            "userErrors": [],
                        }
                    }
                },
            )
        if "UpdateWebhookSubscription" in query:
            update_calls.append(body["variables"])
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptionUpdate": {
                            "webhookSubscription": {"id": body["variables"]["id"], "topic": None},
                            "userErrors": [],
                        }
                    }
                },
            )
        raise AssertionError(f"unexpected query: {query}")

    client = _client_with(handler)
    monkeypatch.setattr(registration, "ShopifyClient", lambda config: client)
    monkeypatch.setattr(ShopifyConfig, "from_settings", classmethod(lambda cls: _CONFIG))

    await _register(callback_url=_CALLBACK_URL, dry_run=False)

    # Exactly one create, for the missing topic — never a duplicate.
    assert len(create_calls) == 1
    assert create_calls[0]["topic"] == _topic_enum(missing_topic)

    # Exactly one update, repointing the misdirected subscription — never
    # a second, competing subscription created for the same topic.
    assert len(update_calls) == 1
    assert update_calls[0]["id"] == "gid://shopify/WebhookSubscription/1"
    assert update_calls[0]["webhookSubscription"]["callbackUrl"] == _CALLBACK_URL

    output = capsys.readouterr().out
    assert missing_topic in output
    assert misdirected_topic in output


async def test_register_dry_run_never_calls_a_mutation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    topics = sorted(SUPPORTED_WEBHOOK_TOPICS)
    existing_edges = [_subscription_edge(1, topics[0], callback_url=_WRONG_URL)]
    mutation_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if "ListWebhookSubscriptions" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webhookSubscriptions": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "edges": existing_edges,
                        }
                    }
                },
            )
        mutation_queries.append(query)
        return httpx.Response(200, json={"data": {}})

    client = _client_with(handler)
    monkeypatch.setattr(registration, "ShopifyClient", lambda config: client)
    monkeypatch.setattr(ShopifyConfig, "from_settings", classmethod(lambda cls: _CONFIG))

    await _register(callback_url=_CALLBACK_URL, dry_run=True)

    assert mutation_queries == []
