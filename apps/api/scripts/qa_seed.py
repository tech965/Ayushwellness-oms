"""TEMPORARY QA-only local dev seed script — NOT part of the app.

Creates a deterministic set of orders/customers/shipments spanning known
dates (today/yesterday/this-month/last-month/old), payment types/statuses,
order statuses, shipment statuses, and couriers, all IST-anchored, so
dashboard/orders filters can be verified against hand-computed expected
counts. Every record is tagged `source_system="qa_seed"` / order numbers
prefixed `QA-` so it's trivially identifiable and removable
(`scripts/qa_seed_cleanup.py`). Local dev DB only — never run against
production.

Run with: python scripts/qa_seed.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.logging import configure_logging, get_logger
from app.db.session import AsyncSessionLocal
from app.models.courier import Courier
from app.models.customer import Customer
from app.models.enums import (
    CancellationStatus,
    FulfillmentStatus,
    NDRStatus,
    OrderStatus,
    PaymentStatus,
    PaymentType,
    RTOStatus,
    ShipmentDelayStatus,
    ShipmentStatus,
)
from app.models.ndr import NDR
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.rto import RTO
from app.models.shipment import Shipment
from sqlalchemy import select

configure_logging()
logger = get_logger(__name__)

IST = timedelta(hours=5, minutes=30)


def ist(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    """Builds a tz-aware UTC datetime for the given IST wall-clock moment."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC) - IST


async def get_or_create_courier(session, name: str, code: str) -> Courier:
    existing = await session.scalar(select(Courier).where(Courier.code == code))
    if existing:
        return existing
    courier = Courier(name=name, code=code, is_active=True)
    session.add(courier)
    await session.flush()
    return courier


async def get_or_create_customer(session, *, full_name: str, email: str, phone: str) -> Customer:
    existing = await session.scalar(select(Customer).where(Customer.email == email))
    if existing:
        return existing
    customer = Customer(
        full_name=full_name,
        first_name=full_name.split(" ")[0],
        last_name=full_name.split(" ")[-1],
        email=email,
        phone=phone,
        is_active=True,
        source_system="qa_seed",
        external_id=f"qa-{email}",
    )
    session.add(customer)
    await session.flush()
    return customer


async def make_order(
    session,
    *,
    order_number: str,
    order_datetime: datetime,
    customer: Customer | None,
    payment_type: PaymentType,
    payment_status: PaymentStatus,
    status: OrderStatus,
    fulfillment_status: FulfillmentStatus,
    sku: str,
    product_name: str,
    quantity: int,
    unit_price: Decimal,
    shipment_status: ShipmentStatus | None = None,
    courier: Courier | None = None,
    delay_status: ShipmentDelayStatus = ShipmentDelayStatus.UNKNOWN,
    ndr_reason: str | None = None,
    rto_reason: str | None = None,
) -> Order:
    subtotal = unit_price * quantity
    total = subtotal
    order = Order(
        order_number=order_number,
        source_system="qa_seed",
        external_id=order_number,
        customer_id=customer.id if customer else None,
        order_datetime=order_datetime,
        currency="INR",
        subtotal=subtotal,
        discount_amount=Decimal("0"),
        tax_amount=Decimal("0"),
        shipping_charge=Decimal("0"),
        total_amount=total,
        payment_type=payment_type,
        payment_status=payment_status,
        status=status,
        fulfillment_status=fulfillment_status,
        cancellation_status=(
            CancellationStatus.CANCELLED
            if status == OrderStatus.CANCELLED
            else CancellationStatus.NONE
        ),
        created_at=order_datetime,
        updated_at=order_datetime,
    )
    session.add(order)
    await session.flush()

    session.add(
        OrderItem(
            order_id=order.id,
            sku=sku,
            product_name=product_name,
            quantity=quantity,
            unit_price=unit_price,
            discount_amount=Decimal("0"),
            tax_amount=Decimal("0"),
            total_amount=total,
            source_system="qa_seed",
            external_id=f"{order_number}-item",
        )
    )
    session.add(
        Payment(
            order_id=order.id,
            payment_type=payment_type,
            status=payment_status,
            amount=total,
            currency="INR",
            provider="qa_seed",
            paid_at=order_datetime if payment_status == PaymentStatus.PAID else None,
            source_system="qa_seed",
            external_id=f"{order_number}-pay",
        )
    )

    if shipment_status is not None:
        shipment = Shipment(
            order_id=order.id,
            courier_id=courier.id if courier else None,
            current_status=shipment_status,
            delay_status=delay_status,
            awb=f"AWB-{order_number}",
            created_at=order_datetime,
            updated_at=order_datetime,
            actual_delivery_date=(
                order_datetime if shipment_status == ShipmentStatus.DELIVERED else None
            ),
            source_system="qa_seed",
            external_id=f"{order_number}-ship",
        )
        session.add(shipment)
        await session.flush()

        if ndr_reason:
            session.add(
                NDR(
                    shipment_id=shipment.id,
                    order_id=order.id,
                    courier_id=courier.id if courier else None,
                    reason=ndr_reason,
                    status=NDRStatus.OPEN,
                    created_at=order_datetime,
                    updated_at=order_datetime,
                    source_system="qa_seed",
                    external_id=f"{order_number}-ndr",
                )
            )
        if rto_reason:
            session.add(
                RTO(
                    shipment_id=shipment.id,
                    order_id=order.id,
                    courier_id=courier.id if courier else None,
                    reason=rto_reason,
                    status=RTOStatus.INITIATED,
                    initiated_at=order_datetime,
                    created_at=order_datetime,
                    updated_at=order_datetime,
                    source_system="qa_seed",
                    external_id=f"{order_number}-rto",
                )
            )

    return order


async def seed() -> None:
    now = datetime.now(UTC)
    today_ist = now + IST  # naive "wall clock IST" for date math convenience
    y, m, d = today_ist.year, today_ist.month, today_ist.day

    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(Order).where(Order.source_system == "qa_seed"))
        if existing:
            print(
                "QA seed data already present — skipping (run qa_seed_cleanup.py first to reset)."
            )
            return

        delhivery = await get_or_create_courier(session, "Delhivery", "delhivery")
        bluedart = await get_or_create_courier(session, "Blue Dart", "blue-dart")

        jane = await get_or_create_customer(
            session, full_name="Jane QA Doe", email="jane.qa@example.com", phone="+919876543210"
        )
        raj = await get_or_create_customer(
            session, full_name="Raj QA Kumar", email="raj.qa@example.com", phone="+919876543211"
        )

        def shift(days: int) -> tuple[int, int, int]:
            dt = today_ist - timedelta(days=days)
            return dt.year, dt.month, dt.day

        ty, tm, td = y, m, d
        yy, ym, yd = shift(1)

        orders = [
            {
                "order_number": "QA-TODAY-1",
                "order_datetime": ist(ty, tm, td, 9, 0),
                "customer": jane,
                "payment_type": PaymentType.COD,
                "payment_status": PaymentStatus.PAID,
                "status": OrderStatus.DELIVERED,
                "fulfillment_status": FulfillmentStatus.FULFILLED,
                "sku": "QA-SKU-A",
                "product_name": "QA Ashwagandha 60ct",
                "quantity": 2,
                "unit_price": Decimal("499.00"),
                "shipment_status": ShipmentStatus.DELIVERED,
                "courier": delhivery,
            },
            {
                "order_number": "QA-TODAY-2",
                "order_datetime": ist(ty, tm, td, 15, 0),
                "customer": raj,
                "payment_type": PaymentType.PREPAID,
                "payment_status": PaymentStatus.PENDING,
                "status": OrderStatus.PENDING,
                "fulfillment_status": FulfillmentStatus.UNFULFILLED,
                "sku": "QA-SKU-B",
                "product_name": "QA Turmeric Capsules",
                "quantity": 1,
                "unit_price": Decimal("349.00"),
                "shipment_status": None,
            },
            {
                "order_number": "QA-TODAY-NDR",
                "order_datetime": ist(ty, tm, td, 11, 0),
                "customer": jane,
                "payment_type": PaymentType.COD,
                "payment_status": PaymentStatus.PENDING,
                "status": OrderStatus.SHIPPED,
                "fulfillment_status": FulfillmentStatus.FULFILLED,
                "sku": "QA-SKU-A",
                "product_name": "QA Ashwagandha 60ct",
                "quantity": 1,
                "unit_price": Decimal("499.00"),
                "shipment_status": ShipmentStatus.NDR,
                "courier": bluedart,
                "ndr_reason": "Customer not available",
            },
            {
                "order_number": "QA-YEST-1",
                "order_datetime": ist(yy, ym, yd, 12, 0),
                "customer": None,
                "payment_type": PaymentType.COD,
                "payment_status": PaymentStatus.PENDING,
                "status": OrderStatus.PROCESSING,
                "fulfillment_status": FulfillmentStatus.UNFULFILLED,
                "sku": "QA-SKU-C",
                "product_name": "QA Neem Tablets",
                "quantity": 3,
                "unit_price": Decimal("199.00"),
                "shipment_status": ShipmentStatus.IN_TRANSIT,
                "courier": bluedart,
            },
            {
                "order_number": "QA-YEST-2",
                "order_datetime": ist(yy, ym, yd, 20, 0),
                "customer": raj,
                "payment_type": PaymentType.PREPAID,
                "payment_status": PaymentStatus.PAID,
                "status": OrderStatus.SHIPPED,
                "fulfillment_status": FulfillmentStatus.FULFILLED,
                "sku": "QA-SKU-A",
                "product_name": "QA Ashwagandha 60ct",
                "quantity": 1,
                "unit_price": Decimal("499.00"),
                "shipment_status": ShipmentStatus.OUT_FOR_DELIVERY,
                "courier": delhivery,
            },
            {
                "order_number": "QA-YEST-RTO",
                "order_datetime": ist(yy, ym, yd, 18, 0),
                "customer": jane,
                "payment_type": PaymentType.COD,
                "payment_status": PaymentStatus.FAILED,
                "status": OrderStatus.CANCELLED,
                "fulfillment_status": FulfillmentStatus.UNFULFILLED,
                "sku": "QA-SKU-B",
                "product_name": "QA Turmeric Capsules",
                "quantity": 2,
                "unit_price": Decimal("349.00"),
                "shipment_status": ShipmentStatus.RTO_INITIATED,
                "courier": delhivery,
                "rto_reason": "Customer refused",
            },
        ]

        # 5/20 days ago (within the default last-30-days window)
        for offset, suffix, pay_type, pay_status in [
            (5, "5DAYS-1", PaymentType.COD, PaymentStatus.PAID),
            (20, "20DAYS-1", PaymentType.PREPAID, PaymentStatus.PAID),
        ]:
            oy, om, od = shift(offset)
            orders.append(
                {
                    "order_number": f"QA-{suffix}",
                    "order_datetime": ist(oy, om, od, 14, 0),
                    "customer": jane,
                    "payment_type": pay_type,
                    "payment_status": pay_status,
                    "status": OrderStatus.DELIVERED,
                    "fulfillment_status": FulfillmentStatus.FULFILLED,
                    "sku": "QA-SKU-A",
                    "product_name": "QA Ashwagandha 60ct",
                    "quantity": 1,
                    "unit_price": Decimal("499.00"),
                    "shipment_status": ShipmentStatus.DELIVERED,
                    "courier": delhivery,
                }
            )

        # Last month (fixed calendar month, independent of "today")
        last_month_ref = today_ist.replace(day=1) - timedelta(days=1)
        orders.append(
            {
                "order_number": "QA-LASTMONTH-1",
                "order_datetime": ist(last_month_ref.year, last_month_ref.month, 15, 10, 0),
                "customer": raj,
                "payment_type": PaymentType.COD,
                "payment_status": PaymentStatus.PAID,
                "status": OrderStatus.DELIVERED,
                "fulfillment_status": FulfillmentStatus.FULFILLED,
                "sku": "QA-SKU-C",
                "product_name": "QA Neem Tablets",
                "quantity": 5,
                "unit_price": Decimal("199.00"),
                "shipment_status": ShipmentStatus.DELIVERED,
                "courier": bluedart,
            }
        )

        # Well outside any default window — negative control
        orders.append(
            {
                "order_number": "QA-OLD-1",
                "order_datetime": ist(today_ist.year, 1, 5, 9, 0),
                "customer": jane,
                "payment_type": PaymentType.COD,
                "payment_status": PaymentStatus.PAID,
                "status": OrderStatus.DELIVERED,
                "fulfillment_status": FulfillmentStatus.FULFILLED,
                "sku": "QA-SKU-A",
                "product_name": "QA Ashwagandha 60ct",
                "quantity": 1,
                "unit_price": Decimal("499.00"),
                "shipment_status": ShipmentStatus.DELIVERED,
                "courier": delhivery,
            }
        )

        for spec in orders:
            await make_order(session, **spec)

        await session.commit()
        print(f"Seeded {len(orders)} QA orders (today={ty}-{tm:02d}-{td:02d} IST).")


if __name__ == "__main__":
    asyncio.run(seed())
