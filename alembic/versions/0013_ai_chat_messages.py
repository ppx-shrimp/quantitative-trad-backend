"""新增 AI 多轮对话消息表。

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_chat_messages"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
        sa.Column("session_id", sa.String(length=128), nullable=False, comment="对话会话 ID"),
        sa.Column("analysis_id", sa.String(length=128), nullable=False, comment="关联的 AI 分析 ID"),
        sa.Column("seq", sa.Integer(), nullable=False, comment="消息在会话中的顺序号"),
        sa.Column("role", sa.String(length=32), nullable=False, comment="角色: user / assistant / system"),
        sa.Column("content", sa.Text(), nullable=False, comment="消息内容"),
        sa.Column("model_name", sa.String(length=128), nullable=True, comment="模型名称（仅 assistant 消息）"),
        sa.Column("token_count", sa.Integer(), nullable=True, comment="Token 数量估算"),
        sa.Column("created_at", sa.String(length=64), nullable=False, comment="创建时间 ISO 字符串"),
        sa.Column("updated_at", sa.String(length=64), nullable=False, comment="更新时间 ISO 字符串"),
        sa.Column("created_by", sa.String(length=64), nullable=False, comment="创建人"),
        sa.Column("updated_by", sa.String(length=64), nullable=False, comment="更新人"),
        sa.PrimaryKeyConstraint("id"),
        comment="AI 多轮对话消息记录表",
    )
    op.create_index("ix_ai_chat_session_id", TABLE_NAME, ["session_id"])
    op.create_index("ix_ai_chat_analysis_id", TABLE_NAME, ["analysis_id"])
    op.create_index("ix_ai_chat_session_seq", TABLE_NAME, ["session_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_ai_chat_session_seq", table_name=TABLE_NAME)
    op.drop_index("ix_ai_chat_analysis_id", table_name=TABLE_NAME)
    op.drop_index("ix_ai_chat_session_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
