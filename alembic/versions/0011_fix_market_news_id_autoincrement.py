"""修复 market_news 自增主键。

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MARKET_NEWS_TABLE_COMMENT = "市场新闻公告资讯表"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute(
            "ALTER TABLE market_news MODIFY COLUMN id INT NOT NULL AUTO_INCREMENT COMMENT '自增主键'"
        )
        op.execute(f"ALTER TABLE market_news COMMENT = '{MARKET_NEWS_TABLE_COMMENT}'")
        return

    op.alter_column(
        "market_news",
        "id",
        existing_type=sa.Integer(),
        nullable=False,
        autoincrement=True,
        comment="自增主键",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        op.execute("ALTER TABLE market_news MODIFY COLUMN id INT NOT NULL COMMENT '自增主键'")
        return

    op.alter_column(
        "market_news",
        "id",
        existing_type=sa.Integer(),
        nullable=False,
        autoincrement=False,
        comment="自增主键",
    )
