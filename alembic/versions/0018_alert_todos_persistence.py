"""新增预警待办持久化表。

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "alert_todos"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("todo_id", sa.String(length=128), nullable=False),
        sa.Column("dedupe_key", sa.String(length=256), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("stock_name", sa.String(length=128), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("suggested_action", sa.String(length=64), nullable=True),
        sa.Column("suggested_direction", sa.String(length=16), nullable=True),
        sa.Column("suggested_quantity", sa.Integer(), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("avg_cost", sa.Float(), nullable=True),
        sa.Column("pnl_pct", sa.Float(), nullable=True),
        sa.Column("action_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("analysis_id", sa.String(length=128), nullable=True),
        sa.Column("linked_order_id", sa.String(length=128), nullable=True),
        sa.Column("snooze_until", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", sa.String(length=64), nullable=True),
        sa.Column("resolved_at", sa.String(length=64), nullable=True),
        sa.Column("ignored_at", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_alert_todos_dedupe_key"),
    )
    op.create_index(op.f("ix_alert_todos_todo_id"), TABLE_NAME, ["todo_id"], unique=True)
    op.create_index(op.f("ix_alert_todos_dedupe_key"), TABLE_NAME, ["dedupe_key"], unique=True)
    op.create_index(op.f("ix_alert_todos_source_type"), TABLE_NAME, ["source_type"])
    op.create_index(op.f("ix_alert_todos_source_id"), TABLE_NAME, ["source_id"])
    op.create_index(op.f("ix_alert_todos_symbol"), TABLE_NAME, ["symbol"])
    op.create_index(op.f("ix_alert_todos_severity"), TABLE_NAME, ["severity"])
    op.create_index(op.f("ix_alert_todos_status"), TABLE_NAME, ["status"])
    op.create_index(op.f("ix_alert_todos_action_required"), TABLE_NAME, ["action_required"])
    op.create_index(op.f("ix_alert_todos_analysis_id"), TABLE_NAME, ["analysis_id"])
    op.create_index(op.f("ix_alert_todos_linked_order_id"), TABLE_NAME, ["linked_order_id"])
    op.create_index(op.f("ix_alert_todos_snooze_until"), TABLE_NAME, ["snooze_until"])
    op.create_index("ix_alert_todos_status_severity", TABLE_NAME, ["status", "severity"])
    op.create_index("ix_alert_todos_symbol_status", TABLE_NAME, ["symbol", "status"])
    op.create_index("ix_alert_todos_source", TABLE_NAME, ["source_type", "source_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    op.drop_table(TABLE_NAME)
