from __future__ import annotations

import hashlib
import json
from typing import Any

from quant_system.core.config import settings


class NewsCacheService:
    """Redis-backed news cache with graceful DB fallback semantics.

    Redis is only an acceleration layer for query results and source payloads. Any
    import, connection, or serialization failure becomes a cache miss so the local
    market_news table remains the source of truth.
    """

    def __init__(self) -> None:
        self.enabled = bool(settings.redis_enabled) and bool(settings.news_cache_enabled)
        self._client: Any | None = None
        self._last_error: str | None = None
        self._hit_count = 0
        self._miss_count = 0

    def get_json(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            raw = client.get(key)
            if not raw:
                self._miss_count += 1
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            self._hit_count += 1
            return json.loads(raw)
        except Exception as exc:
            self._last_error = str(exc)
            return None

    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        if not self.enabled:
            return False
        client = self._get_client()
        if client is None:
            return False
        try:
            payload = json.dumps(value, ensure_ascii=False, default=str)
            client.setex(key, ttl_seconds or settings.news_cache_ttl_seconds, payload)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    def invalidate_all(self) -> int:
        if not self.enabled:
            return 0
        client = self._get_client()
        if client is None:
            return 0
        deleted = 0
        try:
            for pattern in (self.query_pattern(), self.source_pattern(), self.status_pattern()):
                for key in client.scan_iter(match=pattern, count=100):
                    deleted += int(client.delete(key) or 0)
            return deleted
        except Exception as exc:
            self._last_error = str(exc)
            return deleted

    def invalidate_queries(self) -> int:
        if not self.enabled:
            return 0
        client = self._get_client()
        if client is None:
            return 0
        deleted = 0
        try:
            for key in client.scan_iter(match=self.query_pattern(), count=100):
                deleted += int(client.delete(key) or 0)
            return deleted
        except Exception as exc:
            self._last_error = str(exc)
            return deleted

    def build_query_key(self, params: dict[str, Any]) -> str:
        normalized = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]
        return f"{settings.news_cache_prefix}:news:query:{digest}"

    def build_source_key(self, news_type: str, limit: int) -> str:
        return f"{settings.news_cache_prefix}:news:source:{news_type}:limit:{limit}"

    def build_status_key(self) -> str:
        return f"{settings.news_cache_prefix}:news:status"

    def query_pattern(self) -> str:
        return f"{settings.news_cache_prefix}:news:query:*"

    def source_pattern(self) -> str:
        return f"{settings.news_cache_prefix}:news:source:*"

    def status_pattern(self) -> str:
        return f"{settings.news_cache_prefix}:news:status"

    def stats(self) -> dict:
        total = self._hit_count + self._miss_count
        return {
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "hit_rate": round(self._hit_count / total * 100, 2) if total > 0 else 0,
        }

    def status(self) -> dict:
        if not self.enabled:
            return {
                "enabled": False,
                "available": False,
                "status": "skip",
                "message": "资讯缓存未启用，当前直接读取本地 market_news。",
                "redis_url": self._mask_redis_url(settings.redis_url),
                "ttl_seconds": settings.news_cache_ttl_seconds,
                "prefix": settings.news_cache_prefix,
            }
        client = self._get_client()
        if client is None:
            return {
                "enabled": True,
                "available": False,
                "status": "warn",
                "message": f"资讯缓存不可用：{self._last_error or '客户端初始化失败'}。已自动回退本地库。",
                "redis_url": self._mask_redis_url(settings.redis_url),
                "ttl_seconds": settings.news_cache_ttl_seconds,
                "prefix": settings.news_cache_prefix,
            }
        try:
            client.ping()
            return {
                "enabled": True,
                "available": True,
                "status": "ok",
                "message": "资讯缓存连接正常。",
                "redis_url": self._mask_redis_url(settings.redis_url),
                "ttl_seconds": settings.news_cache_ttl_seconds,
                "prefix": settings.news_cache_prefix,
                **self.stats(),
            }
        except Exception as exc:
            self._last_error = str(exc)
            return {
                "enabled": True,
                "available": False,
                "status": "warn",
                "message": f"资讯缓存不可用：{exc}。已自动回退本地库。",
                "redis_url": self._mask_redis_url(settings.redis_url),
                "ttl_seconds": settings.news_cache_ttl_seconds,
                "prefix": settings.news_cache_prefix,
            }

    def _get_client(self):
        if not self.enabled:
            return None
        if self._client is not None:
            return self._client
        try:
            import redis

            self._client = redis.from_url(
                settings.redis_url,
                socket_connect_timeout=settings.redis_socket_timeout_seconds,
                socket_timeout=settings.redis_socket_timeout_seconds,
                decode_responses=False,
            )
            return self._client
        except Exception as exc:
            self._last_error = str(exc)
            return None

    def _mask_redis_url(self, redis_url: str) -> str:
        if "://" not in redis_url or "@" not in redis_url:
            return redis_url
        scheme, rest = redis_url.split("://", 1)
        credentials, host_part = rest.split("@", 1)
        if ":" not in credentials:
            return f"{scheme}://***@{host_part}"
        username, _password = credentials.split(":", 1)
        return f"{scheme}://{username}:***@{host_part}"
