"""SQLAlchemy ORM models.

Every model is imported here so `Base.metadata` is fully populated for
Alembic autogenerate and for `Base.metadata.create_all` in the test
suite. See `docs/database/schema.md` for the full schema design this
package implements.
"""

from __future__ import annotations

from app.models.abandoned_checkout import AbandonedCheckout
from app.models.audit_log import AuditLog
from app.models.auth import RefreshToken, User
from app.models.cashfree_settlement import CashfreeSettlement
from app.models.cod_remittance import CodRemittance
from app.models.shiprocket_finance import ShiprocketPayout, ShiprocketShipmentCharge
from app.models.shiprocket_wallet import (
    ShiprocketWalletCheck,
    ShiprocketWalletSnapshot,
    ShiprocketWalletTransaction,
)
from app.models.chat import ChatQueryLog
from app.models.checkout_workflow import CheckoutWorkflow
from app.models.courier import Courier
from app.models.customer import Customer, CustomerAddress
from app.models.integration import Integration, SyncError, SyncJob, WebhookEvent
from app.models.inventory import InventoryMovement
from app.models.inventory_architecture import (
    InventoryReservation,
    ProviderVariantMapping,
    Warehouse,
)
from app.models.ims import (
    ImsAdjustment,
    ImsBalance,
    ImsExternalParty,
    ImsItem,
    ImsLedger,
    ImsPackagingProduct,
    ImsReturn,
    ImsReturnLine,
    ImsTransfer,
    ImsTransferLine,
    ImsUnitConversion,
)
from app.models.ndr import NDR
from app.models.order import Order, OrderEvent, OrderItem
from app.models.order_workflow import OrderWorkflow
from app.models.payment import Payment, PaymentTransaction
from app.models.platform_inventory import InventoryPlatform, PlatformStockMovement
from app.models.product import CatalogVariant, Product, ProductVariant
from app.models.rbac import Permission, Role, RolePermission, UserRole
from app.models.reconciliation import ReconciliationResult, ReconciliationRun
from app.models.refund import Refund
from app.models.remittance_reconciliation import (
    BankCredit,
    BankCreditLink,
    CodRemittanceLine,
    GatewaySettlementLine,
    GatewaySyncState,
    ReconciliationAdjustment,
    SettlementFile,
)
from app.models.returns import Return
from app.models.rto import RTO
from app.models.settings import AppSettings
from app.models.shipment import Shipment, ShipmentEvent
from app.models.shipment_ref import ShipmentRef
from app.models.telecalling import (
    CallAttempt,
    CheckoutAssignment,
    CheckoutCallAttempt,
    OrderAssignment,
)
from app.models.webhook_delivery import WebhookDelivery
from app.models.whatsapp_automation import (
    WhatsAppContact,
    WhatsAppCustomerWindow,
    WhatsAppQueuedMessage,
    WhatsAppRecipientGroup,
    WhatsAppRecipientGroupMember,
    WhatsAppSchedule,
    WhatsAppScheduleRecipient,
)

__all__ = [
    "AbandonedCheckout",
    "AuditLog",
    "ChatQueryLog",
    "RefreshToken",
    "User",
    "CashfreeSettlement",
    "Courier",
    "Customer",
    "CustomerAddress",
    "Integration",
    "SyncError",
    "SyncJob",
    "WebhookEvent",
    "InventoryMovement",
    "ImsAdjustment",
    "ImsBalance",
    "ImsExternalParty",
    "ImsItem",
    "ImsLedger",
    "ImsPackagingProduct",
    "ImsReturn",
    "ImsReturnLine",
    "ImsTransfer",
    "ImsTransferLine",
    "ImsUnitConversion",
    "NDR",
    "Order",
    "OrderEvent",
    "OrderItem",
    "Payment",
    "PaymentTransaction",
    "InventoryPlatform",
    "PlatformStockMovement",
    "CatalogVariant",
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
    "AppSettings",
    "Shipment",
    "ShipmentEvent",
    "CallAttempt",
    "CheckoutAssignment",
    "CheckoutCallAttempt",
    "OrderAssignment",
    "OrderWorkflow",
    "InventoryReservation",
    "ProviderVariantMapping",
    "Warehouse",
    "CheckoutWorkflow",
    "ShipmentRef",
    "BankCredit",
    "BankCreditLink",
    "CodRemittance",
    "CodRemittanceLine",
    "GatewaySettlementLine",
    "GatewaySyncState",
    "ReconciliationAdjustment",
    "SettlementFile",
    "ShiprocketPayout",
    "ShiprocketShipmentCharge",
    "ShiprocketWalletCheck",
    "ShiprocketWalletSnapshot",
    "ShiprocketWalletTransaction",
    "WebhookDelivery",
    "WhatsAppContact",
    "WhatsAppCustomerWindow",
    "WhatsAppQueuedMessage",
    "WhatsAppRecipientGroup",
    "WhatsAppRecipientGroupMember",
    "WhatsAppSchedule",
    "WhatsAppScheduleRecipient",
]
