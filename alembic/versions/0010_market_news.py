"""新增真实资讯基础数据表。

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MARKET_NEWS_TABLE_COMMENT = "市场新闻公告资讯表"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "market_news" in inspector.get_table_names():
        _apply_market_news_comments()
        existing_indexes = {index["name"] for index in inspector.get_indexes("market_news")}
        if "ix_market_news_news_id" not in existing_indexes:
            op.create_index("ix_market_news_news_id", "market_news", ["news_id"])
        if "ix_market_news_title" not in existing_indexes:
            op.create_index("ix_market_news_title", "market_news", ["title"])
        if "ix_market_news_source" not in existing_indexes:
            op.create_index("ix_market_news_source", "market_news", ["source"])
        if "ix_market_news_news_type" not in existing_indexes:
            op.create_index("ix_market_news_news_type", "market_news", ["news_type"])
        if "ix_market_news_published_at" not in existing_indexes:
            op.create_index("ix_market_news_published_at", "market_news", ["published_at"])
        if "ix_market_news_fetched_at" not in existing_indexes:
            op.create_index("ix_market_news_fetched_at", "market_news", ["fetched_at"])
        if "ix_market_news_sentiment" not in existing_indexes:
            op.create_index("ix_market_news_sentiment", "market_news", ["sentiment"])
        if "ix_market_news_type_published" not in existing_indexes:
            op.create_index("ix_market_news_type_published", "market_news", ["news_type", "published_at"])
        if "ix_market_news_source_published" not in existing_indexes:
            op.create_index("ix_market_news_source_published", "market_news", ["source", "published_at"])
        return

    op.create_table(
        "market_news",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
        sa.Column("news_id", sa.String(length=128), nullable=False, comment="资讯唯一 ID，默认取 fingerprint 前 32 位"),
        sa.Column("fingerprint", sa.String(length=128), nullable=False, comment="去重指纹：source + news_type + title + published_at + url"),
        sa.Column("title", sa.String(length=512), nullable=False, comment="资讯标题"),
        sa.Column("summary", sa.Text(), nullable=True, comment="资讯摘要"),
        sa.Column("content", sa.Text(), nullable=True, comment="资讯正文内容，来源不提供时为空"),
        sa.Column("url", sa.Text(), nullable=True, comment="原文链接"),
        sa.Column("source", sa.String(length=64), nullable=False, comment="资讯来源，例如 eastmoney-akshare / akshare-notice"),
        sa.Column("news_type", sa.String(length=32), nullable=False, comment="资讯类型：news 新闻 / notice 公告"),
        sa.Column("published_at", sa.String(length=64), nullable=False, comment="发布时间 ISO 字符串或来源原始时间字符串"),
        sa.Column("fetched_at", sa.String(length=64), nullable=False, comment="抓取入库时间 ISO 字符串"),
        sa.Column("related_symbols", sa.Text(), nullable=True, comment="关联股票代码 JSON 数组字符串"),
        sa.Column("related_sectors", sa.Text(), nullable=True, comment="关联板块 JSON 数组字符串"),
        sa.Column("tags", sa.Text(), nullable=True, comment="标签 JSON 数组字符串"),
        sa.Column("sentiment", sa.String(length=32), nullable=True, comment="情绪标签，预留字段"),
        sa.Column("importance", sa.Float(), nullable=True, comment="重要性分数，预留字段"),
        sa.Column("raw_json", sa.Text(), nullable=True, comment="来源原始记录 JSON"),
        sa.Column("created_at", sa.String(length=64), nullable=False, comment="创建时间 ISO 字符串"),
        sa.Column("updated_at", sa.String(length=64), nullable=False, comment="更新时间 ISO 字符串"),
        sa.Column("created_by", sa.String(length=64), nullable=False, comment="创建人"),
        sa.Column("updated_by", sa.String(length=64), nullable=False, comment="更新人"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_market_news_fingerprint"),
        sa.UniqueConstraint("news_id"),
        comment=MARKET_NEWS_TABLE_COMMENT,
    )
    op.create_index("ix_market_news_news_id", "market_news", ["news_id"])
    op.create_index("ix_market_news_title", "market_news", ["title"])
    op.create_index("ix_market_news_source", "market_news", ["source"])
    op.create_index("ix_market_news_news_type", "market_news", ["news_type"])
    op.create_index("ix_market_news_published_at", "market_news", ["published_at"])
    op.create_index("ix_market_news_fetched_at", "market_news", ["fetched_at"])
    op.create_index("ix_market_news_sentiment", "market_news", ["sentiment"])
    op.create_index("ix_market_news_type_published", "market_news", ["news_type", "published_at"])
    op.create_index("ix_market_news_source_published", "market_news", ["source", "published_at"])


def downgrade() -> None:
    op.drop_index("ix_market_news_source_published", table_name="market_news")
    op.drop_index("ix_market_news_type_published", table_name="market_news")
    op.drop_index("ix_market_news_sentiment", table_name="market_news")
    op.drop_index("ix_market_news_fetched_at", table_name="market_news")
    op.drop_index("ix_market_news_published_at", table_name="market_news")
    op.drop_index("ix_market_news_news_type", table_name="market_news")
    op.drop_index("ix_market_news_source", table_name="market_news")
    op.drop_index("ix_market_news_title", table_name="market_news")
    op.drop_index("ix_market_news_news_id", table_name="market_news")
    op.drop_table("market_news")


def _apply_market_news_comments() -> None:
    op.execute(f"ALTER TABLE market_news COMMENT = '{MARKET_NEWS_TABLE_COMMENT}'")
    op.alter_column("market_news", "id", existing_type=sa.Integer(), existing_nullable=False, comment="自增主键")
    op.alter_column("market_news", "news_id", existing_type=sa.String(length=128), existing_nullable=False, comment="资讯唯一 ID，默认取 fingerprint 前 32 位")
    op.alter_column("market_news", "fingerprint", existing_type=sa.String(length=128), existing_nullable=False, comment="去重指纹：source + news_type + title + published_at + url")
    op.alter_column("market_news", "title", existing_type=sa.String(length=512), existing_nullable=False, comment="资讯标题")
    op.alter_column("market_news", "summary", existing_type=sa.Text(), existing_nullable=True, comment="资讯摘要")
    op.alter_column("market_news", "content", existing_type=sa.Text(), existing_nullable=True, comment="资讯正文内容，来源不提供时为空")
    op.alter_column("market_news", "url", existing_type=sa.Text(), existing_nullable=True, comment="原文链接")
    op.alter_column("market_news", "source", existing_type=sa.String(length=64), existing_nullable=False, comment="资讯来源，例如 eastmoney-akshare / akshare-notice")
    op.alter_column("market_news", "news_type", existing_type=sa.String(length=32), existing_nullable=False, comment="资讯类型：news 新闻 / notice 公告")
    op.alter_column("market_news", "published_at", existing_type=sa.String(length=64), existing_nullable=False, comment="发布时间 ISO 字符串或来源原始时间字符串")
    op.alter_column("market_news", "fetched_at", existing_type=sa.String(length=64), existing_nullable=False, comment="抓取入库时间 ISO 字符串")
    op.alter_column("market_news", "related_symbols", existing_type=sa.Text(), existing_nullable=True, comment="关联股票代码 JSON 数组字符串")
    op.alter_column("market_news", "related_sectors", existing_type=sa.Text(), existing_nullable=True, comment="关联板块 JSON 数组字符串")
    op.alter_column("market_news", "tags", existing_type=sa.Text(), existing_nullable=True, comment="标签 JSON 数组字符串")
    op.alter_column("market_news", "sentiment", existing_type=sa.String(length=32), existing_nullable=True, comment="情绪标签，预留字段")
    op.alter_column("market_news", "importance", existing_type=sa.Float(), existing_nullable=True, comment="重要性分数，预留字段")
    op.alter_column("market_news", "raw_json", existing_type=sa.Text(), existing_nullable=True, comment="来源原始记录 JSON")
    op.alter_column("market_news", "created_at", existing_type=sa.String(length=64), existing_nullable=False, comment="创建时间 ISO 字符串")
    op.alter_column("market_news", "updated_at", existing_type=sa.String(length=64), existing_nullable=False, comment="更新时间 ISO 字符串")
    op.alter_column("market_news", "created_by", existing_type=sa.String(length=64), existing_nullable=False, comment="创建人")
    op.alter_column("market_news", "updated_by", existing_type=sa.String(length=64), existing_nullable=False, comment="更新人")
