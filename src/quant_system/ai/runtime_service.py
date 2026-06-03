from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quant_system.ai.service import AIAnalysisService
from quant_system.core.config import settings
from quant_system.rag.news_chunk_service import NewsChunkService
from quant_system.rag.news_embedding_service import NewsEmbeddingService
from quant_system.rag.service import RAGService


class AIRuntimeService:
    """AI / RAG runtime diagnostics and one-click RAG preprocessing orchestration."""

    def __init__(self) -> None:
        self.ai_service = AIAnalysisService()
        self.rag_service = RAGService()
        self.chunk_service = NewsChunkService()
        self.embedding_service = NewsEmbeddingService()
        self._last_pipeline_result: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        llm_status = self._safe_call(lambda: self.ai_service.llm_status(), fallback={"ok": False, "error": "LLM 状态读取失败。"})
        rag_status = self._safe_call(lambda: self.rag_service.status(), fallback={"ready": False, "error": "RAG 状态读取失败。"})
        chunk_stats = self._safe_call(lambda: self.embedding_service.chunk_stats(), fallback={"ok": False, "error": "新闻 chunk 统计读取失败。"})

        embedding_status = rag_status.get("embedding") if isinstance(rag_status.get("embedding"), dict) else {}
        vector_status = rag_status.get("vector_store") if isinstance(rag_status.get("vector_store"), dict) else {}
        total_chunks = self._safe_int(chunk_stats.get("total_chunks"))
        embedded_chunks = self._safe_int(chunk_stats.get("embedded_chunks"))
        pending_chunks = self._safe_int(chunk_stats.get("pending_chunks"))

        checks = [
            self._check("ai_enabled", settings.ai_enabled, "AI 分析开关", "QUANT_AI_ENABLED 未开启。"),
            self._check("llm_ready", bool(llm_status.get("llm_ready") or llm_status.get("ok")), "LLM 可用", llm_status.get("message") or llm_status.get("error") or "LLM 未就绪。"),
            self._check("rag_enabled", settings.rag_enabled, "RAG 开关", "QUANT_RAG_ENABLED=true 未开启。"),
            self._check("embedding_ready", bool(embedding_status.get("ready")), "Embedding 配置", "Embedding base_url/api_key/model 未配置完整。"),
            self._check("vector_ready", bool(vector_status.get("ready")), "向量库连接", vector_status.get("error") or "Qdrant collection 未就绪。"),
            self._check("chunks_ready", total_chunks > 0, "新闻 Chunk", "尚未生成 news_rag_chunks，请先执行一键预处理。"),
            self._check("vectors_ready", embedded_chunks > 0, "新闻向量", "尚未完成新闻 chunk 向量化。"),
            self._check(
                "vector_count_consistent",
                self._vector_count_consistent(chunk_stats, vector_status),
                "向量数量一致性",
                "DB 标记的已向量化数量大于 Qdrant 实际 points_count，请点击“强制重建向量”。",
            ),
        ]
        blocking = [item for item in checks if item["severity"] == "blocking"]
        warnings = [item for item in checks if item["severity"] == "warning"]
        ready = not blocking and bool(settings.ai_enabled)
        if ready and warnings:
            status = "degraded"
        elif ready:
            status = "ready"
        else:
            status = "blocked"

        next_actions = self._next_actions(checks, pending_chunks=pending_chunks)
        return {
            "ok": True,
            "status": status,
            "ready": ready,
            "generated_at": self._now(),
            "summary": self._summary(status=status, blocking_count=len(blocking), warning_count=len(warnings), pending_chunks=pending_chunks),
            "checks": checks,
            "next_actions": next_actions,
            "llm": llm_status,
            "rag": rag_status,
            "chunks": chunk_stats,
            "last_pipeline": self._last_pipeline_result,
            "config": {
                "ai_enabled": settings.ai_enabled,
                "ai_mock_enabled": settings.ai_mock_enabled,
                "ai_prompt_version": settings.ai_prompt_version,
                "ai_max_prompt_chars": settings.ai_max_prompt_chars,
                "ai_max_rag_context_chars": settings.ai_max_rag_context_chars,
                "ai_max_rag_citations": settings.ai_max_rag_citations,
                "rag_enabled": settings.rag_enabled,
                "rag_vector_backend": settings.rag_vector_backend,
                "rag_collection_news": settings.rag_collection_news,
                "rag_embedding_model": settings.rag_embedding_model,
                "rag_embedding_dimension": settings.rag_embedding_dimension,
                "rag_chunk_size": settings.rag_chunk_size,
                "rag_chunk_overlap": settings.rag_chunk_overlap,
                "rag_search_limit": settings.rag_search_limit,
                "rag_score_threshold": settings.rag_score_threshold,
                "rag_embedding_batch_size": settings.rag_embedding_batch_size,
                "rag_skip_embedding_if_pending_over": settings.rag_skip_embedding_if_pending_over,
            },
        }

    def run_rag_pipeline(
        self,
        *,
        limit: int = 100,
        force_rechunk: bool = False,
        force_reembed: bool = False,
        ensure_collection: bool = True,
        run_embedding: bool = True,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit or 100), 500))
        started = datetime.now(timezone.utc)
        steps: list[dict[str, Any]] = []

        collection_status = self._safe_call(lambda: self.rag_service.vector_store.status(), fallback={"ready": False, "error": "读取向量库状态失败。"})
        collection_missing = collection_status.get("exists") is False
        if ensure_collection:
            collection_result = self._safe_call(lambda: self.rag_service.ensure_collection(), fallback={"ok": False, "error": "初始化向量库 collection 失败。"})
            steps.append(self._step("ensure_collection", "确认 Qdrant collection", collection_result))
        else:
            collection_result = {"ok": True, "skipped": True}
            steps.append(self._step("ensure_collection", "确认 Qdrant collection", collection_result))
        effective_force_reembed = bool(force_reembed or collection_missing or collection_result.get("created"))

        chunk_result = self._safe_call(
            lambda: self.chunk_service.chunk_recent_news(limit=limit, force_rechunk=force_rechunk),
            fallback={"ok": False, "error": "新闻 chunk 化失败。", "items": []},
        )
        steps.append(self._step("chunk_news", "生成新闻 Chunk", chunk_result))

        embed_result: dict[str, Any]
        if run_embedding:
            embed_result = self._safe_call(
                lambda: self.embedding_service.embed_news_chunks(limit=limit, force_reembed=effective_force_reembed),
                fallback={"ok": False, "error": "新闻 chunk 向量化失败。", "items": []},
            )
            steps.append(self._step("embed_chunks", "Embedding 并写入向量库", embed_result, allow_partial=True))
        else:
            embed_result = {"ok": True, "skipped": True, "message": "已跳过 embedding。"}
            steps.append(self._step("embed_chunks", "Embedding 并写入向量库", embed_result))

        stats_result = self._safe_call(lambda: self.embedding_service.chunk_stats(), fallback={"ok": False, "error": "读取处理后统计失败。"})
        steps.append(self._step("chunk_stats", "读取处理后统计", stats_result))

        ok = all(step.get("ok") or step.get("skipped") for step in steps)
        duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
        result = {
            "ok": ok,
            "status": "success" if ok else "partial_failed",
            "generated_at": self._now(),
            "duration_ms": duration_ms,
            "params": {
                "limit": limit,
                "force_rechunk": force_rechunk,
                "force_reembed": force_reembed,
                "effective_force_reembed": effective_force_reembed,
                "collection_missing_before_run": collection_missing,
                "ensure_collection": ensure_collection,
                "run_embedding": run_embedding,
            },
            "summary": self._pipeline_summary(steps, stats_result),
            "steps": steps,
            "stats": stats_result,
        }
        self._last_pipeline_result = result
        return result

    def _safe_call(self, fn, *, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            result = fn()
            return result if isinstance(result, dict) else {"ok": True, "data": result}
        except Exception as exc:
            return {**fallback, "error": f"{fallback.get('error') or '执行失败'}：{exc}"}

    def _check(self, code: str, passed: bool, label: str, message: str) -> dict[str, Any]:
        severity = "ok" if passed else ("blocking" if code in {"ai_enabled", "rag_enabled", "embedding_ready", "vector_ready"} else "warning")
        return {
            "code": code,
            "label": label,
            "ok": bool(passed),
            "severity": severity,
            "message": "正常" if passed else message,
        }

    def _next_actions(self, checks: list[dict[str, Any]], *, pending_chunks: int) -> list[str]:
        failed = {item["code"] for item in checks if not item.get("ok")}
        actions = []
        if "rag_enabled" in failed:
            actions.append("在 .env 中设置 QUANT_RAG_ENABLED=true，并重启后端。")
        if "embedding_ready" in failed:
            actions.append("补齐 QUANT_RAG_EMBEDDING_BASE_URL / QUANT_RAG_EMBEDDING_API_KEY / QUANT_RAG_EMBEDDING_MODEL。")
        if "vector_ready" in failed:
            actions.append("确认 Qdrant 已启动，并调用 /api/v1/rag/collections/ensure 初始化 collection。")
        if "chunks_ready" in failed:
            actions.append("点击“一键 RAG 预处理”生成 market_news 的 chunk。")
        if "vectors_ready" in failed or pending_chunks > 0:
            actions.append("点击“一键 RAG 预处理”将待处理 chunk 写入向量库。")
        if "vector_count_consistent" in failed:
            actions.append("点击“强制重建向量”，用 UUID point id 覆盖旧的非法 vector_id，并重新写入 Qdrant。")
        if not actions:
            actions.append("AI/RAG 基础链路已就绪，可以直接发起带资讯的 AI 个股分析。")
        return actions

    def _summary(self, *, status: str, blocking_count: int, warning_count: int, pending_chunks: int) -> str:
        if status == "ready":
            return "AI / RAG 运行链路已就绪。"
        if status == "degraded":
            return f"AI / RAG 可用但仍有 {warning_count} 个非阻塞问题，待向量化 chunk {pending_chunks} 条。"
        return f"AI / RAG 运行链路存在 {blocking_count} 个阻塞项，需要先处理配置或向量库状态。"

    def _step(self, code: str, label: str, result: dict[str, Any], *, allow_partial: bool = False) -> dict[str, Any]:
        partial_ok = allow_partial and result.get("status") == "partial_success"
        return {
            "code": code,
            "label": label,
            "ok": bool(result.get("ok") or partial_ok),
            "partial": bool(partial_ok),
            "skipped": bool(result.get("skipped")),
            "error": result.get("error"),
            "message": result.get("message"),
            "result": result,
        }

    def _pipeline_summary(self, steps: list[dict[str, Any]], stats: dict[str, Any]) -> str:
        failed = [step for step in steps if not step.get("ok") and not step.get("skipped")]
        total = self._safe_int(stats.get("total_chunks"))
        embedded = self._safe_int(stats.get("embedded_chunks"))
        pending = self._safe_int(stats.get("pending_chunks"))
        partial = [step for step in steps if step.get("partial")]
        if failed:
            return f"RAG 预处理部分失败：{failed[0].get('label')}。当前 chunk {total} 条，已向量化 {embedded} 条，待处理 {pending} 条。"
        if partial:
            result = partial[0].get("result") or {}
            return f"RAG 预处理部分批次失败：成功写入 {result.get('upserted_count') or embedded} 条，失败批次 {result.get('failed_batch_count') or 0} 个；当前待处理 {pending} 条，下次可继续运行。"
        points = self._safe_int(stats.get("qdrant_points_count"))
        matched = stats.get("qdrant_count_matches_db")
        consistency_text = "向量库数量已覆盖 DB 标记" if matched else f"Qdrant 实际 points {points} 条少于 DB 标记，需强制重建校准"
        return f"RAG 预处理完成。当前 chunk {total} 条，已向量化 {embedded} 条，待处理 {pending} 条，{consistency_text}。"

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _vector_count_consistent(self, chunk_stats: dict[str, Any], vector_status: dict[str, Any]) -> bool:
        if not vector_status.get("ready") or not vector_status.get("exists"):
            return False
        embedded = self._safe_int(chunk_stats.get("embedded_chunks"))
        points = self._safe_int(vector_status.get("points_count"))
        total = self._safe_int(chunk_stats.get("total_chunks"))
        if total <= 0 or embedded <= 0:
            return False
        return points >= embedded

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
