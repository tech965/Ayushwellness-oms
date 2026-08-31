"""Registers/re-registers this deployment's Shopify webhook subscriptions.

Idempotent and safe to rerun: lists every `webhookSubscription` already on
the store, then for each topic in
`app.integrations.shopify.webhooks.SUPPORTED_WEBHOOK_TOPICS` —
`orders/create`, `orders/updated`, `orders/cancelled`, `orders/fulfilled`,
`orders/partially_fulfilled`, `refunds/create`, `customers/create`,
`customers/update`, `products/create`, `products/update` — either:

  * does nothing (a subscription for this topic already points at the
    target callback URL),
  * repoints an existing subscription for this topic at the target URL
    (`webhookSubscriptionUpdate` — the store never ends up with two
    subscriptions for the same topic), or
  * creates a new subscription (`webhookSubscriptionCreate`).

Never deletes a subscription for a topic outside `SUPPORTED_WEBHOOK_TOPICS`
— an operator-added subscription this app doesn't know about is left
alone rather than silently removed.

Requires a working Admin API credential (`SHOPIFY_CLIENT_ID`/
`SHOPIFY_CLIENT_SECRET` or `SHOPIFY_ACCESS_TOKEN` — see
`app.integrations.shopify.config.ShopifyConfig`) with the `write_webhooks`
scope, and the target URL via `SHOPIFY_WEBHOOK_CALLBACK_URL` (or the
`--callback-url` flag) — this app's own public
`POST /api/v1/webhooks/shopify` endpoint, e.g.
`https://oms.example.com/api/v1/webhooks/shopify`.

The GraphQL shape below (`endpoint { ... on WebhookHttpEndpoint }`) matches
the documented 2026-01 Admin API schema; re-verify against a live store's
introspection before the first real run against production, per the same
caveat `queries.py` already carries for every other query in this
integration.

Run with: python scripts/register_shopify_webhooks.py [--callback-url URL] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import run_with_cleanup
from app.integrations.shopify.client import ShopifyClient
from app.integrations.shopify.config import ShopifyConfig
from app.integrations.shopify.errors import ShopifyApiError
from app.integrations.shopify.webhooks import SUPPORTED_WEBHOOK_TOPICS

logger = get_logger(__name__)

LIST_SUBSCRIPTIONS_QUERY = """
query ListWebhookSubscriptions($first: Int!, $after: String) {
  webhookSubscriptions(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        topic
        endpoint {
          __typename
          ... on WebhookHttpEndpoint {
            callbackUrl
          }
        }
      }
    }
  }
}
"""

CREATE_SUBSCRIPTION_MUTATION = """
mutation CreateWebhookSubscription(
  $topic: WebhookSubscriptionTopic!
  $webhookSubscription: WebhookSubscriptionInput!
) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
    webhookSubscription { id topic }
    userErrors { field message }
  }
}
"""

UPDATE_SUBSCRIPTION_MUTATION = """
mutation UpdateWebhookSubscription(
  $id: ID!
  $webhookSubscription: WebhookSubscriptionInput!
) {
  webhookSubscriptionUpdate(id: $id, webhookSubscription: $webhookSubscription) {
    webhookSubscription { id topic }
    userErrors { field message }
  }
}
"""


def _topic_enum(topic: str) -> str:
    """`"orders/partially_fulfilled"` -> `"ORDERS_PARTIALLY_FULFILLED"` —
    Shopify's `WebhookSubscriptionTopic` enum values are always the REST
    topic string, uppercased with `/` replaced by `_`.
    """
    return topic.upper().replace("/", "_")


async def _list_existing_subscriptions(client: ShopifyClient) -> dict[str, tuple[str, str | None]]:
    """Returns `{topic_enum: (subscription_id, callback_url)}` for every
    subscription currently on the store, across all pages — a store can
    have more than 50 (this app's own plus any other app's), so a single
    unpaginated page would silently miss some and risk a duplicate create.
    """
    subscriptions: dict[str, tuple[str, str | None]] = {}
    cursor: str | None = None
    while True:
        data = await client.execute(LIST_SUBSCRIPTIONS_QUERY, {"first": 100, "after": cursor})
        connection = data.get("webhookSubscriptions") or {}
        for edge in connection.get("edges") or []:
            node = edge["node"]
            endpoint = node.get("endpoint") or {}
            subscriptions[node["topic"]] = (node["id"], endpoint.get("callbackUrl"))
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
    return subscriptions


async def _register(*, callback_url: str, dry_run: bool) -> None:
    config = ShopifyConfig.from_settings()
    if config is None:
        raise SystemExit(
            "Shopify is not configured — set SHOPIFY_STORE_DOMAIN and either "
            "SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET or SHOPIFY_ACCESS_TOKEN "
            "before registering webhooks."
        )

    client = ShopifyClient(config)
    try:
        existing = await _list_existing_subscriptions(client)
        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        failed: list[tuple[str, str]] = []

        for topic in sorted(SUPPORTED_WEBHOOK_TOPICS):
            topic_enum = _topic_enum(topic)
            current = existing.get(topic_enum)

            if current is not None and current[1] == callback_url:
                unchanged.append(topic)
                continue

            if dry_run:
                (updated if current else created).append(topic)
                continue

            try:
                if current is not None:
                    subscription_id, _ = current
                    result = await client.execute(
                        UPDATE_SUBSCRIPTION_MUTATION,
                        {
                            "id": subscription_id,
                            "webhookSubscription": {"callbackUrl": callback_url, "format": "JSON"},
                        },
                    )
                    errors = result["webhookSubscriptionUpdate"]["userErrors"]
                    bucket = updated
                else:
                    result = await client.execute(
                        CREATE_SUBSCRIPTION_MUTATION,
                        {
                            "topic": topic_enum,
                            "webhookSubscription": {"callbackUrl": callback_url, "format": "JSON"},
                        },
                    )
                    errors = result["webhookSubscriptionCreate"]["userErrors"]
                    bucket = created
            except ShopifyApiError as exc:
                logger.warning(
                    "shopify_webhook_registration_failed", topic=topic, error=exc.message
                )
                failed.append((topic, exc.message))
                continue

            if errors:
                message = "; ".join(f"{e.get('field')}: {e.get('message')}" for e in errors)
                logger.warning("shopify_webhook_registration_failed", topic=topic, error=message)
                failed.append((topic, message))
            else:
                bucket.append(topic)

        print(f"Callback URL: {callback_url}")
        print(f"{'Would create' if dry_run else 'Created'}: {created or 'none'}")
        print(f"{'Would update' if dry_run else 'Updated'}: {updated or 'none'}")
        print(f"Already correct: {unchanged or 'none'}")
        if failed:
            print(f"FAILED: {failed}")
            raise SystemExit(1)
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--callback-url",
        default=settings.SHOPIFY_WEBHOOK_CALLBACK_URL,
        help="Target webhook URL (default: SHOPIFY_WEBHOOK_CALLBACK_URL env var).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without calling any mutation.",
    )
    args = parser.parse_args()

    if not args.callback_url:
        raise SystemExit(
            "No callback URL provided — set SHOPIFY_WEBHOOK_CALLBACK_URL or pass --callback-url."
        )

    asyncio.run(run_with_cleanup(_register(callback_url=args.callback_url, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
