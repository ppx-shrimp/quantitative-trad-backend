from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from quant_system.core.config import settings
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import NewsRAGChunkModel
from quant_system.rag.embedding_client import OpenAICompatibleEmbeddingClient
from quant_system.rag.news_semantic_service import NewsSemanticService
from quant_system.rag.qdrant_store import QdrantVectorStore
from quant_system.rag.vector_store import VectorDocument


class NewsEmbeddingService:
    """Embed saved news chunks and upsert them into the configured vector store."""

    def __init__(self) -> None:
        init_sqlalchemy_tables()
        self.embedding_client = OpenAICompatibleEmbeddingClient()
        self.vector_store = QdrantVectorStore()
        self.semantic_service = NewsSemanticService()

    def embed_news_chunks(
        self,
        *,
        limit: int = 50,
        news_id: str | None = None,
        force_reembed: bool = False,
    ) -> dict[str, Any]:
        if not settings.rag_enabled:
            return {"ok": False, "error": "RAG 未启用，请设置 QUANT_RAG_ENABLED=true。", "items": []}
        if settings.rag_vector_backend != "qdrant":
            return {"ok": False, "error": "第一期 MVP 仅实现 qdrant。", "items": []}
        collection_result = self._ensure_collection()
        if not collection_result.get("ok"):
            return {**collection_result, "items": []}
        if not self.embedding_client.enabled():
            return {
                "ok": False,
                "error": "RAG embedding 未启用或配置不完整。",
                "embedding": self.embedding_client.status(),
                "items": [],
            }

        limit = max(1, min(int(limit or 50), 200))
        with SessionLocal() as session:
            if news_id:
                stmt = select(NewsRAGChunkModel).where(NewsRAGChunkModel.news_id == news_id).order_by(NewsRAGChunkModel.chunk_index.asc()).limit(limit)
                chunks = session.scalars(stmt).all()
            elif force_reembed:
                chunks = session.scalars(select(NewsRAGChunkModel).order_by(NewsRAGChunkModel.id.asc()).limit(limit)).all()
            else:
                all_rows = session.scalars(select(NewsRAGChunkModel).order_by(NewsRAGChunkModel.id.asc())).all()
                chunks = [row for row in all_rows if not self._is_current_embedded(row)][:limit]

        if not chunks:
            stats = self.chunk_stats()
            message = "没有需要向量化的新闻 chunk。"
            if not stats.get("qdrant_count_matches_db", True):
                message = "没有待处理 chunk，但 DB 已向量化数量大于 Qdrant points_count；请点击“强制重建向量”。"
            return {
                "ok": True,
                "selected_count": 0,
                "embedded_count": 0,
                "upserted_count": 0,
                "items": [],
                "message": message,
                "stats": stats,
            }

        skip_limit = int(settings.rag_skip_embedding_if_pending_over or 0)
        if skip_limit > 0 and not force_reembed and len(chunks) > skip_limit:
            return {
                "ok": False,
                "status": "skipped_by_cost_guard",
                "selected_count": len(chunks),
                "embedded_count": 0,
                "upserted_count": 0,
                "error": f"待向量化 chunk {len(chunks)} 条超过成本保护阈值 {skip_limit}，请降低 limit、分批执行，或设置 force_reembed=true/调整 QUANT_RAG_SKIP_EMBEDDING_IF_PENDING_OVER。",
                "items": [self._public_item(chunk) for chunk in chunks[:20]],
            }

        batch_size = max(1, min(int(settings.rag_embedding_batch_size or 10), 128))
        batch_results: list[dict[str, Any]] = []
        total_embedded = 0
        total_upserted = 0
        dimensions: list[int] = []
        failed_batches: list[dict[str, Any]] = []

        for batch_index, start in enumerate(range(0, len(chunks), batch_size), start=1):
            batch_chunks = chunks[start:start + batch_size]
            batch_result = self._embed_and_upsert_batch(batch_chunks)
            batch_result["batch_index"] = batch_index
            batch_result["offset"] = start
            batch_results.append(batch_result)
            total_embedded += int(batch_result.get("embedded_count") or 0)
            total_upserted += int(batch_result.get("upserted_count") or 0)
            if batch_result.get("dimension"):
                dimensions.append(int(batch_result["dimension"]))
            if not batch_result.get("ok"):
                failed_batches.append({
                    "batch_index": batch_index,
                    "offset": start,
                    "chunk_count": len(batch_chunks),
                    "error": batch_result.get("error"),
                    "chunk_ids": [chunk.chunk_id for chunk in batch_chunks[:10]],
                })

        status = "success" if not failed_batches else ("partial_success" if total_upserted > 0 else "failed")
        return {
            "ok": not failed_batches,
            "status": status,
            "selected_count": len(chunks),
            "embedded_count": total_embedded,
            "upserted_count": total_upserted,
            "failed_batch_count": len(failed_batches),
            "failed_batches": failed_batches,
            "dimension": dimensions[0] if dimensions else 0,
            "embedding": {
                "model": settings.rag_embedding_model,
                "batch_size": batch_size,
                "batches": len(batch_results),
                "successful_batches": len(batch_results) - len(failed_batches),
                "failed_batches": len(failed_batches),
                "count": total_embedded,
            },
            "collection": settings.rag_collection_news,
            "batch_results": batch_results,
            "items": [self._public_item(chunk) for chunk in chunks[:20]],
            "message": "新闻 chunk 向量化完成。" if not failed_batches else "部分批次向量化失败；成功批次已写入 DB/Qdrant，下次可继续处理剩余 chunk。",
        }

    def _embed_and_upsert_batch(self, chunks: list[NewsRAGChunkModel]) -> dict[str, Any]:
        texts = [chunk.text for chunk in chunks]
        try:
            embedding_result = self.embedding_client.embed_texts_batched(texts, batch_size=len(texts))
            vectors = embedding_result["vectors"]
        except Exception as exc:
            return {
                "ok": False,
                "selected_count": len(chunks),
                "embedded_count": 0,
                "upserted_count": 0,
                "error": str(exc),
                "items": [self._public_item(chunk) for chunk in chunks[:10]],
            }

        documents = []
        vector_ids: dict[str, str] = {}
        now = self._now()
        for chunk, vector in zip(chunks, vectors, strict=False):
            metadata = self._metadata(chunk)
            vector_id = self._vector_point_id(chunk)
            vector_ids[chunk.chunk_id] = vector_id
            documents.append(VectorDocument(
                id=vector_id,
                text=chunk.text,
                vector=vector,
                metadata=metadata,
            ))

        try:
            upsert_result = self.vector_store.upsert_documents(documents)
        except Exception as exc:
            return {
                "ok": False,
                "selected_count": len(chunks),
                "embedded_count": len(vectors),
                "upserted_count": 0,
                "dimension": embedding_result.get("dimension") or (len(vectors[0]) if vectors else 0),
                "error": str(exc),
                "items": [self._public_item(chunk) for chunk in chunks[:10]],
            }

        with SessionLocal() as session:
            for chunk in chunks[:len(documents)]:
                row = session.scalar(select(NewsRAGChunkModel).where(NewsRAGChunkModel.chunk_id == chunk.chunk_id))
                if row is None:
                    continue
                row.embedding_model = settings.rag_embedding_model
                row.vector_store = settings.rag_vector_backend
                row.collection_name = settings.rag_collection_news
                row.vector_id = vector_ids.get(chunk.chunk_id) or self._vector_point_id(chunk)
                row.updated_at = now
                row.updated_by = "system"
            session.commit()

        return {
            "ok": True,
            "selected_count": len(chunks),
            "embedded_count": len(vectors),
            "upserted_count": len(documents),
            "dimension": embedding_result.get("dimension") or (len(vectors[0]) if vectors else 0),
            "embedding": {
                "model": embedding_result.get("model"),
                "batches": embedding_result.get("batches"),
                "count": embedding_result.get("count"),
            },
            "upsert": upsert_result,
            "items": [self._public_item(chunk) for chunk in chunks[:10]],
        }

    def search_news_chunks(
        self,
        *,
        query: str,
        limit: int | None = None,
        score_threshold: float | None = None,
        related_symbol: str | None = None,
        related_sector: str | None = None,
        sentiment: str | None = None,
        dedupe_by_news: bool = True,
    ) -> dict[str, Any]:
        if not settings.rag_enabled:
            return {"ok": False, "error": "RAG 未启用，请设置 QUANT_RAG_ENABLED=true。", "items": []}
        clean_query = query.strip()
        if not clean_query:
            return {"ok": False, "error": "query 不能为空", "items": []}
        search_limit = max(1, min(int(limit or settings.rag_search_limit), 20))
        fetch_limit = min(search_limit * 3, 60) if dedupe_by_news or score_threshold is not None else search_limit
        filters = self._search_filters(
            related_symbol=related_symbol,
            related_sector=related_sector,
            sentiment=sentiment,
        )
        try:
            vector = self.embedding_client.embed_text(clean_query)
            results = self.vector_store.search(vector, limit=fetch_limit, filters=filters)
        except Exception as exc:
            return {"ok": False, "query": clean_query, "error": str(exc), "items": []}

        items = []
        seen_news_ids = set()
        threshold = self._score_threshold(score_threshold)
        for result in results:
            if threshold is not None and result.score < threshold:
                continue
            metadata = result.metadata or {}
            news_id = metadata.get("news_id") or result.id
            if dedupe_by_news and news_id in seen_news_ids:
                continue
            seen_news_ids.add(news_id)
            item = self._search_item(result, metadata, clean_query)
            items.append(item)
            if len(items) >= search_limit:
                break
        return {
            "ok": True,
            "query": clean_query,
            "count": len(items),
            "searched_count": len(results),
            "filters": filters,
            "score_threshold": threshold,
            "dedupe_by_news": dedupe_by_news,
            "items": items,
        }

    def build_retrieval_context(
        self,
        *,
        query: str,
        limit: int | None = None,
        score_threshold: float | None = None,
        related_symbol: str | None = None,
        related_sector: str | None = None,
        sentiment: str | None = None,
    ) -> dict[str, Any]:
        search_result = self.search_news_chunks(
            query=query,
            limit=limit,
            score_threshold=score_threshold,
            related_symbol=related_symbol,
            related_sector=related_sector,
            sentiment=sentiment,
            dedupe_by_news=True,
        )
        if not search_result.get("ok"):
            return search_result
        context_blocks = []
        citations = []
        enriched_items = []
        for index, item in enumerate(search_result.get("items") or [], start=1):
            mapping = self._theme_mapping(item)
            enriched_item = {**item, "theme_mapping": mapping}
            enriched_items.append(enriched_item)
            context_blocks.append(
                f"[{index}] 标题：{item.get('title') or '未知'}\n"
                f"来源：{item.get('source') or '未知'} | 时间：{item.get('published_at') or '未知'} | 相似度：{item.get('score')}\n"
                f"相关板块：{'、'.join(item.get('related_sectors') or []) or '无'} | 映射题材：{'、'.join(theme.get('sector') or '' for theme in mapping.get('sectors') or []) or '无'} | 相关股票：{'、'.join(item.get('related_symbols') or []) or '无'}\n"
                f"题材解释：{mapping.get('summary') or '暂无'}\n"
                f"内容：{item.get('text_preview') or item.get('text') or ''}"
            )
            citations.append({
                "index": index,
                "news_id": item.get("news_id"),
                "chunk_id": item.get("id"),
                "title": item.get("title"),
                "score": item.get("score"),
                "theme_mapping": mapping,
            })
        theme_summary = self._theme_summary(enriched_items)
        return {
            "ok": True,
            "query": search_result.get("query"),
            "count": search_result.get("count"),
            "context_text": "\n\n".join(context_blocks),
            "citations": citations,
            "theme_summary": theme_summary,
            "items": enriched_items,
        }

    def _theme_mapping(self, item: dict[str, Any]) -> dict[str, Any]:
        text = "\n".join(str(part or "") for part in [item.get("title"), item.get("text_preview"), item.get("text")])
        semantic = self.semantic_service.analyze_text(text=text, use_llm=False) if text.strip() else {"ok": False}
        metadata_sectors = item.get("related_sectors") or []
        semantic_sectors = semantic.get("sectors") or []
        sector_rows = self._merge_sector_rows(metadata_sectors, semantic_sectors)
        primary = sector_rows[0].get("sector") if sector_rows else None
        return {
            "primary_sector": primary,
            "sectors": sector_rows,
            "sentiment": semantic.get("sentiment") or item.get("sentiment"),
            "impact": semantic.get("impact") or semantic.get("sentiment") or item.get("sentiment"),
            "confidence": semantic.get("confidence") or 0.45,
            "summary": self._mapping_summary(primary, sector_rows, semantic),
            "risk_warnings": semantic.get("risk_warnings") or [],
            "mapping_source": "metadata+rule_semantic",
        }

    def _merge_sector_rows(self, metadata_sectors: list[Any], semantic_sectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for sector in metadata_sectors:
            if not sector:
                continue
            key = str(sector)
            buckets[key] = {
                "sector": key,
                "score": 0.62,
                "source": ["metadata"],
                "matched_keywords": [],
                "reason": "新闻原始 related_sectors 字段命中。",
            }
        for row in semantic_sectors:
            sector = row.get("sector")
            if not sector:
                continue
            key = str(sector)
            existing = buckets.get(key)
            score = float(row.get("score") or 0.5)
            if existing:
                existing["score"] = round(max(float(existing.get("score") or 0), score) + 0.08, 3)
                existing["source"] = sorted(set((existing.get("source") or []) + ["semantic_rule"]))
                existing["matched_keywords"] = row.get("matched_keywords") or existing.get("matched_keywords") or []
                existing["reason"] = "元数据与语义规则同时命中，题材映射可信度更高。"
            else:
                buckets[key] = {
                    "sector": key,
                    "score": round(score, 3),
                    "source": ["semantic_rule"],
                    "matched_keywords": row.get("matched_keywords") or [],
                    "reason": row.get("reason") or "新闻文本关键词语义命中。",
                }
        rows = list(buckets.values())
        for row in rows:
            row["score"] = round(min(1.0, float(row.get("score") or 0)), 3)
            if row["score"] >= 0.78:
                row["tier"] = "strong"
                row["label"] = "强映射"
            elif row["score"] >= 0.58:
                row["tier"] = "medium"
                row["label"] = "中映射"
            else:
                row["tier"] = "weak"
                row["label"] = "弱映射"
        return sorted(rows, key=lambda item: item.get("score") or 0, reverse=True)[:5]

    def _mapping_summary(self, primary: str | None, sectors: list[dict[str, Any]], semantic: dict[str, Any]) -> str:
        if not sectors:
            return "暂未从新闻内容中映射出明确板块/题材。"
        sector_text = "、".join(item.get("sector") or "" for item in sectors[:3])
        sentiment = semantic.get("sentiment") or "neutral"
        sentiment_text = {"positive": "偏利好", "negative": "偏利空", "mixed": "多空混合", "neutral": "偏中性"}.get(sentiment, sentiment)
        return f"该 RAG 证据主要映射到 {primary or sector_text}，候选题材包括 {sector_text}，影响 {sentiment_text}。"

    def _theme_summary(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        buckets: dict[str, dict[str, Any]] = {}
        for item in items:
            mapping = item.get("theme_mapping") or {}
            for sector in mapping.get("sectors") or []:
                name = sector.get("sector")
                if not name:
                    continue
                row = buckets.setdefault(name, {
                    "sector": name,
                    "count": 0,
                    "max_score": 0.0,
                    "avg_score": 0.0,
                    "scores": [],
                    "sentiments": {},
                    "citations": [],
                })
                score = float(sector.get("score") or 0)
                row["count"] += 1
                row["max_score"] = max(float(row.get("max_score") or 0), score)
                row["scores"].append(score)
                sentiment = mapping.get("sentiment") or "neutral"
                row["sentiments"][sentiment] = row["sentiments"].get(sentiment, 0) + 1
                row["citations"].append(item.get("news_id") or item.get("id"))
        rows = []
        for row in buckets.values():
            scores = row.pop("scores", [])
            row["avg_score"] = round(sum(scores) / len(scores), 3) if scores else 0
            row["max_score"] = round(row["max_score"], 3)
            row["dominant_sentiment"] = max(row["sentiments"].items(), key=lambda pair: pair[1])[0] if row["sentiments"] else "neutral"
            row["citations"] = row["citations"][:5]
            rows.append(row)
        rows.sort(key=lambda item: (item.get("count") or 0, item.get("max_score") or 0), reverse=True)
        return {
            "count": len(rows),
            "primary_sector": rows[0].get("sector") if rows else None,
            "sectors": rows[:8],
            "summary": self._theme_summary_text(rows),
        }

    def _theme_summary_text(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "本次 RAG 证据暂未形成明确题材聚合。"
        top = rows[0]
        return f"本次 RAG 证据聚合到 {len(rows)} 个板块/题材，主线为 {top.get('sector')}，命中 {top.get('count')} 条证据。"

    def _score_threshold(self, value: float | None) -> float | None:
        if value is not None:
            return max(0.0, min(float(value), 1.0))
        configured = settings.rag_score_threshold
        if configured is None:
            return None
        return max(0.0, min(float(configured), 1.0))

    def _search_filters(
        self,
        *,
        related_symbol: str | None = None,
        related_sector: str | None = None,
        sentiment: str | None = None,
    ) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if related_symbol:
            filters["related_symbols"] = related_symbol
        if related_sector:
            filters["related_sectors"] = related_sector
        if sentiment:
            filters["sentiment"] = sentiment
        return filters

    def _search_item(self, result, metadata: dict[str, Any], query: str) -> dict[str, Any]:
        text = result.text or ""
        return {
            "id": result.id,
            "score": round(float(result.score or 0), 6),
            "text": text,
            "text_preview": self._preview_text(text, query=query),
            "news_id": metadata.get("news_id"),
            "chunk_index": metadata.get("chunk_index"),
            "title": metadata.get("title"),
            "source": metadata.get("source"),
            "published_at": metadata.get("published_at"),
            "related_symbols": metadata.get("related_symbols") or [],
            "related_sectors": metadata.get("related_sectors") or [],
            "tags": metadata.get("tags") or [],
            "sentiment": metadata.get("sentiment"),
            "importance": metadata.get("importance"),
            "metadata": metadata,
        }

    def _preview_text(self, text: str, *, query: str, max_length: int = 260) -> str:
        clean = " ".join(str(text or "").split())
        if len(clean) <= max_length:
            return clean
        keywords = [item for item in re.split(r"\s+|，|。|、|；|,|;", query) if len(item) >= 2]
        hit_positions = [clean.find(keyword) for keyword in keywords if clean.find(keyword) >= 0]
        if hit_positions:
            start = max(0, min(hit_positions) - 80)
        else:
            start = 0
        end = min(len(clean), start + max_length)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(clean) else ""
        return f"{prefix}{clean[start:end]}{suffix}"

    def chunk_stats(self) -> dict[str, Any]:
        try:
            with SessionLocal() as session:
                total = session.scalar(select(func.count()).select_from(NewsRAGChunkModel)) or 0
                rows = session.scalars(select(NewsRAGChunkModel)).all()
                uuid_like_count = sum(1 for row in rows if self._looks_like_uuid(row.vector_id))
                embedded = sum(1 for row in rows if self._is_current_embedded(row))
                news_count = session.scalar(select(func.count(func.distinct(NewsRAGChunkModel.news_id)))) or 0
                latest_updated = session.scalar(select(func.max(NewsRAGChunkModel.updated_at)))
        except Exception as exc:
            return {"ok": False, "error": f"读取 news_rag_chunks 统计失败：{exc}"}
        vector_status = self.vector_store.status() if settings.rag_vector_backend == "qdrant" else {}
        qdrant_points = self._safe_int(vector_status.get("points_count")) if vector_status.get("exists") else 0
        pending = max(0, int(total) - int(embedded))
        needs_rebuild = pending > 0 or (bool(vector_status.get("exists")) and qdrant_points < int(embedded))
        return {
            "ok": True,
            "total_chunks": int(total),
            "embedded_chunks": int(embedded),
            "pending_chunks": pending,
            "news_count": int(news_count),
            "embedding_model": settings.rag_embedding_model,
            "vector_store": settings.rag_vector_backend,
            "collection_name": settings.rag_collection_news,
            "latest_updated_at": latest_updated,
            "qdrant_points_count": qdrant_points,
            "qdrant_indexed_vectors_count": self._safe_int(vector_status.get("indexed_vectors_count")),
            "qdrant_dimension": vector_status.get("dimension"),
            "qdrant_count_matches_db": bool(vector_status.get("exists")) and qdrant_points >= int(embedded),
            "needs_rebuild": needs_rebuild,
            "uuid_like_embedded_chunks": int(uuid_like_count),
            "invalid_uuid_marked_chunks": max(0, int(uuid_like_count) - int(embedded)),
            "vector_status": vector_status,
        }

    def list_chunks(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        news_id: str | None = None,
        embedded: bool | None = None,
    ) -> dict[str, Any]:
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 20), 100))
        try:
            with SessionLocal() as session:
                stmt = select(NewsRAGChunkModel)
                conditions = []
                if news_id:
                    conditions.append(NewsRAGChunkModel.news_id == news_id)
                if conditions:
                    stmt = stmt.where(*conditions)
                all_rows = session.scalars(stmt.order_by(NewsRAGChunkModel.updated_at.desc(), NewsRAGChunkModel.id.desc())).all()
                if embedded is None:
                    filtered_rows = all_rows
                else:
                    filtered_rows = [row for row in all_rows if self._is_current_embedded(row) is embedded]
                total = len(filtered_rows)
                rows = filtered_rows[(page - 1) * page_size:page * page_size]
        except Exception as exc:
            return {"ok": False, "error": f"读取 news_rag_chunks 列表失败：{exc}", "items": []}
        return {
            "ok": True,
            "page": page,
            "page_size": page_size,
            "total": int(total),
            "items": [self._chunk_detail(row) for row in rows],
        }

    def _ensure_collection(self) -> dict[str, Any]:
        try:
            return self.vector_store.ensure_collection()
        except Exception as exc:
            return {
                "ok": False,
                "backend": settings.rag_vector_backend,
                "collection": settings.rag_collection_news,
                "url": settings.rag_qdrant_url,
                "error": str(exc),
                "hint": "请确认 Qdrant 已启动，QUANT_RAG_QDRANT_URL 可访问，向量维度与 embedding 模型一致。",
            }

    def _metadata(self, chunk: NewsRAGChunkModel) -> dict[str, Any]:
        metadata = self._json_object(chunk.metadata_json)
        metadata.update({
            "chunk_id": chunk.chunk_id,
            "news_id": chunk.news_id,
            "chunk_index": chunk.chunk_index,
            "content_hash": chunk.content_hash,
            "embedding_model": settings.rag_embedding_model,
            "vector_store": settings.rag_vector_backend,
            "collection_name": settings.rag_collection_news,
            "source_type": "market_news",
        })
        return metadata

    def _is_current_embedded(self, chunk: NewsRAGChunkModel) -> bool:
        return bool(
            chunk.vector_store == settings.rag_vector_backend
            and chunk.collection_name == settings.rag_collection_news
            and chunk.embedding_model == settings.rag_embedding_model
            and self._is_uuid(chunk.vector_id)
        )

    def _vector_point_id(self, chunk: NewsRAGChunkModel) -> str:
        """Return a stable Qdrant-compatible UUID point id for a news chunk."""
        source = f"{settings.rag_collection_news}:{chunk.chunk_id}:{chunk.content_hash or ''}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, source))

    def _looks_like_uuid(self, value: str | None) -> bool:
        return bool(value and re.fullmatch(r".{8}-.{4}-.{4}-.{4}-.{12}", value))

    def _is_uuid(self, value: str | None) -> bool:
        if not value:
            return False
        return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value))

    def _public_item(self, chunk: NewsRAGChunkModel) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id,
            "news_id": chunk.news_id,
            "chunk_index": chunk.chunk_index,
            "text_preview": chunk.text_preview,
            "vector_id": chunk.vector_id or chunk.chunk_id,
            "embedding_model": settings.rag_embedding_model,
            "collection_name": settings.rag_collection_news,
        }

    def _chunk_detail(self, chunk: NewsRAGChunkModel) -> dict[str, Any]:
        metadata = self._json_object(chunk.metadata_json)
        return {
            "id": chunk.id,
            "chunk_id": chunk.chunk_id,
            "news_id": chunk.news_id,
            "chunk_index": chunk.chunk_index,
            "text_preview": chunk.text_preview,
            "token_estimate": chunk.token_estimate,
            "content_hash": chunk.content_hash,
            "vector_id": chunk.vector_id,
            "embedding_model": chunk.embedding_model,
            "vector_store": chunk.vector_store,
            "collection_name": chunk.collection_name,
            "is_embedded": self._is_current_embedded(chunk),
            "title": metadata.get("title"),
            "source": metadata.get("source"),
            "published_at": metadata.get("published_at"),
            "related_symbols": metadata.get("related_symbols") or [],
            "related_sectors": metadata.get("related_sectors") or [],
            "tags": metadata.get("tags") or [],
            "created_at": chunk.created_at,
            "updated_at": chunk.updated_at,
        }

    def _json_object(self, value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
