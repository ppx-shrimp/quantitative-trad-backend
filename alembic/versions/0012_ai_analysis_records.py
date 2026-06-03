"""新增 AI 分析记录表。

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_analysis_records"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        existing_indexes = {index["name"] for index in inspector.get_indexes(TABLE_NAME)}
        _create_missing_indexes(existing_indexes)
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
        sa.Column("analysis_id", sa.String(length=128), nullable=False, comment="AI 分析唯一 ID"),
        sa.Column("symbol", sa.String(length=32), nullable=False, comment="股票代码"),
        sa.Column("analysis_type", sa.String(length=64), nullable=False, comment="分析类型：buy_decision / position_review / risk_review"),
        sa.Column("action", sa.String(length=32), nullable=True, comment="AI 建议动作"),
        sa.Column("confidence", sa.Float(), nullable=True, comment="置信度 0-1"),
        sa.Column("risk_level", sa.String(length=32), nullable=True, comment="风险等级 low/medium/high"),
        sa.Column("model_provider", sa.String(length=64), nullable=True, comment="模型提供方"),
        sa.Column("model_name", sa.String(length=128), nullable=True, comment="模型名称"),
        sa.Column("prompt_version", sa.String(length=32), nullable=False, comment="提示词版本"),
        sa.Column("status", sa.String(length=32), nullable=False, comment="分析状态 success/failed"),
        sa.Column("input_json", sa.Text(), nullable=True, comment="请求参数 JSON"),
        sa.Column("context_json", sa.Text(), nullable=True, comment="分析上下文 JSON"),
        sa.Column("output_json", sa.Text(), nullable=True, comment="模型输出 JSON"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误信息"),
        sa.Column("created_at", sa.String(length=64), nullable=False, comment="创建时间 ISO 字符串"),
        sa.Column("updated_at", sa.String(length=64), nullable=False, comment="更新时间 ISO 字符串"),
        sa.Column("created_by", sa.String(length=64), nullable=False, comment="创建人"),
        sa.Column("updated_by", sa.String(length=64), nullable=False, comment="更新人"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_id"),
        comment="AI 决策辅助分析记录表",
    )
    _create_missing_indexes(set())


def downgrade() -> None:
    op.drop_index("ix_ai_analysis_type_status_created", table_name=TABLE_NAME)
    op.drop_index("ix_ai_analysis_symbol_created", table_name=TABLE_NAME)
    op.drop_index("ix_ai_analysis_status", table_name=TABLE_NAME)
    op.drop_index("ix_ai_analysis_risk_level", table_name=TABLE_NAME)
    op.drop_index("ix_ai_analysis_action", table_name=TABLE_NAME)
    op.drop_index("ix_ai_analysis_analysis_type", table_name=TABLE_NAME)
    op.drop_index("ix_ai_analysis_symbol", table_name=TABLE_NAME)
    op.drop_index("ix_ai_analysis_analysis_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)


def _create_missing_indexes(existing_indexes: set[str]) -> None:
    index_defs = {
        "ix_ai_analysis_analysis_id": ["analysis_id"],
        "ix_ai_analysis_symbol": ["symbol"],
        "ix_ai_analysis_analysis_type": ["analysis_type"],
        "ix_ai_analysis_action": ["action"],
        "ix_ai_analysis_risk_level": ["risk_level"],
        "ix_ai_analysis_status": ["status"],
        "ix_ai_analysis_symbol_created": ["symbol", "created_at"],
        "ix_ai_analysis_type_status_created": ["analysis_type", "status", "created_at"],
    }
    for index_name, columns in index_defs.items():
        if index_name not in existing_indexes:
            op.create_index(index_name, TABLE_NAME, columns)
