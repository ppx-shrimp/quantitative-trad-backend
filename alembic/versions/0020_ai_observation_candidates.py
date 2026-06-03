"""新增 AI 观察池候选表。

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "ai_observation_candidates"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.String(length=128), nullable=False),
        sa.Column("scan_id", sa.String(length=128), nullable=False),
        sa.Column("dedupe_key", sa.String(length=256), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("stock_name", sa.String(length=128), nullable=True),
        sa.Column("pool_code", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("recommendation_score", sa.Float(), nullable=True),
        sa.Column("ai_action", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("reasons_json", sa.Text(), nullable=True),
        sa.Column("risk_notes_json", sa.Text(), nullable=True),
        sa.Column("suggested_next_step", sa.Text(), nullable=True),
        sa.Column("trigger_price", sa.Float(), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("analysis_id", sa.String(length=128), nullable=True),
        sa.Column("linked_order_id", sa.String(length=128), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("last_reviewed_at", sa.String(length=64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_ai_observation_candidates_dedupe_key"),
    )
    op.create_index(op.f("ix_ai_observation_candidates_candidate_id"), TABLE_NAME, ["candidate_id"], unique=True)
    op.create_index("ix_ai_observation_symbol_status", TABLE_NAME, ["symbol", "status"])
    op.create_index("ix_ai_observation_pool_scan", TABLE_NAME, ["pool_code", "scan_id"])
    op.create_index("ix_ai_observation_status_score", TABLE_NAME, ["status", "recommendation_score"])
    op.create_index(op.f("ix_ai_observation_candidates_scan_id"), TABLE_NAME, ["scan_id"])
    op.create_index(op.f("ix_ai_observation_candidates_symbol"), TABLE_NAME, ["symbol"])
    op.create_index(op.f("ix_ai_observation_candidates_pool_code"), TABLE_NAME, ["pool_code"])
    op.create_index(op.f("ix_ai_observation_candidates_status"), TABLE_NAME, ["status"])
    op.create_index(op.f("ix_ai_observation_candidates_ai_action"), TABLE_NAME, ["ai_action"])
    op.create_index(op.f("ix_ai_observation_candidates_risk_level"), TABLE_NAME, ["risk_level"])
    op.create_index(op.f("ix_ai_observation_candidates_analysis_id"), TABLE_NAME, ["analysis_id"])
    op.create_index(op.f("ix_ai_observation_candidates_linked_order_id"), TABLE_NAME, ["linked_order_id"])
    op.create_index(op.f("ix_ai_observation_candidates_last_reviewed_at"), TABLE_NAME, ["last_reviewed_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    op.drop_table(TABLE_NAME)
