"""补充模拟交易表高频查询索引。

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES = [
    (
        "ix_paper_orders_created_at",
        "paper_orders",
        ["created_at"],
    ),
    (
        "ix_paper_orders_side_status_created",
        "paper_orders",
        ["side", "status", "created_at"],
    ),
    (
        "ix_paper_orders_strategy_side_status_created",
        "paper_orders",
        ["strategy_mode", "side", "status", "created_at"],
    ),
    (
        "ix_paper_orders_symbol_created",
        "paper_orders",
        ["symbol", "created_at"],
    ),
    (
        "ix_paper_cash_flows_symbol_created",
        "paper_cash_flows",
        ["symbol", "created_at"],
    ),
]


def upgrade() -> None:
    for index_name, table_name, columns in INDEXES:
        op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(INDEXES):
        op.drop_index(index_name, table_name=table_name)
