"""新增新闻 RAG chunk 映射表。

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "news_rag_chunks"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chunk_id", sa.String(length=128), nullable=False),
        sa.Column("news_id", sa.String(length=128), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_preview", sa.String(length=512), nullable=True),
        sa.Column("token_estimate", sa.Integer(), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("vector_store", sa.String(length=64), nullable=True),
        sa.Column("collection_name", sa.String(length=128), nullable=True),
        sa.Column("vector_id", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", name="uq_news_rag_chunks_chunk_id"),
        sa.UniqueConstraint("news_id", "chunk_index", name="uq_news_rag_chunks_news_index"),
    )
    op.create_index(op.f("ix_news_rag_chunks_chunk_id"), TABLE_NAME, ["chunk_id"], unique=True)
    op.create_index(op.f("ix_news_rag_chunks_news_id"), TABLE_NAME, ["news_id"])
    op.create_index(op.f("ix_news_rag_chunks_content_hash"), TABLE_NAME, ["content_hash"])
    op.create_index(op.f("ix_news_rag_chunks_vector_id"), TABLE_NAME, ["vector_id"])
    op.create_index("ix_news_rag_chunks_vector", TABLE_NAME, ["vector_store", "collection_name"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return
    op.drop_table(TABLE_NAME)
