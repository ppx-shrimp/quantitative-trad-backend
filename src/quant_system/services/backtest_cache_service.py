from __future__ import annotations

import hashlib
import json
from typing import Any

from quant_system.core.config import settings


class BacktestCacheService:
    """Redis-backed backtest result cache with graceful degradation.

    Caches backtest results to avoid redundant computation when running
    strategy-compare and grid-optimize with the same parameters.
    Redis is an acceleration layer only - any error degrades to cache miss.
    """

    def __init__(self) -> None:
        self.enabled = bool(settings.redis_enabled) and bool(settings.backtest_cache_enabled)
        self._client: Any | None = None
        self._last_error: str | None = None
        self._hit_count = 0
        self._miss_count = 0

    def get_backtest(self, cache_key: str) -> dict | None:
        if not self.enabled:
            return None
        client = self._get_client()
        if client is None:
            return None
        try:
            raw = client.get(cache_key)
            if not raw:
                self._miss_count += 1
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            result = json.loads(raw)
            if not isinstance(result, dict):
                return None
            self._hit_count += 1
            return result
        except Exception as exc:
            self._last_error = str(exc)
            return None

    def set_backtest(self, cache_key: str, result: dict) -> bool:
        if not self.enabled:
            return False
        client = self._get_client()
        if client is None:
            return False
        try:
            payload = json.dumps(result, ensure_ascii=False, default=str)
            client.setex(cache_key, settings.backtest_cache_ttl_seconds, payload)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            return False

    def invalidate_symbol(self, symbol: str) -> int:
        if not self.enabled:
            return 0
        client = self._get_client()
        if client is None:
            return 0
        normalized = symbol.strip().upper().split(".")[0]
        pattern = f"{settings.backtest_cache_prefix}:bt:*:{normalized}:*"
        deleted = 0
        try:
            for key in client.scan_iter(match=pattern, count=100):
                deleted += int(client.delete(key) or 0)
            return deleted
        except Exception as exc:
            self._last_error = str(exc)
            return deleted

    def invalidate_pool(self, pool_code: str) -> int:
        if not self.enabled:
            return 0
        client = self._get_client()
        if client is None:
            return 0
        pattern = f"{settings.backtest_cache_prefix}:bt:*:pool:{pool_code}:*"
        deleted = 0
        try:
            for key in client.scan_iter(match=pattern, count=100):
                deleted += int(client.delete(key) or 0)
            return deleted
        except Exception as exc:
            self._last_error = str(exc)
            return deleted

    def invalidate_all(self) -> int:
        if not self.enabled:
            return 0
        client = self._get_client()
        if client is None:
            return 0
        pattern = f"{settings.backtest_cache_prefix}:bt:*"
        deleted = 0
        try:
            for key in client.scan_iter(match=pattern, count=100):
                deleted += int(client.delete(key) or 0)
            return deleted
        except Exception as exc:
            self._last_error = str(exc)
            return deleted

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
                "message": "回测缓存未启用。",
                "ttl_seconds": settings.backtest_cache_ttl_seconds,
            }
        client = self._get_client()
        if client is None:
            return {
                "enabled": True,
                "available": False,
                "status": "warn",
                "message": f"回测缓存不可用：{self._last_error or '客户端初始化失败'}。",
                "ttl_seconds": settings.backtest_cache_ttl_seconds,
            }
        try:
            client.ping()
            return {
                "enabled": True,
                "available": True,
                "status": "ok",
                "message": "回测缓存连接正常。",
                "ttl_seconds": settings.backtest_cache_ttl_seconds,
                **self.stats(),
            }
        except Exception as exc:
            self._last_error = str(exc)
            return {
                "enabled": True,
                "available": False,
                "status": "warn",
                "message": f"回测缓存不可用：{exc}。",
                "ttl_seconds": settings.backtest_cache_ttl_seconds,
            }

    def build_backtest_key(
        self,
        symbol: str | None = None,
        pool_code: str | None = None,
        period: str = "daily",
        strategy_mode: str = "strict",
        initial_cash: float | None = None,
        quantity: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit_symbols: int | None = None,
    ) -> str:
        if symbol:
            normalized = symbol.strip().upper().split(".")[0]
            target = f"symbol:{normalized}"
        else:
            target = f"pool:{pool_code or 'default'}"

        params_str = f"{period}:{strategy_mode}:{initial_cash}:{quantity}:{start_date}:{end_date}:{limit_symbols}"
        params_hash = hashlib.md5(params_str.encode()).hexdigest()[:12]
        return f"{settings.backtest_cache_prefix}:bt:{target}:{params_hash}"

    def build_strategy_compare_key(
        self,
        symbol: str | None = None,
        pool_code: str | None = None,
        period: str = "daily",
        strategy_modes: list[str] | None = None,
        initial_cash: float | None = None,
        quantity: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit_symbols: int | None = None,
    ) -> str:
        if symbol:
            normalized = symbol.strip().upper().split(".")[0]
            target = f"symbol:{normalized}"
        else:
            target = f"pool:{pool_code or 'default'}"

        modes_str = ",".join(sorted(strategy_modes or []))
        params_str = f"{period}:{modes_str}:{initial_cash}:{quantity}:{start_date}:{end_date}:{limit_symbols}"
        params_hash = hashlib.md5(params_str.encode()).hexdigest()[:12]
        return f"{settings.backtest_cache_prefix}:bt:compare:{target}:{params_hash}"

    def build_grid_optimize_key(
        self,
        symbol: str | None = None,
        pool_code: str | None = None,
        period: str = "daily",
        strategy_mode: str = "loose",
        initial_cash: float | None = None,
        quantity: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit_symbols: int | None = None,
        take_profit_pct: list[float] | None = None,
        stop_loss_pct: list[float] | None = None,
        min_trend_score: list[float] | None = None,
        min_confidence: list[float] | None = None,
    ) -> str:
        if symbol:
            normalized = symbol.strip().upper().split(".")[0]
            target = f"symbol:{normalized}"
        else:
            target = f"pool:{pool_code or 'default'}"

        grid_str = f"{take_profit_pct}:{stop_loss_pct}:{min_trend_score}:{min_confidence}"
        params_str = f"{period}:{strategy_mode}:{initial_cash}:{quantity}:{start_date}:{end_date}:{limit_symbols}:{grid_str}"
        params_hash = hashlib.md5(params_str.encode()).hexdigest()[:12]
        return f"{settings.backtest_cache_prefix}:bt:grid:{target}:{params_hash}"

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
