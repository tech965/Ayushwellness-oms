"""SQLAlchemy ORM models.

Every model is imported here so `Base.metadata` is fully populated for
Alembic autogenerate and for `Base.metadata.create_all` in the test
suite. See `docs/database/schema.md` for the full schema design this
package implements.
"""

from __future__ import annotations

from app.models.audit_log import AuditLog
from app.models.auth import RefreshToken, User
from app.models.courier import Courier
from app.models.customer import Customer, CustomerAddress
from app.models.integration import Integration, SyncError, SyncJob, WebhookEvent
from app.models.ndr import NDR
from app.models.order import Order, OrderEvent, OrderItem
from app.models.payment import Payment, PaymentTransaction
from app.models.product import Product, ProductVariant
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.reconciliation import ReconciliationResult, ReconciliationRun
from app.models.refund import Refund
from app.models.returns import Return
from app.models.rto import RTO
from app.models.shipment import Shipment, ShipmentEvent

__all__ = [
    "AuditLog",
    "RefreshToken",
    "User",
    "Courier",
    "Customer",
    "CustomerAddress",
    "Integration",
    "SyncError",
    "SyncJob",
    "WebhookEvent",
    "NDR",
    "Order",
    "OrderEvent",
    "OrderItem",
    "Payment",
    "PaymentTransaction",
    "Product",
    "ProductVariant",
    "Permission",
    "Role",
    "RolePermission",
    "UserRole",
    "ReconciliationResult",
    "ReconciliationRun",
    "Refund",
    "Return",
    "RTO",
    "Shipment",
    "ShipmentEvent",
]
