"""扩展 AI 分析记录结构化执行计划字段。

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_analysis_records"
PLAN_COLUMNS = {
    "plan_execution": "建议执行方式",
    "plan_position_size": "仓位建议",
    "plan_entry_condition": "入场/加仓条件",
    "plan_watch_condition": "观察信号",
    "plan_stop_loss": "止损参考",
    "plan_take_profit": "止盈参考",
    "plan_invalid_condition": "判断失效条件",
    "plan_review_time": "建议复盘时间",
    "plan_next_step": "下一步操作建议",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column_name, comment in PLAN_COLUMNS.items():
            if column_name not in existing_columns:
                batch_op.add_column(sa.Column(column_name, sa.Text(), nullable=True, comment=comment))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column_name in reversed(list(PLAN_COLUMNS.keys())):
            if column_name in existing_columns:
                batch_op.drop_column(column_name)
