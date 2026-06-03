"""扩展 AI 分析记录关联订单字段。

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_analysis_records"
LINKED_ORDER_COLUMNS = {
    "linked_order_id": sa.Column("linked_order_id", sa.String(length=128), nullable=True, comment="关联模拟订单 ID"),
    "linked_order_status": sa.Column("linked_order_status", sa.String(length=32), nullable=True, comment="关联订单状态"),
    "linked_order_side": sa.Column("linked_order_side", sa.String(length=16), nullable=True, comment="关联订单方向"),
    "linked_order_quantity": sa.Column("linked_order_quantity", sa.Integer(), nullable=True, comment="关联订单数量"),
    "linked_order_price": sa.Column("linked_order_price", sa.Float(), nullable=True, comment="关联订单成交价"),
    "linked_order_at": sa.Column("linked_order_at", sa.String(length=64), nullable=True, comment="关联订单时间"),
    "linked_order_json": sa.Column("linked_order_json", sa.Text(), nullable=True, comment="关联订单摘要 JSON"),
}
INDEXES = {
    "ix_ai_analysis_linked_order_id": ["linked_order_id"],
    "ix_ai_analysis_linked_order_status": ["linked_order_status"],
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(TABLE_NAME)}
    with op.batch_alter_table(TABLE_NAME) as batch_op:
        for column_name, column in LINKED_ORDER_COLUMNS.items():
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
        for column_name in reversed(list(LINKED_ORDER_COLUMNS.keys())):
            if column_name in existing_columns:
                batch_op.drop_column(column_name)
