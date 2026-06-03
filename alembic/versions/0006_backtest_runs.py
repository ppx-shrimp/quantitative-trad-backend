"""新增回测结果沉淀表。

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("pool_code", sa.String(length=64), nullable=True),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("strategy_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.String(length=32), nullable=True),
        sa.Column("end_date", sa.String(length=32), nullable=True),
        sa.Column("initial_cash", sa.Float(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("rows_count", sa.Integer(), nullable=True),
        sa.Column("tested_bars", sa.Integer(), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("round_trip_count", sa.Integer(), nullable=True),
        sa.Column("total_pnl", sa.Float(), nullable=True),
        sa.Column("total_pnl_pct", sa.Float(), nullable=True),
        sa.Column("final_equity", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("rule_json", sa.Text(), nullable=True),
        sa.Column("execution_rules_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_backtest_runs_run_id"),
    )
    op.create_index("ix_backtest_runs_run_id", "backtest_runs", ["run_id"])
    op.create_index("ix_backtest_runs_scope", "backtest_runs", ["scope"])
    op.create_index("ix_backtest_runs_symbol", "backtest_runs", ["symbol"])
    op.create_index("ix_backtest_runs_pool_code", "backtest_runs", ["pool_code"])
    op.create_index("ix_backtest_runs_period", "backtest_runs", ["period"])
    op.create_index("ix_backtest_runs_strategy_mode", "backtest_runs", ["strategy_mode"])
    op.create_index("ix_backtest_runs_status", "backtest_runs", ["status"])

    op.create_table(
        "backtest_trades",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("trade_time", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False, default=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("requested_price", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("total_fee", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_backtest_trades_run_id", "backtest_trades", ["run_id"])
    op.create_index("ix_backtest_trades_symbol", "backtest_trades", ["symbol"])
    op.create_index("ix_backtest_trades_trade_time", "backtest_trades", ["trade_time"])
    op.create_index("ix_backtest_trades_side", "backtest_trades", ["side"])

    op.create_table(
        "backtest_equity_curve",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("trade_time", sa.String(length=64), nullable=False),
        sa.Column("cash", sa.Float(), nullable=True),
        sa.Column("market_value", sa.Float(), nullable=True),
        sa.Column("equity", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_backtest_equity_curve_run_id", "backtest_equity_curve", ["run_id"])
    op.create_index("ix_backtest_equity_curve_symbol", "backtest_equity_curve", ["symbol"])
    op.create_index("ix_backtest_equity_curve_trade_time", "backtest_equity_curve", ["trade_time"])


def downgrade() -> None:
    op.drop_index("ix_backtest_equity_curve_trade_time", table_name="backtest_equity_curve")
    op.drop_index("ix_backtest_equity_curve_symbol", table_name="backtest_equity_curve")
    op.drop_index("ix_backtest_equity_curve_run_id", table_name="backtest_equity_curve")
    op.drop_table("backtest_equity_curve")

    op.drop_index("ix_backtest_trades_side", table_name="backtest_trades")
    op.drop_index("ix_backtest_trades_trade_time", table_name="backtest_trades")
    op.drop_index("ix_backtest_trades_symbol", table_name="backtest_trades")
    op.drop_index("ix_backtest_trades_run_id", table_name="backtest_trades")
    op.drop_table("backtest_trades")

    op.drop_index("ix_backtest_runs_status", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_strategy_mode", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_period", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_pool_code", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_symbol", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_scope", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_run_id", table_name="backtest_runs")
    op.drop_table("backtest_runs")
