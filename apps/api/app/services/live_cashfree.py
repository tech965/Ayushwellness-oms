"""Live (API-first) Cashfree reads.

Cashfree is the source of truth for payments, transactions, refunds and
settlements. Nothing here writes to PostgreSQL: every figure comes from
the Cashfree API at request time, and bulk views are bounded
(`max_pages`) and report `incomplete` when the bound is hit.

Order <-> Cashfree order id: for the Shopify -> Cashfree checkout flow
the Cashfree `order_id` is the Shopify order number with disallowed
characters (such as the leading `#`) removed -- the same convention
`build_cashfree_order_id` applies. Lookups try that form first, then the
raw order number.

Not supported (no verified Cashfree endpoint in this integration):
per-order refund lookup. Refunds are reported from the bulk
reconciliation feed (`POST /recon`, REFUND rows) by date range.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.exceptions import IntegrationError
from app.integrations.cashfree.client import CashfreeClient
from app.integrations.cashfree.config import CashfreeConfig
from app.integrations.cashfree.errors import CashfreeApiError
from app.integrations.cashfree.normalizer import (
    build_cashfree_order_id,
    extract_recon_rows,
    extract_settlement_rows,
    normalize_payment_status,
    parse_iso_datetime,
)
from app.models.enums import PaymentStatus

MAX_PAGES = 20
MAX_RETRY_SUFFIX = 3
_CONCURRENCY = 5

# Overridable in tests; production builds a real client from settings.
client_factory: Callable[[], Any] | None = None


def _make_client() -> Any:
    if client_factory is not None:
        return client_factory()
    config = CashfreeConfig.from_settings()
    if config is None:
        raise IntegrationError(
            "Cashfree integration is not configured "
            "(missing CASHFREE_CLIENT_ID/CASHFREE_CLIENT_SECRET).",
            details={"error_type": "not_configured"},
        )
    return CashfreeClient(config)


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _method_name(raw: Any) -> str | None:
    return next(iter(raw), None) if isinstance(raw, dict) else None


def _wrap(exc: CashfreeApiError) -> IntegrationError:
    return IntegrationError(exc.message, details={"error_type": exc.error_type})


def candidate_order_ids(order_number: str) -> list[str]:
    ids = [build_cashfree_order_id(order_number), order_number]
    return list(dict.fromkeys(i for i in ids if i))


class LiveCashfreeService:
    async def _lookup(self, client: Any, cashfree_order_id: str) -> dict[str, Any] | None:
        try:
            order = await client.get_order(cashfree_order_id)
        except CashfreeApiError as exc:
            if exc.status_code == 404 or exc.error_type in ("not_found", "order_not_found"):
                return None
            raise _wrap(exc) from exc
        attempts = await client.get_payments_for_order(cashfree_order_id)
        payments = [self._payment(p) for p in attempts if isinstance(p, dict)]
        return {
            "found": True,
            "cashfree_order_id": cashfree_order_id,
            "order_status": order.get("order_status"),
            "order_amount": _decimal(order.get("order_amount")),
            "currency": order.get("order_currency"),
            "payment_session_id": order.get("payment_session_id"),
            "payments": payments,
            "paid": any(p["status"] == PaymentStatus.PAID.value for p in payments),
        }

    async def order_payment(
        self, order_number: str, *, client: Any | None = None
    ) -> dict[str, Any]:
        """Cashfree's current view of one order's payments. `found` is False
        (not an error) when Cashfree has no such order. A checkout retried
        after a failed/expired attempt lives under `<id>-1`, `<id>-2`, ...
        (see `build_cashfree_order_id`): those are probed only when the
        base order is not already paid/active."""
        own_client = client is None
        client = client or _make_client()
        try:
            for base in candidate_order_ids(order_number):
                found = await self._lookup(client, base)
                if found is None:
                    continue
                latest = found
                if not found["paid"] and found["order_status"] != "ACTIVE":
                    for suffix in range(1, MAX_RETRY_SUFFIX + 1):
                        retry = await self._lookup(
                            client, build_cashfree_order_id(order_number, disambiguator=str(suffix))
                        )
                        if retry is None:
                            break
                        latest = retry
                        if retry["paid"] or retry["order_status"] == "ACTIVE":
                            break
                return latest
        finally:
            if own_client:
                await client.aclose()
        return {"found": False, "cashfree_order_id": None, "payments": [], "paid": False}

    async def create_checkout(self, order: Any, *, return_url: str | None) -> dict[str, Any]:
        """Creates (or reuses) a Cashfree checkout for a live order. The
        session id is returned to the caller and not stored anywhere;
        reuse works by asking Cashfree for the order's current state."""
        from app.core.exceptions import ConflictError, ValidationError
        from app.integrations.cashfree.normalizer import CashfreeOrderPushNormalizer
        from app.models.enums import OrderStatus

        if order.payment_status == PaymentStatus.PAID:
            raise ConflictError("Order is already paid.")
        if order.status == OrderStatus.CANCELLED:
            raise ConflictError("Cannot collect payment on a cancelled order.")
        client = _make_client()
        try:
            existing = await self.order_payment(order.order_number, client=client)
            if existing["found"] and existing["paid"]:
                raise ConflictError("Order is already paid in Cashfree.")
            if (
                existing["found"]
                and existing["order_status"] == "ACTIVE"
                and existing.get("payment_session_id")
            ):
                return {**existing, "created": False}

            address = order.shipping_address or {}
            customer = order.customer
            phone = address.get("contact_phone") or (customer.phone if customer else None) or ""
            if not phone:
                raise ValidationError(
                    "Order has no customer phone number on file — Cashfree requires "
                    "customer_details.customer_phone to create a checkout session."
                )
            attempts = 0
            if existing["found"]:
                used = existing["cashfree_order_id"]
                tail = used.rsplit("-", 1)[-1]
                attempts = int(tail) + 1 if "-" in used and tail.isdigit() else 1
            cashfree_order_id = build_cashfree_order_id(
                order.order_number, disambiguator=str(attempts) if attempts else None
            )
            payload = CashfreeOrderPushNormalizer().build_payload(
                order,
                cashfree_order_id=cashfree_order_id,
                customer_phone=phone,
                customer_id=str(customer.id) if customer else f"guest-{order.id}",
                customer_email=customer.email if customer else None,
                customer_name=address.get("contact_name"),
                return_url=return_url,
            )
            response = await client.create_order(payload)
        except CashfreeApiError as exc:
            raise _wrap(exc) from exc
        finally:
            await client.aclose()
        return {
            "found": True,
            "created": True,
            "cashfree_order_id": cashfree_order_id,
            "order_status": response.get("order_status"),
            "payment_session_id": response.get("payment_session_id"),
            "paid": False,
            "payments": [],
        }

    @staticmethod
    def _payment(raw: dict[str, Any]) -> dict[str, Any]:
        status = normalize_payment_status(raw.get("payment_status"))
        return {
            "cf_payment_id": str(raw["cf_payment_id"])
            if raw.get("cf_payment_id") is not None
            else None,
            "status": status.value if status else "unknown",
            "raw_status": raw.get("payment_status"),
            "amount": _decimal(raw.get("payment_amount")),
            "currency": raw.get("payment_currency"),
            "method": _method_name(raw.get("payment_method")),
            "paid_at": parse_iso_datetime(raw.get("payment_time")),
            "bank_reference": raw.get("bank_reference"),
        }

    async def verify(
        self, order_number: str, shopify_payment_status: PaymentStatus
    ) -> dict[str, Any]:
        """Payment verification: Cashfree vs what Shopify says. Read-only --
        reports the mismatch, never auto-corrects either system."""
        detail = await self.order_payment(order_number)
        if not detail["found"]:
            outcome = "not_found_in_cashfree"
        elif (
            detail["paid"]
            and shopify_payment_status == PaymentStatus.PAID
            or not detail["paid"]
            and shopify_payment_status != PaymentStatus.PAID
        ):
            outcome = "match"
        else:
            outcome = "mismatch"
        return {
            "outcome": outcome,
            "shopify_payment_status": shopify_payment_status.value,
            **detail,
        }

    async def order_payments_bulk(
        self, order_numbers: list[str]
    ) -> dict[str, dict[str, Any] | None]:
        """Concurrent (bounded) lookups for a page of orders. A failed
        lookup yields None so the caller shows 'unavailable', never a guess."""
        gate = asyncio.Semaphore(_CONCURRENCY)

        async def one(number: str) -> tuple[str, dict[str, Any] | None]:
            async with gate:
                try:
                    return number, await self.order_payment(number)
                except IntegrationError:
                    return number, None

        return dict(await asyncio.gather(*(one(n) for n in order_numbers)))

    async def _recon(
        self, date_from: datetime, date_to: datetime, max_pages: int
    ) -> tuple[list[dict[str, Any]], bool]:
        client = _make_client()
        start = date_from.strftime("%Y-%m-%dT%H:%M:%SZ")
        end = date_to.strftime("%Y-%m-%dT%H:%M:%SZ")
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        try:
            for _ in range(max_pages):
                page = await client.get_reconciliation(
                    start_date=start, end_date=end, cursor=cursor
                )
                batch, cursor = extract_recon_rows(page)
                rows.extend(batch)
                if cursor is None or not batch:
                    return rows, False
        except CashfreeApiError as exc:
            raise _wrap(exc) from exc
        finally:
            await client.aclose()
        return rows, True

    async def transactions(
        self, date_from: datetime, date_to: datetime, *, max_pages: int = MAX_PAGES
    ) -> dict[str, Any]:
        rows, incomplete = await self._recon(date_from, date_to, max_pages)
        items = [self._recon_item(r) for r in rows if r.get("event_type") in (None, "PAYMENT")]
        return self._bounded({"items": items}, incomplete, max_pages)

    async def refunds(
        self, date_from: datetime, date_to: datetime, *, max_pages: int = MAX_PAGES
    ) -> dict[str, Any]:
        rows, incomplete = await self._recon(date_from, date_to, max_pages)
        items = [self._recon_item(r) for r in rows if r.get("event_type") == "REFUND"]
        return self._bounded({"items": items}, incomplete, max_pages)

    @staticmethod
    def _recon_item(raw: dict[str, Any]) -> dict[str, Any]:
        status = normalize_payment_status(raw.get("status"))
        return {
            "event_type": raw.get("event_type") or "PAYMENT",
            "cashfree_order_id": raw.get("order_id"),
            "cf_payment_id": str(raw["cf_payment_id"])
            if raw.get("cf_payment_id") is not None
            else None,
            "status": status.value if status else (raw.get("status") or "unknown"),
            "amount": _decimal(raw.get("payment_amount") or raw.get("refund_amount")),
            "currency": raw.get("payment_currency"),
            "time": parse_iso_datetime(raw.get("payment_time") or raw.get("event_time")),
        }

    async def settlements(
        self, date_from: datetime, date_to: datetime, *, max_pages: int = MAX_PAGES
    ) -> dict[str, Any]:
        client = _make_client()
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        incomplete = True
        try:
            for _ in range(max_pages):
                page = await client.get_settlements(
                    start_date=date_from.strftime("%Y-%m-%d"),
                    end_date=date_to.strftime("%Y-%m-%d"),
                    cursor=cursor,
                )
                batch, cursor = extract_settlement_rows(page)
                for raw in batch:
                    items.append(
                        {
                            "cf_settlement_id": raw.get("cf_settlement_id"),
                            "status": raw.get("status"),
                            "utr": raw.get("settlement_utr"),
                            "payment_amount": _decimal(raw.get("payment_amount")),
                            "amount_settled": _decimal(raw.get("amount_settled")),
                            "initiated_on": parse_iso_datetime(raw.get("settlement_initiated_on")),
                            "processed_on": parse_iso_datetime(raw.get("settlement_processed_on")),
                        }
                    )
                if cursor is None or not batch:
                    incomplete = False
                    break
        except CashfreeApiError as exc:
            raise _wrap(exc) from exc
        finally:
            await client.aclose()
        unsettled = sum(
            (
                i["payment_amount"] or Decimal("0")
                for i in items
                if i["status"] not in ("SUCCESS", "FAILED")
            ),
            Decimal("0"),
        )
        result = self._bounded(
            {"items": items, "unsettled_amount": unsettled}, incomplete, max_pages
        )
        result["unsettled_amount_is_derived"] = True
        return result

    async def payment_received_summary(
        self, date_from: datetime, date_to: datetime, *, max_pages: int = MAX_PAGES
    ) -> dict[str, Any]:
        rows, incomplete = await self._recon(date_from, date_to, max_pages)
        received = Decimal("0")
        refunded = Decimal("0")
        by_status: dict[str, list] = defaultdict(lambda: [0, Decimal("0")])
        by_day: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for raw in rows:
            item = self._recon_item(raw)
            amount = item["amount"] or Decimal("0")
            if item["event_type"] == "REFUND":
                refunded += amount
                continue
            if item["event_type"] != "PAYMENT":
                continue
            by_status[item["status"]][0] += 1
            by_status[item["status"]][1] += amount
            if item["status"] == PaymentStatus.PAID.value:
                received += amount
                if item["time"]:
                    by_day[item["time"].date().isoformat()] += amount
        return self._bounded(
            {
                "payments_received": received,
                "refunded": refunded,
                "by_status": [
                    {"status": k, "count": v[0], "amount": v[1]} for k, v in by_status.items()
                ],
                "daily_received": [{"date": d, "amount": a} for d, a in sorted(by_day.items())],
            },
            incomplete,
            max_pages,
        )

    @staticmethod
    def _bounded(data: dict[str, Any], incomplete: bool, max_pages: int) -> dict[str, Any]:
        data["complete"] = not incomplete
        data["warnings"] = (
            [
                f"Stopped after {max_pages} Cashfree pages; results are incomplete. Narrow the date range."
            ]
            if incomplete
            else []
        )
        return data
