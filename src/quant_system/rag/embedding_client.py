from __future__ import annotations

import time
from typing import Any

import httpx

from quant_system.core.config import settings


class EmbeddingClientError(RuntimeError):
    pass


class OpenAICompatibleEmbeddingClient:
    """Small OpenAI-compatible embedding client used by the RAG MVP."""

    def __init__(self) -> None:
        self.provider = settings.rag_embedding_provider
        self.base_url = (settings.rag_embedding_base_url or settings.llm_base_url or "").rstrip("/")
        self.api_key = settings.rag_embedding_api_key or settings.llm_api_key
        self.model = settings.rag_embedding_model
        self.timeout = settings.llm_timeout_seconds
        self.dimension = settings.rag_embedding_dimension
        self.batch_size = settings.rag_embedding_batch_size
        self.retry_count = max(0, int(settings.rag_embedding_retry_count or 0))
        self.retry_delay_seconds = max(0.0, float(settings.rag_embedding_retry_delay_seconds or 0.0))

    def enabled(self) -> bool:
        return bool(settings.rag_enabled and self.base_url and self.api_key and self.model)

    def status(self) -> dict[str, Any]:
        missing: list[str] = []
        if not settings.rag_enabled:
            missing.append("QUANT_RAG_ENABLED=true")
        if not self.base_url:
            missing.append("QUANT_RAG_EMBEDDING_BASE_URL or QUANT_LLM_BASE_URL")
        if not self.api_key:
            missing.append("QUANT_RAG_EMBEDDING_API_KEY or QUANT_LLM_API_KEY")
        if not self.model:
            missing.append("QUANT_RAG_EMBEDDING_MODEL")
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url or None,
            "dimension": self.dimension,
            "batch_size": self.batch_size,
            "retry_count": self.retry_count,
            "ready": self.enabled(),
            "missing": missing,
        }

    def embed_text(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.embed_texts_batched(texts)["vectors"]

    def embed_texts_batched(self, texts: list[str], *, batch_size: int | None = None) -> dict[str, Any]:
        if not self.enabled():
            raise EmbeddingClientError("RAG embedding 未启用或配置不完整。")
        cleaned = [text.strip() for text in texts if text and text.strip()]
        if not cleaned:
            return {"vectors": [], "count": 0, "dimension": 0, "batches": 0, "model": self.model}
        batch_size = max(1, min(int(batch_size or self.batch_size or 10), 128))
        vectors: list[list[float]] = []
        for start in range(0, len(cleaned), batch_size):
            batch = cleaned[start:start + batch_size]
            vectors.extend(self._embed_batch(batch, offset=start))
        self._validate_vectors(vectors, expected_count=len(cleaned))
        return {
            "vectors": vectors,
            "count": len(vectors),
            "dimension": len(vectors[0]) if vectors else 0,
            "batches": (len(cleaned) + batch_size - 1) // batch_size,
            "model": self.model,
        }

    def _client(self) -> httpx.Client:
        # Avoid inheriting broken system proxy settings for DashScope/OpenAI-compatible calls.
        # If curl can reach DashScope but Python reports connection failures, Windows proxy
        # environment variables are a common cause.
        return httpx.Client(timeout=self.timeout, trust_env=False)

    def _embed_batch(self, texts: list[str], *, offset: int = 0) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": self.model, "input": texts}
        data: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                with self._client() as client:
                    response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code if exc.response else 0
                if not self._should_retry_status(status_code) or attempt >= self.retry_count:
                    body = exc.response.text[:500] if exc.response is not None else ""
                    raise EmbeddingClientError(f"Embedding HTTP 状态异常：{status_code or 'unknown'} {body}") from exc
                self._sleep_before_retry(attempt)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as exc:
                last_error = exc
                if attempt >= self.retry_count:
                    if isinstance(exc, httpx.ConnectError):
                        raise EmbeddingClientError(f"Embedding 服务连接失败：{self.base_url}，detail={exc!r}") from exc
                    if isinstance(exc, httpx.TimeoutException):
                        raise EmbeddingClientError(f"Embedding 服务超时：{self.base_url}，detail={exc!r}") from exc
                    raise EmbeddingClientError(f"Embedding 网络请求失败：{self.base_url}，detail={exc!r}") from exc
                self._sleep_before_retry(attempt)
            except Exception as exc:
                last_error = exc
                raise EmbeddingClientError(f"Embedding 调用失败：{exc!r}") from exc
        if data is None:
            raise EmbeddingClientError(f"Embedding 调用失败：{last_error!r}")
        try:
            rows = sorted(data["data"], key=lambda item: item.get("index", 0))
            vectors = [list(map(float, row["embedding"])) for row in rows]
        except Exception as exc:
            raise EmbeddingClientError(f"Embedding 返回结构异常：{exc}") from exc
        if len(vectors) != len(texts):
            raise EmbeddingClientError(f"Embedding 返回数量异常：请求 {len(texts)} 条，返回 {len(vectors)} 条，batch_offset={offset}")
        return vectors

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code in {408, 409, 425, 429, 500, 502, 503, 504}

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_delay_seconds <= 0:
            return
        time.sleep(self.retry_delay_seconds * (2 ** attempt))

    def _validate_vectors(self, vectors: list[list[float]], *, expected_count: int) -> None:
        if len(vectors) != expected_count:
            raise EmbeddingClientError(f"Embedding 总数量异常：请求 {expected_count} 条，返回 {len(vectors)} 条。")
        if not vectors:
            return
        actual_dimension = len(vectors[0])
        if self.dimension and actual_dimension != self.dimension:
            raise EmbeddingClientError(
                f"Embedding 维度不匹配：模型返回 {actual_dimension} 维，但 QUANT_RAG_EMBEDDING_DIMENSION={self.dimension}。"
            )
        for index, vector in enumerate(vectors):
            if len(vector) != actual_dimension:
                raise EmbeddingClientError(f"Embedding 第 {index} 条维度不一致：{len(vector)} != {actual_dimension}")
