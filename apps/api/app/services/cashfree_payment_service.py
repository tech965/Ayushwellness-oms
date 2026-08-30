"""OMS -> Cashfree checkout orchestration, and Cashfree -> OMS payment
state updates (webhook + reconciliation).

Explicitly Cashfree-aware — the same pattern
`app.services.shiprocket_service.ShiprocketOperationsService` already
uses for Shiprocket — but every OMS write goes through existing
repositories/services, never a raw session mutation. `apply_payment_event`
is the ONE place a Cashfree payment result is ever applied to
`Payment`/`Order` state, shared by the webhook endpoint
(`app.api.v1.webhooks.cashfree`, after signature verification) and
`reconcile_payment` (an authenticated, direct Cashfree API lookup) — the
frontend/browser is never a caller and never a trusted source (spec:
never mark an order paid from anything but a verified Cashfree result).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, IntegrationError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.cashfree.client import CashfreeClient
from app.integrations.cashfree.config import CashfreeConfig
from app.integrations.cashfree.errors import CashfreeApiError
from app.integrations.cashfree.normalizer import (
    ORDER_PUSH_NORMALIZER,
    build_cashfree_order_id,
    normalize_payment_status,
    parse_iso_datetime,
)
from app.models.auth import User
from app.models.enums import OrderStatus, PaymentStatus
from app.models.mixins import SourceSystem
from app.models.order import Order
from app.models.payment import Payment
from app.repositories.order import OrderEventRepository, OrderRepository
from app.repositories.payment import PaymentRepository, PaymentTransactionRepository
from app.services.audit_service import AuditService
from app.services.order_service import ORDER_STATUS_TRANSITIONS

logger = get_logger(__name__)


@dataclass(frozen=True)
class PaymentEventResult:
    """Outcome of applying one Cashfree payment result. `applied=False`
    is an ordinary, expected outcome (unknown order, amount mismatch,
    already-paid, duplicate) — never raised as an exception; the caller
    (webhook endpoint / reconcile endpoint) decides how to log/respond.
    """

    applied: bool
    reason: str | None
    payment_id: uuid.UUID | None
    mapped_status: PaymentStatus | None = None


class CashfreePaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.order_events = OrderEventRepository(session)
        self.payments = PaymentRepository(session)
        self.payment_transactions = PaymentTransactionRepository(session)
        self.audit = AuditService(session)

    def _require_config(self) -> CashfreeConfig:
        config = CashfreeConfig.from_settings()
        if config is None:
            raise IntegrationError(
                "Cashfree integration is not configured "
                "(missing CASHFREE_CLIENT_ID/CASHFREE_CLIENT_SECRET).",
                details={"error_type": "not_configured"},
            )
        return config

    async def _get_order(self, order_id: uuid.UUID) -> Order:
        order = await self.orders.get_by_id_with_items_and_customer(order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        return order

    # --- Checkout creation (OMS -> Cashfree) --------------------------

    async def create_or_reuse_checkout(
        self, order_id: uuid.UUID, *, actor: User | None
    ) -> tuple[Payment, bool]:
        """Returns `(payment, created)`. `created=False` means an already
        -active Cashfree session for this order was reused — no Cashfree
        API call is made in that case (spec: never create a new Cashfree
        order on every page refresh).
        """
        order = await self._get_order(order_id)
        if order.payment_status == PaymentStatus.PAID:
            raise ConflictError("Order is already paid.")
        if order.status == OrderStatus.CANCELLED:
            raise ConflictError("Cannot collect payment on a cancelled order.")

        existing_attempts = sorted(
            (p for p in await self.payments.list_for_order(order_id) if p.provider == "cashfree"),
            key=lambda p: p.created_at,
        )
        latest = existing_attempts[-1] if existing_attempts else None

        if latest is not None and latest.status == PaymentStatus.PENDING:
            metadata = latest.payment_metadata or {}
            if metadata.get("payment_session_id"):
                logger.info(
                    "cashfree_checkout_reused", order_id=str(order_id), payment_id=str(latest.id)
                )
                return latest, False

        config = self._require_config()
        customer = order.customer
        address = order.shipping_address or {}
        # Same fallback chain `app.integrations.shiprocket.normalizer.
        # ShiprocketOrderPushNormalizer` already uses for a billing phone
        # — a shipping-address contact phone first, the customer's own
        # phone otherwise.
        customer_phone = (
            address.get("contact_phone") or (customer.phone if customer else None) or ""
        )
        if not customer_phone:
            raise ValidationError(
                "Order has no customer phone number on file — Cashfree requires "
                "customer_details.customer_phone to create a checkout session."
            )
        customer_id = str(customer.id) if customer else f"guest-{order.id}"
        customer_email = customer.email if customer else None
        customer_name = (
            address.get("contact_name")
            or (
                f"{customer.first_name or ''} {customer.last_name or ''}".strip()
                if customer
                else None
            )
            or None
        )

        # A prior attempt for this order that's no longer reusable
        # (FAILED, dropped, or a Cashfree order that's since expired) gets
        # a disambiguated order_id for the retry — Cashfree's order_id
        # identifies one order for its whole lifetime, so the SAME id can
        # never be reused for a second, distinct checkout attempt.
        disambiguator = str(len(existing_attempts)) if existing_attempts else None
        cashfree_order_id = build_cashfree_order_id(
            order.order_number, disambiguator=disambiguator
        )
        return_url = (
            config.return_url.replace("{order_id}", str(order.id)) if config.return_url else None
        )

        payload = ORDER_PUSH_NORMALIZER.build_payload(
            order,
            cashfree_order_id=cashfree_order_id,
            customer_phone=customer_phone,
            customer_id=customer_id,
            customer_email=customer_email,
            customer_name=customer_name,
            return_url=return_url,
        )

        client = CashfreeClient(config)
        try:
            response = await client.create_order(payload)
        except CashfreeApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc
        finally:
            await client.aclose()

        payment, created = await self.payments.upsert_by_external_id(
            source_system=SourceSystem.CASHFREE,
            external_id=cashfree_order_id,
            order_id=order.id,
            payment_type=order.payment_type,
            status=PaymentStatus.PENDING,
            amount=order.total_amount,
            currency=order.currency,
            provider="cashfree",
            payment_metadata={
                "cashfree_order_id": cashfree_order_id,
                "cf_order_id": response.get("cf_order_id"),
                "payment_session_id": response.get("payment_session_id"),
                "order_status": response.get("order_status"),
            },
            raw_external_payload=response,
        )
        await self.session.commit()

        await self.audit.record(
            user=actor,
            action="payment.cashfree_checkout_created",
            entity_type="payment",
            entity_id=str(payment.id),
            new_value={"order_id": str(order.id), "cashfree_order_id": cashfree_order_id},
        )
        await self.session.commit()

        logger.info(
            "cashfree_checkout_created",
            order_id=str(order_id),
            payment_id=str(payment.id),
            cashfree_order_id=cashfree_order_id,
            created=created,
        )
        return payment, created

    async def get_payment_for_order(self, order_id: uuid.UUID) -> Payment:
        await self._get_order(order_id)  # 404s cleanly for an unknown order
        attempts = sorted(
            (p for p in await self.payments.list_for_order(order_id) if p.provider == "cashfree"),
            key=lambda p: p.created_at,
        )
        if not attempts:
            raise NotFoundError("No Cashfree payment found for this order.")
        return attempts[-1]

    # --- Applying a trusted Cashfree result (webhook / reconciliation) -

    async def apply_payment_event(
        self,
        *,
        cashfree_order_id: str,
        cf_payment_id: str | None,
        raw_status: str | None,
        amount: Decimal | None,
        currency: str | None,
        payment_method_name: str | None,
        paid_at: datetime | None,
        raw_payload: dict[str, Any],
    ) -> PaymentEventResult:
        """`raw_payload` is stored on the new `PaymentTransaction` row for
        reconciliation/debugging — caller must have already stripped any
        customer PII (see `app.api.v1.webhooks.cashfree`).
        """
        payment = await self.payments.get_by_source_external_id(
            source_system=SourceSystem.CASHFREE, external_id=cashfree_order_id
        )
        if payment is None:
            logger.warning(
                "cashfree_payment_event_unmatched", cashfree_order_id=cashfree_order_id
            )
            return PaymentEventResult(
                applied=False, reason="unknown_cashfree_order", payment_id=None
            )

        mapped_status = normalize_payment_status(raw_status)
        if mapped_status is None:
            logger.warning(
                "cashfree_payment_event_unrecognized_status",
                payment_id=str(payment.id),
                raw_status=raw_status,
            )
            return PaymentEventResult(
                applied=False,
                reason=f"unrecognized_payment_status:{raw_status}",
                payment_id=payment.id,
            )

        # Amount/currency validation (spec §9) — only meaningful for a
        # SUCCESS result; a FAILED/dropped event has no money to verify.
        # Never marked paid on a mismatch, regardless of what Cashfree's
        # `payment_status` claimed.
        if mapped_status == PaymentStatus.PAID:
            if amount is None or amount != payment.amount:
                logger.warning(
                    "cashfree_amount_mismatch",
                    payment_id=str(payment.id),
                    expected_amount=str(payment.amount),
                    received_amount=str(amount) if amount is not None else None,
                )
                return PaymentEventResult(
                    applied=False,
                    reason=f"amount_mismatch:expected={payment.amount}:received={amount}",
                    payment_id=payment.id,
                )
            if currency and currency.upper() != payment.currency.upper():
                logger.warning(
                    "cashfree_currency_mismatch",
                    payment_id=str(payment.id),
                    expected_currency=payment.currency,
                    received_currency=currency,
                )
                return PaymentEventResult(
                    applied=False,
                    reason=f"currency_mismatch:expected={payment.currency}:received={currency}",
                    payment_id=payment.id,
                )

        # PAID is terminal/sticky: a later non-PAID event (a stale retry,
        # an out-of-order delivery, or a second attempt's failure after
        # an earlier attempt already succeeded) must never downgrade an
        # already-paid payment (spec: "do not allow FAILED -> SUCCESS
        # unless trusted" applies symmetrically — an already-trusted
        # SUCCESS is never undone by a later, less-authoritative signal).
        already_paid = payment.status == PaymentStatus.PAID
        if already_paid and mapped_status != PaymentStatus.PAID:
            await self._record_transaction(
                payment,
                cf_payment_id=cf_payment_id,
                status=mapped_status,
                amount=amount if amount is not None else payment.amount,
                raw_payload=raw_payload,
            )
            logger.info(
                "cashfree_event_ignored_after_paid",
                payment_id=str(payment.id),
                raw_status=raw_status,
            )
            return PaymentEventResult(
                applied=False,
                reason="payment_already_paid",
                payment_id=payment.id,
                mapped_status=mapped_status,
            )

        transaction, txn_created = await self._record_transaction(
            payment,
            cf_payment_id=cf_payment_id,
            status=mapped_status,
            amount=amount if amount is not None else payment.amount,
            raw_payload=raw_payload,
        )
        if not txn_created:
            logger.info(
                "cashfree_event_duplicate_transaction",
                payment_id=str(payment.id),
                cf_payment_id=cf_payment_id,
            )
            return PaymentEventResult(
                applied=False,
                reason="duplicate_transaction",
                payment_id=payment.id,
                mapped_status=mapped_status,
            )

        metadata = dict(payment.payment_metadata or {})
        metadata["order_status"] = "PAID" if mapped_status == PaymentStatus.PAID else "ACTIVE"
        metadata["last_raw_payment_status"] = raw_status
        if payment_method_name:
            metadata["payment_method"] = payment_method_name

        payment_updates: dict[str, Any] = {"status": mapped_status, "payment_metadata": metadata}
        if mapped_status == PaymentStatus.PAID:
            payment_updates["paid_at"] = paid_at or datetime.now(UTC)
        if cf_payment_id:
            payment_updates["external_transaction_id"] = cf_payment_id

        await self.payments.update(payment, **payment_updates)
        await self.session.commit()

        await self._apply_order_state(payment, mapped_status=mapped_status, raw_status=raw_status)

        logger.info(
            "cashfree_payment_event_applied",
            payment_id=str(payment.id),
            mapped_status=mapped_status.value,
            transaction_id=str(transaction.id),
        )
        return PaymentEventResult(
            applied=True, reason=None, payment_id=payment.id, mapped_status=mapped_status
        )

    async def _record_transaction(
        self,
        payment: Payment,
        *,
        cf_payment_id: str | None,
        status: PaymentStatus,
        amount: Decimal,
        raw_payload: dict[str, Any],
    ) -> tuple[Any, bool]:
        transaction, created = await self.payment_transactions.create_if_new(
            gateway="cashfree",
            gateway_transaction_id=cf_payment_id,
            payment_id=payment.id,
            status=status,
            amount=amount,
            raw_payload=raw_payload,
        )
        await self.session.commit()
        return transaction, created

    async def _apply_order_state(
        self, payment: Payment, *, mapped_status: PaymentStatus, raw_status: str | None
    ) -> None:
        order = await self.orders.get_by_id(payment.order_id)
        if order is None:
            return

        if mapped_status == PaymentStatus.PAID:
            if order.payment_status != PaymentStatus.PAID:
                await self.orders.update(order, payment_status=PaymentStatus.PAID)
            if order.status == OrderStatus.PENDING and OrderStatus.CONFIRMED in (
                ORDER_STATUS_TRANSITIONS.get(order.status, set())
            ):
                await self.orders.update(order, status=OrderStatus.CONFIRMED)
            await self.order_events.create(
                order_id=order.id,
                event_type="payment.cashfree_paid",
                status=PaymentStatus.PAID.value,
                description=f"Cashfree payment confirmed ({payment.amount} {payment.currency}).",
                source="cashfree",
            )
        else:
            # A FAILED/dropped attempt never changes `Order.payment_status`
            # away from PENDING — the customer may still retry via a new
            # checkout session (spec: never mark paid on anything but a
            # verified success; a failure isn't itself a state change to
            # apply beyond recording it for visibility).
            await self.order_events.create(
                order_id=order.id,
                event_type="payment.cashfree_failed",
                status=mapped_status.value,
                description=f"Cashfree payment {raw_status or mapped_status.value}.",
                source="cashfree",
            )
        await self.session.commit()

    # --- Reconciliation (fallback for a delayed/missed webhook) -------

    async def reconcile_payment(self, order_id: uuid.UUID, *, actor: User | None) -> Payment:
        """Direct, authenticated Cashfree API lookup — a fallback for a
        delayed/missed webhook, never a replacement for it (spec §13:
        not aggressive polling; operator/task-triggered only). Applies
        every payment attempt Cashfree currently reports for this order
        through the exact same `apply_payment_event` the webhook uses.
        """
        payment = await self.get_payment_for_order(order_id)
        cashfree_order_id = payment.external_id
        if not cashfree_order_id:
            raise ConflictError("This payment has no Cashfree order_id to reconcile against.")

        config = self._require_config()
        client = CashfreeClient(config)
        try:
            attempts = await client.get_payments_for_order(cashfree_order_id)
        except CashfreeApiError as exc:
            raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc
        finally:
            await client.aclose()

        for raw in attempts:
            if not isinstance(raw, dict):
                continue
            method = raw.get("payment_method")
            method_name = next(iter(method), None) if isinstance(method, dict) else None
            raw_amount = raw.get("payment_amount")
            amount = Decimal(str(raw_amount)) if raw_amount is not None else None
            paid_at = parse_iso_datetime(raw.get("payment_time"))
            await self.apply_payment_event(
                cashfree_order_id=cashfree_order_id,
                cf_payment_id=(
                    str(raw["cf_payment_id"]) if raw.get("cf_payment_id") is not None else None
                ),
                raw_status=raw.get("payment_status"),
                amount=amount,
                currency=raw.get("payment_currency"),
                payment_method_name=method_name,
                paid_at=paid_at,
                raw_payload={k: v for k, v in raw.items() if k != "payment_method"} | (
                    {"payment_method": method_name} if method_name else {}
                ),
            )

        await self.audit.record(
            user=actor,
            action="payment.cashfree_reconciled",
            entity_type="payment",
            entity_id=str(payment.id),
            new_value={"attempts_checked": len(attempts)},
        )
        await self.session.commit()

        return await self.get_payment_for_order(order_id)
