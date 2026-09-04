"""add chat_query_logs (OMS AI Assistant audit trail)

Revision ID: e7a1c9d4f2b6
Revises: d8f2c5a91b4e
Create Date: 2026-09-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import app.db.base
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7a1c9d4f2b6"
down_revision: str | None = "d8f2c5a91b4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_query_logs",
        sa.Column("id", app.db.base.GUID(), nullable=False),
        sa.Column("user_id", app.db.base.GUID(), nullable=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("partial", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "tools_used",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "sources",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            app.db.base.AwareDateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chat_query_logs_user_id"), "chat_query_logs", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_chat_query_logs_conversation_id"),
        "chat_query_logs",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chat_query_logs_error_code"), "chat_query_logs", ["error_code"], unique=False
    )
    op.create_index(
        op.f("ix_chat_query_logs_created_at"), "chat_query_logs", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_query_logs_created_at"), table_name="chat_query_logs")
    op.drop_index(op.f("ix_chat_query_logs_error_code"), table_name="chat_query_logs")
    op.drop_index(op.f("ix_chat_query_logs_conversation_id"), table_name="chat_query_logs")
    op.drop_index(op.f("ix_chat_query_logs_user_id"), table_name="chat_query_logs")
    op.drop_table("chat_query_logs")
