"""扩展 AI 分析记录风控硬约束字段。

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_analysis_records"
RISK_CONSTRAINT_COLUMNS = {
    "risk_constraint_triggered": sa.Column("risk_constraint_triggered", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否触发风控硬约束"),
    "risk_forced_action": sa.Column("risk_forced_action", sa.String(length=32), nullable=True, comment="风控硬约束最终强制动作"),
    "risk_original_action": sa.Column("risk_original_action", sa.String(length=32), nullable=True, comment="模型原始建议动作"),
    "risk_trigger_message": sa.Column("risk_trigger_message", sa.Text(), nullable=True, comment="风控触发原因"),
    "risk_original_confidence": sa.Column("risk_original_confidence", sa.Float(), nullable=True, comment="模型原始置信度"),
    "risk_final_confidence": sa.Column("risk_final_confidence", sa.Float(), nullable=True, comment="风控覆盖后最终置信度"),
    "risk_constraint_json": sa.Column("risk_constraint_json", sa.Text(), nullable=True, comment="风控硬约束完整结构"),
}
INDEXES = {
    "ix_ai_analysis_risk_constraint_triggered": ["risk_constraint_triggered"],
    "ix_ai_analysis_risk_forced_action": ["risk_forced_action"],
    "ix_ai_analysis_risk_original_action": ["risk_original_action"],
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column_name, column in RISK_CONSTRAINT_COLUMNS.items():
            if column_name not in existing_columns:
                batch_op.add_column(column)

    existing_indexes = {index["name"] for index in inspector.get_indexes(TABLE_NAME)}
    for index_name, columns in INDEXES.items():
        if index_name not in existing_indexes:
            op.create_index(index_name, TABLE_NAME, columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes(TABLE_NAME)}
    for index_name in reversed(list(INDEXES.keys())):
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=TABLE_NAME)

    existing_columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column_name in reversed(list(RISK_CONSTRAINT_COLUMNS.keys())):
            if column_name in existing_columns:
                batch_op.drop_column(column_name)
