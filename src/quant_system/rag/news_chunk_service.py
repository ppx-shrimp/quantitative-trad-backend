from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from quant_system.core.config import settings
from quant_system.db.database import SessionLocal, engine, init_sqlalchemy_tables
from quant_system.db.models import MarketNewsModel, NewsRAGChunkModel


class NewsChunkService:
    """Chunk market_news rows into reusable RAG text chunks without requiring embeddings."""

    def __init__(self) -> None:
        init_sqlalchemy_tables()

    def chunk_recent_news(self, *, limit: int = 50, force_rechunk: bool = False) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        try:
            with SessionLocal() as session:
                rows = session.scalars(
                    select(MarketNewsModel)
                    .order_by(MarketNewsModel.published_at.desc(), MarketNewsModel.id.desc())
                    .limit(limit)
                ).all()
        except Exception as exc:
            return self._error_result("读取 market_news 失败", exc)
        return self.chunk_news_rows(rows, force_rechunk=force_rechunk)

    def chunk_news_by_id(self, news_id: str, *, force_rechunk: bool = False) -> dict[str, Any]:
        try:
            with SessionLocal() as session:
                row = session.scalar(select(MarketNewsModel).where(MarketNewsModel.news_id == news_id))
        except Exception as exc:
            return self._error_result("读取指定 market_news 失败", exc)
        if row is None:
            return {"ok": False, "error": f"未找到新闻：{news_id}", "chunk_count": 0, "items": []}
        return self.chunk_news_rows([row], force_rechunk=force_rechunk)

    def chunk_news_rows(self, rows: list[MarketNewsModel], *, force_rechunk: bool = False) -> dict[str, Any]:
        now = self._now()
        values: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in rows:
            chunks = self._build_chunks(row)
            if not chunks:
                skipped.append({"news_id": row.news_id, "title": row.title, "reason": "新闻文本为空"})
                continue
            for index, text in enumerate(chunks):
                chunk_id = self._chunk_id(row.news_id, index, text)
                content_hash = self._hash(text)
                metadata = self._metadata(row, index=index)
                values.append({
                    "chunk_id": chunk_id,
                    "news_id": row.news_id,
                    "chunk_index": index,
                    "content_hash": content_hash,
                    "text": text,
                    "text_preview": text[:240],
                    "token_estimate": self._token_estimate(text),
                    "embedding_model": settings.rag_embedding_model,
                    "vector_store": settings.rag_vector_backend,
                    "collection_name": settings.rag_collection_news,
                    "vector_id": chunk_id,
                    "metadata_json": json.dumps(metadata, ensure_ascii=False, default=str),
                    "created_at": now,
                    "updated_at": now,
                    "created_by": "system",
                    "updated_by": "system",
                })
        if not values:
            return {"ok": True, "news_count": len(rows), "chunk_count": 0, "saved_count": 0, "skipped": skipped, "items": []}
        try:
            with SessionLocal() as session:
                if force_rechunk:
                    news_ids = sorted({value["news_id"] for value in values})
                    session.execute(delete(NewsRAGChunkModel).where(NewsRAGChunkModel.news_id.in_(news_ids)))
                self._upsert_chunks(session, values)
                session.commit()
        except Exception as exc:
            return self._error_result("保存 news_rag_chunks 失败", exc, news_count=len(rows), chunk_count=len(values))
        return {
            "ok": True,
            "news_count": len(rows),
            "chunk_count": len(values),
            "saved_count": len(values),
            "skipped": skipped,
            "items": [self._public_item(value) for value in values[:20]],
        }

    def _build_chunks(self, row: MarketNewsModel) -> list[str]:
        header_parts = [
            f"标题：{row.title}" if row.title else "",
            f"来源：{row.source}" if row.source else "",
            f"时间：{row.published_at}" if row.published_at else "",
            f"摘要：{row.summary}" if row.summary else "",
            f"标签：{self._json_text(row.tags)}" if row.tags else "",
            f"相关板块：{self._json_text(row.related_sectors)}" if row.related_sectors else "",
            f"相关股票：{self._json_text(row.related_symbols)}" if row.related_symbols else "",
        ]
        header = "\n".join(part for part in header_parts if part)
        body = self._clean_text(row.content or row.summary or row.title or "")
        if not body:
            return []
        body_chunks = self._split_text(body, settings.rag_chunk_size, settings.rag_chunk_overlap)
        return [f"{header}\n正文片段：{chunk}".strip() for chunk in body_chunks]

    def _split_text(self, text: str, chunk_size: int, overlap: int) -> list[str]:
        clean = self._clean_text(text)
        if len(clean) <= chunk_size:
            return [clean]
        sentences = [item for item in re.split(r"(?<=[。！？!?；;])", clean) if item]
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            if len(current) + len(sentence) <= chunk_size:
                current += sentence
                continue
            if current:
                chunks.append(current)
            current = (current[-overlap:] if overlap and current else "") + sentence
            while len(current) > chunk_size:
                chunks.append(current[:chunk_size])
                current = current[max(0, chunk_size - overlap):]
        if current:
            chunks.append(current)
        return chunks

    def _upsert_chunks(self, session, values: list[dict[str, Any]]) -> None:
        dialect = engine.dialect.name
        if dialect == "mysql":
            stmt = mysql_insert(NewsRAGChunkModel).values(values)
            update_columns = {
                "content_hash": stmt.inserted.content_hash,
                "text": stmt.inserted.text,
                "text_preview": stmt.inserted.text_preview,
                "token_estimate": stmt.inserted.token_estimate,
                "embedding_model": stmt.inserted.embedding_model,
                "vector_store": stmt.inserted.vector_store,
                "collection_name": stmt.inserted.collection_name,
                "vector_id": stmt.inserted.vector_id,
                "metadata_json": stmt.inserted.metadata_json,
                "updated_at": stmt.inserted.updated_at,
                "updated_by": stmt.inserted.updated_by,
            }
            session.execute(stmt.on_duplicate_key_update(**update_columns))
            return

        stmt = sqlite_insert(NewsRAGChunkModel).values(values)
        update_columns = {
            "content_hash": stmt.excluded.content_hash,
            "text": stmt.excluded.text,
            "text_preview": stmt.excluded.text_preview,
            "token_estimate": stmt.excluded.token_estimate,
            "embedding_model": stmt.excluded.embedding_model,
            "vector_store": stmt.excluded.vector_store,
            "collection_name": stmt.excluded.collection_name,
            "vector_id": stmt.excluded.vector_id,
            "metadata_json": stmt.excluded.metadata_json,
            "updated_at": stmt.excluded.updated_at,
            "updated_by": stmt.excluded.updated_by,
        }
        session.execute(stmt.on_conflict_do_update(index_elements=["chunk_id"], set_=update_columns))

    def _metadata(self, row: MarketNewsModel, *, index: int) -> dict[str, Any]:
        return {
            "news_id": row.news_id,
            "chunk_index": index,
            "title": row.title,
            "source": row.source,
            "news_type": row.news_type,
            "published_at": row.published_at,
            "related_symbols": self._json_list(row.related_symbols),
            "related_sectors": self._json_list(row.related_sectors),
            "tags": self._json_list(row.tags),
            "sentiment": row.sentiment,
            "importance": row.importance,
        }

    def _public_item(self, value: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunk_id": value["chunk_id"],
            "news_id": value["news_id"],
            "chunk_index": value["chunk_index"],
            "text_preview": value["text_preview"],
            "token_estimate": value["token_estimate"],
            "vector_id": value["vector_id"],
        }

    def _error_result(self, message: str, exc: Exception, **extra: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "error": f"{message}：{exc}",
            "hint": "请确认已执行 alembic upgrade head，news_rag_chunks 表已创建，并检查后端控制台完整异常。",
            "items": [],
            **extra,
        }

    def _json_list(self, value: str | None) -> list[Any]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return [value]

    def _json_text(self, value: str | None) -> str:
        items = self._json_list(value)
        return "、".join(str(item) for item in items if item)

    def _clean_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip()

    def _token_estimate(self, text: str) -> int:
        return max(1, len(text) // 2)

    def _chunk_id(self, news_id: str, index: int, text: str) -> str:
        return f"news_{news_id}_{index}_{self._hash(text)[:12]}"

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
