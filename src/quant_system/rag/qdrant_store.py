from __future__ import annotations

from typing import Any

import httpx

from quant_system.core.config import settings
from quant_system.rag.vector_store import VectorDocument, VectorSearchResult


class QdrantStoreError(RuntimeError):
    pass


class QdrantVectorStore:
    """Minimal Qdrant REST adapter for the first RAG MVP."""

    def __init__(self) -> None:
        self.url = settings.rag_qdrant_url.rstrip("/")
        self.api_key = settings.rag_qdrant_api_key
        self.collection = settings.rag_collection_news
        self.dimension = settings.rag_embedding_dimension
        self.timeout = settings.llm_timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        return headers

    def _client(self) -> httpx.Client:
        # Do not inherit system proxy settings for localhost Qdrant calls.
        # On Windows, proxy/VPN environment variables can route localhost:6333
        # through an HTTP proxy and turn healthy Qdrant responses into 502 errors.
        return httpx.Client(timeout=self.timeout, trust_env=False)

    def status(self) -> dict[str, Any]:
        if not settings.rag_enabled:
            return {
                "ready": False,
                "backend": "qdrant",
                "collection": self.collection,
                "url": self.url,
                "error": "RAG 未启用。",
            }
        try:
            with self._client() as client:
                response = client.get(f"{self.url}/collections/{self.collection}", headers=self._headers())
                if response.status_code == 404:
                    return {
                        "ready": False,
                        "backend": "qdrant",
                        "collection": self.collection,
                        "url": self.url,
                        "exists": False,
                        "dimension_matched": False,
                        "expected_dimension": self.dimension,
                        "error": "collection 不存在，可通过 ensure_collection 创建。",
                    }
                response.raise_for_status()
                data = response.json()
            result = data.get("result") or {}
            vector_size = self._collection_vector_size(result)
            dimension_matched = vector_size in (None, self.dimension)
            return {
                "ready": bool(dimension_matched),
                "backend": "qdrant",
                "collection": self.collection,
                "url": self.url,
                "exists": True,
                "dimension": vector_size,
                "expected_dimension": self.dimension,
                "dimension_matched": dimension_matched,
                "points_count": result.get("points_count"),
                "indexed_vectors_count": result.get("indexed_vectors_count"),
                "status": result.get("status"),
                "detail": result,
                "error": None if dimension_matched else f"collection 维度 {vector_size} 与配置维度 {self.dimension} 不一致。",
            }
        except httpx.ConnectError:
            return {"ready": False, "backend": "qdrant", "collection": self.collection, "url": self.url, "error": f"无法连接 Qdrant：{self.url}"}
        except httpx.TimeoutException:
            return {"ready": False, "backend": "qdrant", "collection": self.collection, "url": self.url, "error": f"连接 Qdrant 超时：{self.url}"}
        except Exception as exc:
            return {
                "ready": False,
                "backend": "qdrant",
                "collection": self.collection,
                "url": self.url,
                "error": str(exc),
            }

    def ensure_collection(self, *, force_recreate: bool = False) -> dict[str, Any]:
        existing = self.status()
        deleted = False
        if existing.get("exists") and force_recreate:
            self.delete_collection(missing_ok=True)
            existing = {"exists": False}
            deleted = True
        if existing.get("exists"):
            if not existing.get("dimension_matched", True):
                raise QdrantStoreError(existing.get("error") or "Qdrant collection 维度不匹配。")
            return {
                "ok": True,
                "collection": self.collection,
                "dimension": existing.get("dimension") or self.dimension,
                "created": False,
                "deleted": deleted,
                "detail": existing.get("detail"),
            }
        payload = {
            "vectors": {
                "size": self.dimension,
                "distance": "Cosine",
            }
        }
        try:
            with self._client() as client:
                response = client.put(f"{self.url}/collections/{self.collection}", headers=self._headers(), json=payload)
                if response.status_code >= 400:
                    raise QdrantStoreError(f"HTTP {response.status_code}: {response.text[:500]}")
                data = response.json()
            return {"ok": True, "collection": self.collection, "dimension": self.dimension, "created": True, "deleted": deleted, "detail": data.get("result")}
        except QdrantStoreError:
            raise
        except httpx.ConnectError as exc:
            raise QdrantStoreError(f"无法连接 Qdrant：{self.url}，请确认 Qdrant 服务已启动。") from exc
        except httpx.TimeoutException as exc:
            raise QdrantStoreError(f"连接 Qdrant 超时：{self.url}。") from exc
        except Exception as exc:
            raise QdrantStoreError(f"Qdrant collection 初始化失败：{exc}") from exc

    def delete_collection(self, *, missing_ok: bool = True) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.delete(f"{self.url}/collections/{self.collection}", headers=self._headers())
                if response.status_code == 404 and missing_ok:
                    return {"ok": True, "collection": self.collection, "deleted": False, "missing": True}
                if response.status_code >= 400:
                    raise QdrantStoreError(f"HTTP {response.status_code}: {response.text[:500]}")
                data = response.json()
            return {"ok": True, "collection": self.collection, "deleted": True, "detail": data.get("result")}
        except QdrantStoreError:
            raise
        except Exception as exc:
            raise QdrantStoreError(f"Qdrant collection 删除失败：{exc}") from exc

    def upsert_documents(self, documents: list[VectorDocument]) -> dict[str, Any]:
        if not documents:
            return {"ok": True, "upserted": 0}
        self._validate_documents(documents)
        points = [
            {
                "id": doc.id,
                "vector": doc.vector,
                "payload": {**doc.metadata, "text": doc.text},
            }
            for doc in documents
        ]
        try:
            with self._client() as client:
                response = client.put(
                    f"{self.url}/collections/{self.collection}/points",
                    headers=self._headers(),
                    params={"wait": "true"},
                    json={"points": points},
                )
                if response.status_code >= 400:
                    raise QdrantStoreError(f"HTTP {response.status_code}: {response.text[:500]}")
                data = response.json()
            return {"ok": True, "upserted": len(points), "dimension": self.dimension, "detail": data.get("result")}
        except QdrantStoreError:
            raise
        except Exception as exc:
            raise QdrantStoreError(f"Qdrant upsert 失败：{exc}") from exc

    def search(self, vector: list[float], *, limit: int = 8, filters: dict[str, Any] | None = None) -> list[VectorSearchResult]:
        if self.dimension and len(vector) != self.dimension:
            raise QdrantStoreError(f"查询向量维度不匹配：{len(vector)} != {self.dimension}")
        payload: dict[str, Any] = {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
        }
        qdrant_filter = self._build_filter(filters or {})
        if qdrant_filter:
            payload["filter"] = qdrant_filter
        try:
            with self._client() as client:
                response = client.post(
                    f"{self.url}/collections/{self.collection}/points/search",
                    headers=self._headers(),
                    json=payload,
                )
                if response.status_code == 404:
                    response = client.post(
                        f"{self.url}/collections/{self.collection}/points/query",
                        headers=self._headers(),
                        json={"query": vector, "limit": limit, "with_payload": True, **({"filter": qdrant_filter} if qdrant_filter else {})},
                    )
                if response.status_code >= 400:
                    raise QdrantStoreError(f"HTTP {response.status_code}: {response.text[:500]}")
                data = response.json()
        except QdrantStoreError:
            raise
        except Exception as exc:
            raise QdrantStoreError(f"Qdrant search 失败：{exc}") from exc
        results = []
        for item in data.get("result") or []:
            payload = item.get("payload") or {}
            results.append(VectorSearchResult(
                id=str(item.get("id")),
                score=float(item.get("score") or 0),
                text=payload.get("text"),
                metadata={key: value for key, value in payload.items() if key != "text"},
            ))
        return results

    def _collection_vector_size(self, result: dict[str, Any]) -> int | None:
        vectors = ((result.get("config") or {}).get("params") or {}).get("vectors")
        if isinstance(vectors, dict):
            if "size" in vectors:
                try:
                    return int(vectors["size"])
                except Exception:
                    return None
            first_vector = next(iter(vectors.values()), None)
            if isinstance(first_vector, dict) and "size" in first_vector:
                try:
                    return int(first_vector["size"])
                except Exception:
                    return None
        return None

    def _validate_documents(self, documents: list[VectorDocument]) -> None:
        for doc in documents:
            if self.dimension and len(doc.vector) != self.dimension:
                raise QdrantStoreError(f"文档 {doc.id} 向量维度不匹配：{len(doc.vector)} != {self.dimension}")
            if not doc.id:
                raise QdrantStoreError("VectorDocument.id 不能为空。")

    def _build_filter(self, filters: dict[str, Any]) -> dict[str, Any] | None:
        must = []
        for key, value in filters.items():
            if value in (None, "", []):
                continue
            if isinstance(value, list):
                must.append({"key": key, "match": {"any": value}})
            else:
                must.append({"key": key, "match": {"value": value}})
        return {"must": must} if must else None
