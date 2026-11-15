"""seed IMS permissions and roles

Revision ID: c3d9f1a7e5b2
Revises: b7e4c2a9d1f3
Create Date: 2026-11-15 00:00:00.000000

Why a migration: the IMS roles used to be created only by running
`scripts/seed.py` by hand, so any database that ran `alembic upgrade head`
but not the seed (the usual deploy path) had IMS tables but no IMS roles --
Administration -> Roles and the New User modal, which just render
`GET /roles`, never showed them.

Idempotent and non-destructive: every row is inserted only if it does not
already exist (matched on `permissions.code`, `roles.name` and the
role/permission pair). Existing roles are never renamed, re-described or
stripped of permissions; nothing is deleted. `scripts/seed.py` defines the
same grants, so running both is safe.

Grants:
  IMS_USER           ims.read, ims.stock.write
  INVENTORY_STAFF    ims.read, ims.stock.write
  INVENTORY_MANAGER  ims.read, ims.stock.write, ims.manage
  ADMIN (if present) ims.read, ims.stock.write, ims.manage
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d9f1a7e5b2"
down_revision = "b7e4c2a9d1f3"
branch_labels = None
depends_on = None

PERMISSIONS = {
    "ims.read": "View the Inventory Management workspace (stock, history, reports)",
    "ims.stock.write": "Record stock inward, transfers, external returns and adjustments",
    "ims.manage": (
        "Manage IMS warehouses, packaging, unit conversions, parties and opening balances"
    ),
}
ROLES = {
    "IMS_USER": ("IMS User role", ["ims.read", "ims.stock.write"]),
    "INVENTORY_STAFF": ("Inventory_Staff role", ["ims.read", "ims.stock.write"]),
    "INVENTORY_MANAGER": (
        "Inventory_Manager role",
        ["ims.read", "ims.stock.write", "ims.manage"],
    ),
}
ADMIN_GRANTS = ["ims.read", "ims.stock.write", "ims.manage"]


def _grant(conn: sa.engine.Connection, role_name: str, codes: list[str]) -> None:
    for code in codes:
        conn.execute(
            sa.text(
                "INSERT INTO role_permissions (id, role_id, permission_id) "
                "SELECT gen_random_uuid(), r.id, p.id FROM roles r, permissions p "
                "WHERE r.name = :role AND p.code = :code AND NOT EXISTS ("
                "  SELECT 1 FROM role_permissions rp "
                "  WHERE rp.role_id = r.id AND rp.permission_id = p.id)"
            ),
            {"role": role_name, "code": code},
        )


def upgrade() -> None:
    conn = op.get_bind()
    for code, description in PERMISSIONS.items():
        module, action = code.split(".", 1)
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, code, module, action, description) "
                "SELECT gen_random_uuid(), :code, :module, :action, :description "
                "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"
            ),
            {"code": code, "module": module, "action": action, "description": description},
        )
    for name, (description, codes) in ROLES.items():
        conn.execute(
            sa.text(
                "INSERT INTO roles (id, name, description) "
                "SELECT gen_random_uuid(), :name, :description "
                "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :name)"
            ),
            {"name": name, "description": description},
        )
        _grant(conn, name, codes)
    _grant(conn, "ADMIN", ADMIN_GRANTS)


def downgrade() -> None:
    """Deliberate no-op: roles/permissions may by now be assigned to real
    users, so they are never removed automatically."""
