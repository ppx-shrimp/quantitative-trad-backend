from __future__ import annotations

import uuid
from typing import Any

from quant_system.core.config import settings
from quant_system.db.database import SessionLocal
from quant_system.db.models import NewsRAGChunkModel
from quant_system.rag.embedding_client import OpenAICompatibleEmbeddingClient
from quant_system.rag.qdrant_store import QdrantVectorStore
from quant_system.rag.vector_store import VectorDocument


class RAGService:
    """First-phase RAG service: health, debug upsert, and debug semantic search."""

    def __init__(self) -> None:
        self.embedding_client = OpenAICompatibleEmbeddingClient()
        self.vector_store = QdrantVectorStore()

    def status(self) -> dict[str, Any]:
        embedding_status = self.embedding_client.status()
        vector_status = self.vector_store.status() if settings.rag_vector_backend == "qdrant" else {
            "ready": False,
            "backend": settings.rag_vector_backend,
            "error": "第一期 MVP 仅实现 qdrant。",
        }
        return {
            "enabled": settings.rag_enabled,
            "backend": settings.rag_vector_backend,
            "collection": settings.rag_collection_news,
            "embedding": embedding_status,
            "vector_store": vector_status,
            "ready": bool(settings.rag_enabled and embedding_status.get("ready") and vector_status.get("ready")),
        }

    def ensure_collection(self, *, force_recreate: bool = False) -> dict[str, Any]:
        if not settings.rag_enabled:
            return {"ok": False, "error": "RAG 未启用，请设置 QUANT_RAG_ENABLED=true。"}
        if settings.rag_vector_backend != "qdrant":
            return {"ok": False, "error": "第一期 MVP 仅实现 qdrant。"}
        try:
            result = self.vector_store.ensure_collection(force_recreate=force_recreate)
            if force_recreate and result.get("ok"):
                self._clear_news_vector_marks()
            return result
        except Exception as exc:
            return {
                "ok": False,
                "backend": settings.rag_vector_backend,
                "collection": settings.rag_collection_news,
                "url": settings.rag_qdrant_url,
                "error": str(exc),
                "hint": "请确认 Qdrant 已启动，QUANT_RAG_QDRANT_URL 可访问，向量维度与 embedding 模型一致。",
            }

    def debug_upsert(self, text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not settings.rag_enabled:
            return {"ok": False, "error": "RAG 未启用，请设置 QUANT_RAG_ENABLED=true。"}
        clean_text = text.strip()
        if not clean_text:
            return {"ok": False, "error": "text 不能为空"}
        vector = self.embedding_client.embed_text(clean_text)
        vector_id = self._debug_id(clean_text)
        doc = VectorDocument(
            id=vector_id,
            text=clean_text,
            vector=vector,
            metadata={
                "source": "rag_debug",
                "content_hash": vector_id,
                **(metadata or {}),
            },
        )
        result = self.vector_store.upsert_documents([doc])
        return {
            "ok": True,
            "id": vector_id,
            "dimension": len(vector),
            "upsert": result,
        }

    def debug_search(self, query: str, limit: int | None = None) -> dict[str, Any]:
        if not settings.rag_enabled:
            return {"ok": False, "error": "RAG 未启用，请设置 QUANT_RAG_ENABLED=true。", "items": []}
        clean_query = query.strip()
        if not clean_query:
            return {"ok": False, "error": "query 不能为空", "items": []}
        vector = self.embedding_client.embed_text(clean_query)
        items = self.vector_store.search(vector, limit=limit or settings.rag_search_limit)
        return {
            "ok": True,
            "query": clean_query,
            "count": len(items),
            "items": [
                {
                    "id": item.id,
                    "score": item.score,
                    "text": item.text,
                    "metadata": item.metadata,
                }
                for item in items
            ],
        }

    def _clear_news_vector_marks(self) -> None:
        with SessionLocal() as session:
            rows = session.query(NewsRAGChunkModel).filter(NewsRAGChunkModel.collection_name == settings.rag_collection_news).all()
            for row in rows:
                row.embedding_model = None
                row.vector_store = None
                row.collection_name = None
                row.vector_id = None
            session.commit()

    def _debug_id(self, text: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{settings.rag_collection_news}:debug:{text}"))
