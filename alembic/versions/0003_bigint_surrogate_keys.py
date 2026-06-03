"""将 stock_basic 和 stock_pools 改为 bigint 自增主键。

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL 当前线上表已用业务字符串字段做主键；这里切换为技术主键 id，
    # 并保留业务字段唯一约束，避免影响现有 API 按 code/ts_code 使用。
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("stock_basic") as batch_op:
            batch_op.add_column(sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True))
            batch_op.create_unique_constraint("uq_stock_basic_ts_code", ["ts_code"])
        with op.batch_alter_table("stock_pools") as batch_op:
            batch_op.add_column(sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True))
            batch_op.create_unique_constraint("uq_stock_pools_code", ["code"])
        return

    op.execute(
        "ALTER TABLE stock_basic "
        "DROP PRIMARY KEY, "
        "ADD COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键' FIRST, "
        "ADD PRIMARY KEY (id), "
        "ADD UNIQUE KEY uq_stock_basic_ts_code (ts_code)"
    )

    op.execute(
        "ALTER TABLE stock_pools "
        "DROP PRIMARY KEY, "
        "ADD COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键' FIRST, "
        "ADD PRIMARY KEY (id), "
        "ADD UNIQUE KEY uq_stock_pools_code (code)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE stock_pools DROP INDEX uq_stock_pools_code")
    op.execute("ALTER TABLE stock_pools DROP PRIMARY KEY")
    op.execute("ALTER TABLE stock_pools DROP COLUMN id")
    op.execute("ALTER TABLE stock_pools ADD PRIMARY KEY (code)")

    op.execute("ALTER TABLE stock_basic DROP INDEX uq_stock_basic_ts_code")
    op.execute("ALTER TABLE stock_basic DROP PRIMARY KEY")
    op.execute("ALTER TABLE stock_basic DROP COLUMN id")
    op.execute("ALTER TABLE stock_basic ADD PRIMARY KEY (ts_code)")
