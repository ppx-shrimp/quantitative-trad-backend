from __future__ import annotations

import json
from typing import Any

from quant_system.core.config import settings


class KlineCacheService:
    """Redis-backed K-line cache with graceful DB fallback semantics.

    Redis is an acceleration layer only. Any import/connection/serialization error is
    converted to cache miss or degraded status so the database remains the source of truth.
    """

    def __init__(self) -> None:
        self.enabled = bool(settings.redis_enabled)
        self._client: Any | None = None
        self._last_error: str | None = None

    def get_klines(self, symbol: str, period: str, limit: int) -> list[dict] | None:
        if not self.enabled:
            return None
        client = self._get_client()
        if client is None:
            return None
        key = self._kline_key(symbol, period, limit)
        try:
            raw = client.get(key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            rows = json.loads(raw)
            if not isinstance(rows, list):
                return None
            return rows
        except Exception as exc:
            self._last_error = str(exc)
            return None

    def set_klines(self, symbol: str, period: str, limit: int, rows: list[dict]) -> bool:
        if not self.enabled:
            return False
        client = self._get_client()
        if client is None:
            return False
        key = self._kline_key(symbol, period, limit)
        try:
            payload = json.dumps(rows, ensure_ascii=False, default=str)
            client.setex(key, settings.kline_cache_ttl_seconds, payload)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    def invalidate_klines(self, symbol: str, period: str) -> int:
        if not self.enabled:
            return 0
        client = self._get_client()
        if client is None:
            return 0
        pattern = self._kline_key(symbol, period, "*")
        deleted = 0
        try:
            for key in client.scan_iter(match=pattern, count=100):
                deleted += int(client.delete(key) or 0)
            return deleted
        except Exception as exc:
            self._last_error = str(exc)
            return deleted

    def status(self) -> dict:
        if not self.enabled:
            return {
                "enabled": False,
                "available": False,
                "status": "skip",
                "message": "Redis K 线缓存未启用，当前仅使用数据库查询。",
                "redis_url": self._mask_redis_url(settings.redis_url),
                "ttl_seconds": settings.kline_cache_ttl_seconds,
                "prefix": settings.kline_cache_prefix,
            }
        client = self._get_client()
        if client is None:
            return {
                "enabled": True,
                "available": False,
                "status": "warn",
                "message": f"Redis K 线缓存不可用：{self._last_error or '客户端初始化失败'}。已自动回退数据库。",
                "redis_url": self._mask_redis_url(settings.redis_url),
                "ttl_seconds": settings.kline_cache_ttl_seconds,
                "prefix": settings.kline_cache_prefix,
            }
        try:
            client.ping()
            return {
                "enabled": True,
                "available": True,
                "status": "ok",
                "message": "Redis K 线缓存连接正常。",
                "redis_url": self._mask_redis_url(settings.redis_url),
                "ttl_seconds": settings.kline_cache_ttl_seconds,
                "prefix": settings.kline_cache_prefix,
            }
        except Exception as exc:
            self._last_error = str(exc)
            return {
                "enabled": True,
                "available": False,
                "status": "warn",
                "message": f"Redis K 线缓存不可用：{exc}。已自动回退数据库。",
                "redis_url": self._mask_redis_url(settings.redis_url),
                "ttl_seconds": settings.kline_cache_ttl_seconds,
                "prefix": settings.kline_cache_prefix,
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

    def _kline_key(self, symbol: str, period: str, limit: int | str) -> str:
        normalized_symbol = symbol.strip().upper().split(".")[0]
        return f"{settings.kline_cache_prefix}:kline:{normalized_symbol}:{period}:limit:{limit}"

    def _mask_redis_url(self, redis_url: str) -> str:
        if "://" not in redis_url or "@" not in redis_url:
            return redis_url
        scheme, rest = redis_url.split("://", 1)
        credentials, host_part = rest.split("@", 1)
        if ":" not in credentials:
            return f"{scheme}://***@{host_part}"
        username, _password = credentials.split(":", 1)
        return f"{scheme}://{username}:***@{host_part}"
