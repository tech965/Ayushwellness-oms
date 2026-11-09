"""Transient webhook handling for API-first (live) mode.

The signed payload is verified by the endpoint, handled in memory and
discarded: it is never written to PostgreSQL, Redis, a file, a job
argument or the logs. Only the topic, provider and the opaque delivery id
are logged. Handlers may persist OMS-owned workflow state keyed by the
external order id, never any part of the payload (see
`app.services.live_workflow_handlers`).

Idempotency is persistent (`webhook_deliveries`: provider + delivery id +
topic, no payload). The claim row is inserted in the SAME transaction as
the handler's writes:

  * a duplicate delivery loses on the unique index and is acknowledged
    without re-running the handler (works across restarts and workers;
    concurrent duplicates serialize on the index);
  * a handler failure rolls the claim back with everything else, so the
    provider's retry is processed normally;
  * handlers must still be idempotent and order-independent, because
    providers can deliver events out of order (e.g. `orders/updated`
    before `orders/create`) -- the shipped handlers converge on the same
    `order_workflow` state in any order.

Limits (no event persistence):
  * A delivery that never reaches us (outage longer than the provider's
    retry window) is lost to this path. Events carry no state the live
    API cannot return, so the recovery is simply reading live.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.webhook_delivery import WebhookDelivery

logger = get_logger(__name__)

_RETENTION = timedelta(days=14)
_PRUNE_PROBABILITY = 0.02

# Handlers receive (session, topic, payload) and must not persist `payload`.
LiveEventHandler = Callable[[AsyncSession, str, dict[str, Any]], Awaitable[None]]
_HANDLERS: dict[str, LiveEventHandler] = {}


def register_live_handler(topic: str, handler: LiveEventHandler) -> None:
    _HANDLERS[topic] = handler


def clear_live_handlers() -> None:
    _HANDLERS.clear()


def registered_live_topics() -> list[str]:
    return sorted(_HANDLERS)


async def claim_delivery(
    session: AsyncSession, *, provider: str, delivery_id: str | None, topic: str
) -> bool:
    """Record a delivery id. False if it was already recorded (duplicate).
    A delivery without an id cannot be deduplicated and is always handled
    (handlers are idempotent)."""
    if not delivery_id:
        return True
    try:
        session.add(WebhookDelivery(provider=provider, delivery_id=delivery_id, topic=topic))
        await session.flush()
    except IntegrityError:
        # The claim is the first write of the request, so rolling the
        # whole transaction back loses nothing.
        await session.rollback()
        return False
    if random.random() < _PRUNE_PROBABILITY:  # noqa: S311 - not security sensitive
        await session.execute(
            delete(WebhookDelivery).where(
                WebhookDelivery.received_at < datetime.now(UTC) - _RETENTION
            )
        )
    return True


async def process_live_webhook(
    *,
    session: AsyncSession,
    topic: str,
    delivery_id: str | None,
    payload: dict[str, Any],
    provider: str = "shopify",
    handler: LiveEventHandler | None = None,
) -> bool:
    """Returns False for a duplicate delivery (already handled), else True.
    Commits claim + handler writes atomically; rolls back and re-raises on
    handler failure."""
    try:
        if not await claim_delivery(
            session, provider=provider, delivery_id=delivery_id, topic=topic
        ):
            logger.info("live_webhook_duplicate", provider=provider, topic=topic)
            return False
        handler = handler or _HANDLERS.get(topic)
        if handler is not None:
            await handler(session, topic, payload)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning("live_webhook_failed", provider=provider, topic=topic)
        raise
    logger.info(
        "live_webhook_handled", provider=provider, topic=topic, has_handler=handler is not None
    )
    return True
