"""补充 K 线和特征高频查询索引。

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES = [
    (
        "ix_stock_klines_symbol_period_time",
        "stock_klines",
        ["symbol", "period", "trade_time"],
    ),
    (
        "ix_stock_klines_period_time",
        "stock_klines",
        ["period", "trade_time"],
    ),
    (
        "ix_stock_features_symbol_period_time",
        "stock_features",
        ["symbol", "period", "trade_time"],
    ),
    (
        "ix_stock_features_period_time",
        "stock_features",
        ["period", "trade_time"],
    ),
]


def upgrade() -> None:
    for index_name, table_name, columns in INDEXES:
        op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(INDEXES):
        op.drop_index(index_name, table_name=table_name)
