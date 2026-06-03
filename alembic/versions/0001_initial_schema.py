"""初始 schema：创建全部业务表。

Revision ID: 0001
Revises: None
Create Date: 2026-05-24

这是第一个 Alembic 迁移脚本，对应 mysql_schema.sql 中的全部表定义。
对于已有数据库，使用 `alembic stamp head` 标记为已迁移状态，而不是执行此脚本。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # stock_pools
    op.create_table(
        "stock_pools",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("pool_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
    )

    # stock_pool_members
    op.create_table(
        "stock_pool_members",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pool_code", sa.String(64), nullable=False, index=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("tags", sa.Text, nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
        sa.UniqueConstraint("pool_code", "symbol", name="uq_stock_pool_member"),
    )

    # stock_klines
    op.create_table(
        "stock_klines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("period", sa.String(32), nullable=False, index=True),
        sa.Column("trade_time", sa.String(64), nullable=False, index=True),
        sa.Column("open", sa.Float, nullable=True),
        sa.Column("high", sa.Float, nullable=True),
        sa.Column("low", sa.Float, nullable=True),
        sa.Column("close", sa.Float, nullable=True),
        sa.Column("volume", sa.Float, nullable=True),
        sa.Column("amount", sa.Float, nullable=True),
        sa.Column("change_pct", sa.Float, nullable=True),
        sa.Column("turnover_rate", sa.Float, nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="akshare"),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
        sa.UniqueConstraint("symbol", "period", "trade_time", name="uq_stock_kline"),
    )

    # kline_sync_logs
    op.create_table(
        "kline_sync_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("pool_code", sa.String(64), nullable=True, index=True),
        sa.Column("symbol", sa.String(32), nullable=True, index=True),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("rows_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
    )

    # stock_features
    op.create_table(
        "stock_features",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("period", sa.String(32), nullable=False, index=True),
        sa.Column("trade_time", sa.String(64), nullable=False, index=True),
        sa.Column("close", sa.Float, nullable=True),
        sa.Column("ma5", sa.Float, nullable=True),
        sa.Column("ma10", sa.Float, nullable=True),
        sa.Column("ma20", sa.Float, nullable=True),
        sa.Column("ma60", sa.Float, nullable=True),
        sa.Column("return_1", sa.Float, nullable=True),
        sa.Column("return_5", sa.Float, nullable=True),
        sa.Column("return_20", sa.Float, nullable=True),
        sa.Column("volatility_20", sa.Float, nullable=True),
        sa.Column("volume_ratio_5", sa.Float, nullable=True),
        sa.Column("price_position_20", sa.Float, nullable=True),
        sa.Column("price_position_60", sa.Float, nullable=True),
        sa.Column("trend_direction", sa.String(64), nullable=True),
        sa.Column("trend_score", sa.Float, nullable=True),
        sa.Column("signal", sa.String(64), nullable=True),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
        sa.UniqueConstraint("symbol", "period", "trade_time", name="uq_stock_feature"),
    )

    # paper_accounts
    op.create_table(
        "paper_accounts",
        sa.Column("account_id", sa.String(64), primary_key=True),
        sa.Column("initial_cash", sa.Float, nullable=False),
        sa.Column("cash", sa.Float, nullable=False),
        sa.Column("realized_pnl", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
    )

    # paper_positions
    op.create_table(
        "paper_positions",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("avg_price", sa.Float, nullable=False),
        sa.Column("opened_at", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
    )

    # paper_orders
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(128), nullable=False, unique=True),
        sa.Column("symbol", sa.String(32), nullable=False, index=True),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("strategy_mode", sa.String(32), nullable=True, index=True),
        sa.Column("decision_json", sa.Text, nullable=True),
        sa.Column("realized_pnl", sa.Float, nullable=True),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
    )

    # paper_cash_flows
    op.create_table(
        "paper_cash_flows",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(128), nullable=True, index=True),
        sa.Column("symbol", sa.String(32), nullable=True, index=True),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("cash_after", sa.Float, nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
    )


def downgrade() -> None:
    op.drop_table("paper_cash_flows")
    op.drop_table("paper_orders")
    op.drop_table("paper_positions")
    op.drop_table("paper_accounts")
    op.drop_table("stock_features")
    op.drop_table("kline_sync_logs")
    op.drop_table("stock_klines")
    op.drop_table("stock_pool_members")
    op.drop_table("stock_pools")
