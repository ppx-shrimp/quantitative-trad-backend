"""AI 观察池持续跟踪字段。

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_observation_candidates"


def _has_column(inspector, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(TABLE_NAME))


def _has_index(inspector, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(TABLE_NAME))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    columns = [
        ("tracking_json", sa.Text(), True),
        ("last_tracked_at", sa.String(length=64), True),
        ("next_check_at", sa.String(length=64), True),
        ("trigger_reason", sa.Text(), True),
        ("status_changed_at", sa.String(length=64), True),
    ]
    for name, column_type, nullable in columns:
        if not _has_column(inspector, name):
            op.add_column(TABLE_NAME, sa.Column(name, column_type, nullable=nullable))

    inspector = inspect(bind)
    for name in ["last_tracked_at", "next_check_at", "status_changed_at"]:
        index_name = op.f(f"ix_ai_observation_candidates_{name}")
        if not _has_index(inspector, index_name):
            op.create_index(index_name, TABLE_NAME, [name])
    if not _has_index(inspector, "ix_ai_observation_next_check"):
        op.create_index("ix_ai_observation_next_check", TABLE_NAME, ["status", "next_check_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    if _has_index(inspector, "ix_ai_observation_next_check"):
        op.drop_index("ix_ai_observation_next_check", table_name=TABLE_NAME)
    for name in ["last_tracked_at", "next_check_at", "status_changed_at"]:
        index_name = op.f(f"ix_ai_observation_candidates_{name}")
        if _has_index(inspector, index_name):
            op.drop_index(index_name, table_name=TABLE_NAME)
    inspector = inspect(bind)
    for name in ["status_changed_at", "trigger_reason", "next_check_at", "last_tracked_at", "tracking_json"]:
        if _has_column(inspector, name):
            op.drop_column(TABLE_NAME, name)
