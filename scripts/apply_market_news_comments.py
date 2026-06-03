from __future__ import annotations

from sqlalchemy import create_engine, text

from quant_system.core.config import settings


TABLE_COMMENT = "市场新闻公告资讯表"

COLUMN_COMMENTS = {
    "id": "自增主键",
    "news_id": "资讯唯一 ID，默认取 fingerprint 前 32 位",
    "fingerprint": "去重指纹：source + news_type + title + published_at + url",
    "title": "资讯标题",
    "summary": "资讯摘要",
    "content": "资讯正文内容，来源不提供时为空",
    "url": "原文链接",
    "source": "资讯来源，例如 eastmoney-akshare / akshare-notice",
    "news_type": "资讯类型：news 新闻 / notice 公告",
    "published_at": "发布时间 ISO 字符串或来源原始时间字符串",
    "fetched_at": "抓取入库时间 ISO 字符串",
    "related_symbols": "关联股票代码 JSON 数组字符串",
    "related_sectors": "关联板块 JSON 数组字符串",
    "tags": "标签 JSON 数组字符串",
    "sentiment": "情绪标签，预留字段",
    "importance": "重要性分数，预留字段",
    "raw_json": "来源原始记录 JSON",
    "created_at": "创建时间 ISO 字符串",
    "updated_at": "更新时间 ISO 字符串",
    "created_by": "创建人",
    "updated_by": "更新人",
}


def main() -> None:
    if not settings.database_url:
        raise RuntimeError("未配置 QUANT_DATABASE_URL，无法更新 MySQL 注释。")

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE market_news COMMENT = '{TABLE_COMMENT}'"))
        rows = conn.execute(
            text(
                """
                SELECT column_name, column_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = DATABASE() AND table_name = 'market_news'
                ORDER BY ordinal_position
                """
            )
        ).fetchall()

        for column_name, column_type, is_nullable in rows:
            comment = COLUMN_COMMENTS.get(column_name)
            if not comment:
                continue
            nullable_sql = "NULL" if str(is_nullable).upper() == "YES" else "NOT NULL"
            conn.execute(
                text(
                    f"ALTER TABLE market_news MODIFY COLUMN `{column_name}` {column_type} {nullable_sql} COMMENT :comment"
                ),
                {"comment": comment},
            )
    print("market_news comments applied")


if __name__ == "__main__":
    main()
