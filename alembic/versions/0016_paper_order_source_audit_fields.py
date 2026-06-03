"""扩展模拟订单来源审计字段。

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "paper_orders"
SOURCE_AUDIT_COLUMNS = {
    "source_type": sa.Column("source_type", sa.String(length=64), nullable=True, comment="订单来源类型，如 risk_warning / ai_analysis / manual"),
    "source_id": sa.Column("source_id", sa.String(length=128), nullable=True, comment="来源记录 ID，如 AI 分析 ID"),
    "source_action": sa.Column("source_action", sa.String(length=32), nullable=True, comment="来源建议动作"),
    "source_confidence": sa.Column("source_confidence", sa.Float(), nullable=True, comment="来源建议置信度"),
    "source_memo": sa.Column("source_memo", sa.Text(), nullable=True, comment="来源摘要/风控说明"),
    "audit_json": sa.Column("audit_json", sa.Text(), nullable=True, comment="订单来源审计完整结构"),
}
INDEXES = {
    "ix_paper_orders_source_type": ["source_type"],
    "ix_paper_orders_source_id": ["source_id"],
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column_name, column in SOURCE_AUDIT_COLUMNS.items():
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
        for column_name in reversed(list(SOURCE_AUDIT_COLUMNS.keys())):
            if column_name in existing_columns:
                batch_op.drop_column(column_name)
