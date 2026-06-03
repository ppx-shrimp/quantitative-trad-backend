"""新增任务执行记录表。

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_execution_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("execution_id", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("task_name", sa.String(128), nullable=False, index=True),
        sa.Column("task_type", sa.String(64), nullable=False, index=True),
        sa.Column("trigger_type", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column("started_at", sa.String(64), nullable=False, index=True),
        sa.Column("finished_at", sa.String(64), nullable=True, index=True),
        sa.Column("duration_ms", sa.Float, nullable=True),
        sa.Column("params_json", sa.Text, nullable=True),
        sa.Column("result_summary_json", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("candidate_count", sa.Integer, nullable=True),
        sa.Column("success_count", sa.Integer, nullable=True),
        sa.Column("failed_count", sa.Integer, nullable=True),
        sa.Column("accepted_count", sa.Integer, nullable=True),
        sa.Column("rejected_count", sa.Integer, nullable=True),
        sa.Column("order_count", sa.Integer, nullable=True),
        sa.Column("created_at", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="system"),
    )


def downgrade() -> None:
    op.drop_table("task_execution_records")
