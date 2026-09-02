"""Development database seed script.

Creates, in a single transaction:
  - 5 roles (ADMIN, OPERATIONS, CUSTOMER_SUPPORT, MARKETING, MANAGEMENT)
  - Every `module.action` permission the API actually enforces
  - RolePermission assignments matching the access grid in
    docs/security/rbac.md
  - One dev admin user (is_superuser=True, bypasses permission checks
    entirely — see app/dependencies/auth.py:require_permission).
    Email/password come from ADMIN_SEED_EMAIL/ADMIN_SEED_PASSWORD env
    vars; the defaults below are dev-only and are never a production
    credential — set those env vars in any environment that matters.
  - Courier records (Blue Dart, Delhivery, Ecom Express, Other) —
    database rows only, no live courier API in Phase 1.

Deliberately does NOT seed fake customers/orders/products (spec §52).
Idempotent: safe to run against an already-seeded database.

Run with: python scripts/seed.py
"""

from __future__ import annotations

import asyncio
import os

from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.auth import User
from app.models.courier import Courier
from app.models.enums import IntegrationStatus, IntegrationType
from app.models.integration import Integration, IntegrationCode
from app.models.rbac import Permission, Role, RolePermission, UserRole
from sqlalchemy import select

configure_logging()
logger = get_logger(__name__)

# module.action -> description
PERMISSIONS: dict[str, str] = {
    "orders.read": "View orders",
    "orders.create": "Create orders",
    "orders.update": "Update order status/details (non-cancellation)",
    "orders.cancel": "Cancel orders",
    "shipments.read": "View shipments",
    "shipments.update": "Create/update shipments",
    "customers.read": "View customers",
    "customers.update": "Create/update customers",
    "products.read": "View products",
    "products.update": "Create/update products",
    "ndr.read": "View NDR records",
    "ndr.update": "Update NDR records",
    "rto.read": "View RTO records",
    "rto.update": "Update RTO records",
    "returns.read": "View returns",
    "returns.update": "Create/update returns",
    "refunds.read": "View refunds",
    "payments.read": "View payments",
    "payments.create": "Initiate/reconcile a Cashfree payment checkout for an order",
    "couriers.read": "View couriers",
    "couriers.update": "Create/update couriers",
    "analytics.read": "View dashboard/analytics",
    "users.manage": "Create/update/deactivate users",
    "roles.manage": "Create/update roles",
    "audit_logs.read": "View audit logs",
    "integrations.read": "View integration status and sync history",
    "integrations.manage": "Configure integrations",
    "integrations.test": "Run an integration health check",
    "sync_jobs.read": "View sync job history",
    "sync_jobs.manage": "Trigger a manual sync",
    "webhooks.read": "View webhook event log",
    "reconciliation.read": "View reconciliation runs and results",
    "reconciliation.manage": "Trigger a reconciliation run and resolve mismatches",
    "telecalling.manage": (
        "Team Leader: view team orders/telecallers, assign/reassign orders, "
        "view team calling activity and performance"
    ),
    "calls.manage": "Telecaller: view own assigned orders, log calls, manage own follow-ups",
    # No "settings.manage"-gated read counterpart: `GET /settings` only
    # requires authentication (see `app/api/v1/endpoints/settings.py`) --
    # nothing in it is sensitive, and it's read in the background by every
    # role (page size, dashboard refresh interval, session timeout), not
    # just whoever can change it.
    "settings.manage": "Update OMS settings (Administration -> Settings)",
}

ROLE_PERMISSIONS: dict[str, list[str] | str] = {
    "ADMIN": "*",
    "OPERATIONS": [
        "orders.read",
        "orders.update",
        "orders.cancel",
        "shipments.read",
        "shipments.update",
        "ndr.read",
        "ndr.update",
        "rto.read",
        "rto.update",
        "couriers.read",
        "couriers.update",
        "customers.read",
        "reconciliation.read",
        "reconciliation.manage",
        "payments.read",
        "payments.create",
    ],
    "CUSTOMER_SUPPORT": [
        "customers.read",
        "customers.update",
        "orders.read",
        "orders.cancel",
        "shipments.read",
        "ndr.read",
        "returns.read",
        "returns.update",
        "refunds.read",
    ],
    "MARKETING": [
        "analytics.read",
    ],
    "MANAGEMENT": [
        "analytics.read",
        "orders.read",
        "customers.read",
        "shipments.read",
        "ndr.read",
        "rto.read",
        "returns.read",
        "refunds.read",
        "payments.read",
        "couriers.read",
        "integrations.read",
        "sync_jobs.read",
        "webhooks.read",
        "reconciliation.read",
    ],
    "TEAM_LEADER": ["telecalling.manage"],
    "TELECALLER": ["calls.manage"],
}

COURIERS = [
    ("Blue Dart", "blue_dart"),
    ("Delhivery", "delhivery"),
    ("Ecom Express", "ecom_express"),
    ("Other", "other"),
]

# name, code, type — every row seeds DISCONNECTED/disabled (spec §16: an
# honest "Not Connected" in the UI, never fake production status). Phase 2
# flips `enabled`/`status` once real credentials + an adapter exist.
INTEGRATIONS = [
    ("Shopify", IntegrationCode.SHOPIFY, IntegrationType.ECOMMERCE),
    ("Shiprocket", IntegrationCode.SHIPROCKET, IntegrationType.COURIER),
    # `IntegrationType` has no dedicated "payment gateway" value (adding
    # one would mean an Alembic `ALTER TYPE ... ADD VALUE` migration for
    # a purely descriptive field — nothing in the app branches on
    # `IntegrationType`, confirmed by inspection). ECOMMERCE is the
    # closest existing fit; this row exists only so
    # `WebhookService.ingest()` has an `integration_id` to attach
    # Cashfree webhook deliveries to (see app.integrations.cashfree).
    ("Cashfree", IntegrationCode.CASHFREE, IntegrationType.ECOMMERCE),
    ("Blue Dart", IntegrationCode.BLUE_DART, IntegrationType.COURIER),
    ("Delhivery", IntegrationCode.DELHIVERY, IntegrationType.COURIER),
    ("Ecom Express", IntegrationCode.ECOM_EXPRESS, IntegrationType.COURIER),
    ("WhatsApp", IntegrationCode.WHATSAPP, IntegrationType.MESSAGING),
    ("Meta", IntegrationCode.META, IntegrationType.SOCIAL),
    ("Instagram", IntegrationCode.INSTAGRAM, IntegrationType.SOCIAL),
]


async def _seed_permissions(session) -> dict[str, Permission]:  # noqa: ANN001
    by_code: dict[str, Permission] = {}
    for code, description in PERMISSIONS.items():
        existing = (
            await session.execute(select(Permission).where(Permission.code == code))
        ).scalar_one_or_none()
        if existing is None:
            module, action = code.split(".", 1)
            existing = Permission(code=code, module=module, action=action, description=description)
            session.add(existing)
            await session.flush()
        by_code[code] = existing
    return by_code


async def _seed_roles(session, permissions: dict[str, Permission]) -> None:  # noqa: ANN001
    code_by_permission_id = {p.id: code for code, p in permissions.items()}

    for role_name, codes in ROLE_PERMISSIONS.items():
        role = (
            await session.execute(select(Role).where(Role.name == role_name))
        ).scalar_one_or_none()
        if role is None:
            role = Role(name=role_name, description=f"{role_name.title()} role")
            session.add(role)
            await session.flush()

        wanted_codes = set(permissions.keys()) if codes == "*" else set(codes)
        # Query the join table directly rather than `role.role_permissions`
        # — under AsyncSession, touching a relationship collection that
        # wasn't eagerly loaded in the same query raises MissingGreenlet.
        existing_permission_ids = (
            (
                await session.execute(
                    select(RolePermission.permission_id).where(RolePermission.role_id == role.id)
                )
            )
            .scalars()
            .all()
        )
        existing_codes = {code_by_permission_id[pid] for pid in existing_permission_ids}
        for code in wanted_codes - existing_codes:
            session.add(RolePermission(role_id=role.id, permission_id=permissions[code].id))
        await session.flush()

    logger.info("roles_seeded", roles=list(ROLE_PERMISSIONS.keys()))


async def _seed_admin_user(session) -> None:  # noqa: ANN001
    email = os.environ.get("ADMIN_SEED_EMAIL", "admin@ayushwellness-oms.com")
    password = os.environ.get("ADMIN_SEED_PASSWORD", "ChangeMe!DevOnly123")

    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        logger.info("admin_user_already_exists", email=email)
        return

    admin_role = (
        await session.execute(select(Role).where(Role.name == "ADMIN"))
    ).scalar_one_or_none()

    user = User(
        name="Development Admin",
        email=email,
        password_hash=hash_password(password),
        is_active=True,
        is_superuser=True,
    )
    session.add(user)
    await session.flush()

    if admin_role is not None:
        session.add(UserRole(user_id=user.id, role_id=admin_role.id))

    logger.warning(
        "admin_user_created",
        email=email,
        message=(
            "Dev-only credential. Set ADMIN_SEED_EMAIL/ADMIN_SEED_PASSWORD "
            "before seeding any non-local environment."
        ),
    )


async def _seed_couriers(session) -> None:  # noqa: ANN001
    for name, code in COURIERS:
        existing = (
            await session.execute(select(Courier).where(Courier.code == code))
        ).scalar_one_or_none()
        if existing is None:
            session.add(Courier(name=name, code=code, is_active=True))
    await session.flush()
    logger.info("couriers_seeded", couriers=[c[1] for c in COURIERS])


async def _seed_integrations(session) -> None:  # noqa: ANN001
    for name, code, integration_type in INTEGRATIONS:
        existing = (
            await session.execute(select(Integration).where(Integration.code == code))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Integration(
                    name=name,
                    code=code,
                    type=integration_type,
                    status=IntegrationStatus.DISCONNECTED,
                    enabled=False,
                )
            )
    await session.flush()
    logger.info("integrations_seeded", integrations=[i[1] for i in INTEGRATIONS])


async def main() -> None:
    async with AsyncSessionLocal() as session:
        permissions = await _seed_permissions(session)
        await _seed_roles(session, permissions)
        await _seed_admin_user(session)
        await _seed_couriers(session)
        await _seed_integrations(session)
        await session.commit()
    logger.info("seed_complete")


if __name__ == "__main__":
    asyncio.run(main())
