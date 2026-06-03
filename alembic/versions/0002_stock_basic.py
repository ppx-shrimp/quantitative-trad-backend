"""新增股票基础信息表。

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_basic",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts_code", sa.String(32), nullable=False, unique=True, index=True),
        sa.Column("symbol", sa.String(16), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False, index=True),
        sa.Column("area", sa.String(64), nullable=True),
        sa.Column("industry", sa.String(128), nullable=True),
        sa.Column("market", sa.String(64), nullable=True),
        sa.Column("exchange", sa.String(16), nullable=True, index=True),
        sa.Column("list_date", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("1"), index=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="tushare"),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
    )


def downgrade() -> None:
    op.drop_table("stock_basic")
