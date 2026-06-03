"""新增高频查询组合索引。

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEXES = [
    (
        "ix_paper_orders_symbol_strategy_status_created",
        "paper_orders",
        ["symbol", "strategy_mode", "status", "created_at"],
    ),
    (
        "ix_paper_orders_strategy_status_created",
        "paper_orders",
        ["strategy_mode", "status", "created_at"],
    ),
    (
        "ix_paper_cash_flows_created",
        "paper_cash_flows",
        ["created_at"],
    ),
    (
        "ix_task_execution_records_task_status_started",
        "task_execution_records",
        ["task_name", "status", "started_at"],
    ),
    (
        "ix_task_execution_records_type_status_started",
        "task_execution_records",
        ["task_type", "status", "started_at"],
    ),
    (
        "ix_backtest_runs_scope_status_strategy_created",
        "backtest_runs",
        ["scope", "status", "strategy_mode", "created_at"],
    ),
    (
        "ix_backtest_runs_symbol_status_created",
        "backtest_runs",
        ["symbol", "status", "created_at"],
    ),
    (
        "ix_backtest_runs_pool_status_created",
        "backtest_runs",
        ["pool_code", "status", "created_at"],
    ),
    (
        "ix_backtest_trades_run_trade_time",
        "backtest_trades",
        ["run_id", "trade_time"],
    ),
    (
        "ix_backtest_equity_curve_run_trade_time",
        "backtest_equity_curve",
        ["run_id", "trade_time"],
    ),
    (
        "ix_kline_sync_logs_symbol_period_created",
        "kline_sync_logs",
        ["symbol", "period", "created_at"],
    ),
]


def upgrade() -> None:
    for index_name, table_name, columns in INDEXES:
        op.create_index(index_name, table_name, columns)


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(INDEXES):
        op.drop_index(index_name, table_name=table_name)
