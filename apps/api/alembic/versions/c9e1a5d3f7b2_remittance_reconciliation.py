"""remittance reconciliation (Modules 6 & 7)

Revision ID: c9e1a5d3f7b2
Revises: b4d8e2a6c1f7
Create Date: 2026-11-24 00:00:00.000000

Additive and reversible. Six new tables (preserved uploaded files, bank
credits, COD remittance lines, gateway settlement lines, bank-credit links,
manual adjustments) and two new permissions (`reconciliation.upload`,
`reconciliation.adjust`) granted to ADMIN only (other roles need an
explicit, separate authorization). No existing table is altered and no
financial row is written.

`downgrade()` drops only the six new tables. The two permission rows are
left in place on purpose (they may already be assigned to users; leaving an
unused permission is harmless).
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9e1a5d3f7b2"
down_revision: str | None = "b4d8e2a6c1f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUM = sa.Numeric(precision=14, scale=2)
DT = app.db.base.AwareDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
GUID = app.db.base.GUID()

NEW_PERMISSIONS = {
    "reconciliation.upload": "Upload COD / prepaid settlement reports and bank statements",
    "reconciliation.adjust": "Manually match, link or adjust remittance / settlement records",
}
# Nobody else by default -- see docs/security/reconciliation-permissions.md
GRANTED_ROLES = ("ADMIN",)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", GUID, nullable=False),
        sa.Column("created_at", DT, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", DT, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ]


def _fk(column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [target], ondelete=ondelete)


def _index(table: str, *columns: str) -> None:
    op.create_index(f"ix_{table}_{'_'.join(columns)}", table, list(columns))


def upgrade() -> None:
    op.create_table(
        "settlement_files",
        sa.Column("module", sa.String(length=12), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("row_errors", JSON, nullable=True),
        sa.Column("uploaded_by", GUID, nullable=True),
        _fk("uploaded_by", "users.id", "SET NULL"),
        sa.UniqueConstraint("module", "sha256", name="uq_settlement_files_module_sha"),
        *_base(),
    )
    _index("settlement_files", "module")

    op.create_table(
        "bank_credits",
        sa.Column("file_id", GUID, nullable=True),
        sa.Column("credit_date", DT, nullable=True),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("narration", sa.String(length=500), nullable=True),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("raw_row", JSON, nullable=True),
        _fk("file_id", "settlement_files.id", "SET NULL"),
        sa.UniqueConstraint("dedupe_key", name="uq_bank_credits_dedupe_key"),
        *_base(),
    )
    _index("bank_credits", "file_id")
    _index("bank_credits", "credit_date")
    _index("bank_credits", "reference")

    op.create_table(
        "cod_remittance_lines",
        sa.Column("file_id", GUID, nullable=True),
        sa.Column("partner_name", sa.String(length=128), nullable=False),
        sa.Column("awb", sa.String(length=64), nullable=True),
        sa.Column("order_ref", sa.String(length=64), nullable=True),
        sa.Column("remittance_reference", sa.String(length=128), nullable=True),
        sa.Column("remittance_amount", NUM, nullable=False),
        sa.Column("cod_amount_reported", NUM, nullable=True),
        sa.Column("remittance_date", DT, nullable=True),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("matched_order_id", GUID, nullable=True),
        sa.Column("match_method", sa.String(length=16), nullable=True),
        sa.Column("matched_by", GUID, nullable=True),
        sa.Column("raw_row", JSON, nullable=True),
        _fk("file_id", "settlement_files.id", "SET NULL"),
        _fk("matched_order_id", "orders.id", "SET NULL"),
        _fk("matched_by", "users.id", "SET NULL"),
        sa.UniqueConstraint("dedupe_key", name="uq_cod_remittance_lines_dedupe_key"),
        *_base(),
    )
    for col in (
        "file_id",
        "partner_name",
        "awb",
        "order_ref",
        "remittance_reference",
        "remittance_date",
        "matched_order_id",
    ):
        _index("cod_remittance_lines", col)

    op.create_table(
        "gateway_settlement_lines",
        sa.Column("file_id", GUID, nullable=True),
        sa.Column("gateway", sa.String(length=64), nullable=False),
        sa.Column("gateway_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("order_ref", sa.String(length=64), nullable=True),
        sa.Column("settlement_reference", sa.String(length=128), nullable=True),
        sa.Column("gross_amount", NUM, nullable=True),
        sa.Column("gateway_charges", NUM, nullable=False),
        sa.Column("gst_amount", NUM, nullable=False),
        sa.Column("refund_amount", NUM, nullable=False),
        sa.Column("net_amount", NUM, nullable=False),
        sa.Column("transaction_date", DT, nullable=True),
        sa.Column("settlement_date", DT, nullable=True),
        sa.Column("line_status", sa.String(length=32), nullable=True),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("matched_order_id", GUID, nullable=True),
        sa.Column("match_method", sa.String(length=16), nullable=True),
        sa.Column("matched_by", GUID, nullable=True),
        sa.Column("raw_row", JSON, nullable=True),
        _fk("file_id", "settlement_files.id", "SET NULL"),
        _fk("matched_order_id", "orders.id", "SET NULL"),
        _fk("matched_by", "users.id", "SET NULL"),
        sa.UniqueConstraint("dedupe_key", name="uq_gateway_settlement_lines_dedupe_key"),
        *_base(),
    )
    for col in (
        "file_id",
        "gateway",
        "gateway_transaction_id",
        "order_ref",
        "settlement_reference",
        "settlement_date",
        "matched_order_id",
    ):
        _index("gateway_settlement_lines", col)

    op.create_table(
        "bank_credit_links",
        sa.Column("bank_credit_id", GUID, nullable=False),
        sa.Column("module", sa.String(length=12), nullable=False),
        sa.Column("reference", sa.String(length=128), nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("linked_by", GUID, nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("reversed_at", DT, nullable=True),
        sa.Column("reversed_by", GUID, nullable=True),
        _fk("bank_credit_id", "bank_credits.id", "CASCADE"),
        _fk("linked_by", "users.id", "SET NULL"),
        _fk("reversed_by", "users.id", "SET NULL"),
        sa.UniqueConstraint(
            "bank_credit_id", "module", "reference", name="uq_bank_credit_links_credit_ref"
        ),
        *_base(),
    )
    _index("bank_credit_links", "bank_credit_id")
    _index("bank_credit_links", "module")
    _index("bank_credit_links", "reference")

    op.create_table(
        "reconciliation_adjustments",
        sa.Column("module", sa.String(length=12), nullable=False),
        sa.Column("order_id", GUID, nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("created_by", GUID, nullable=True),
        sa.Column("reversed_at", DT, nullable=True),
        sa.Column("reversed_by", GUID, nullable=True),
        _fk("order_id", "orders.id", "CASCADE"),
        _fk("created_by", "users.id", "SET NULL"),
        _fk("reversed_by", "users.id", "SET NULL"),
        *_base(),
    )
    _index("reconciliation_adjustments", "module")
    _index("reconciliation_adjustments", "order_id")

    conn = op.get_bind()
    for code, description in NEW_PERMISSIONS.items():
        module, action = code.split(".", 1)
        conn.execute(
            sa.text(
                "INSERT INTO permissions (id, code, module, action, description) "
                "SELECT gen_random_uuid(), :code, :module, :action, :description "
                "WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE code = :code)"
            ),
            {"code": code, "module": module, "action": action, "description": description},
        )
        for role in GRANTED_ROLES:
            conn.execute(
                sa.text(
                    "INSERT INTO role_permissions (id, role_id, permission_id) "
                    "SELECT gen_random_uuid(), r.id, p.id FROM roles r, permissions p "
                    "WHERE r.name = :role AND p.code = :code AND NOT EXISTS ("
                    "  SELECT 1 FROM role_permissions rp "
                    "  WHERE rp.role_id = r.id AND rp.permission_id = p.id)"
                ),
                {"role": role, "code": code},
            )


def downgrade() -> None:
    op.drop_table("reconciliation_adjustments")
    op.drop_table("bank_credit_links")
    op.drop_table("gateway_settlement_lines")
    op.drop_table("cod_remittance_lines")
    op.drop_table("bank_credits")
    op.drop_table("settlement_files")
