"""模拟交易增加成交价、滑点和交易成本字段。

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("paper_orders", sa.Column("requested_price", sa.Float(), nullable=True))
    op.add_column("paper_orders", sa.Column("gross_amount", sa.Float(), nullable=True))
    op.add_column("paper_orders", sa.Column("commission", sa.Float(), nullable=True))
    op.add_column("paper_orders", sa.Column("stamp_duty", sa.Float(), nullable=True))
    op.add_column("paper_orders", sa.Column("transfer_fee", sa.Float(), nullable=True))
    op.add_column("paper_orders", sa.Column("total_fee", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_orders", "total_fee")
    op.drop_column("paper_orders", "transfer_fee")
    op.drop_column("paper_orders", "stamp_duty")
    op.drop_column("paper_orders", "commission")
    op.drop_column("paper_orders", "gross_amount")
    op.drop_column("paper_orders", "requested_price")
