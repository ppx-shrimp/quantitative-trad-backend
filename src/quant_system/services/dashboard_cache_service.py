from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from quant_system.api.pagination import PageParams
from quant_system.core.config import settings
from quant_system.services.alert_todo_service import AlertTodoService
from quant_system.services.market_index_service import MarketIndexService
from quant_system.services.news_service import NewsService
from quant_system.services.stock_pool_service import StockPoolService
from quant_system.services.trading_service import TradingService


class DashboardCacheService:
    """首页首屏数据聚合 + 文件缓存预热服务。

    首页原来一次性并发打 7 个接口，其中指数/板块/资讯/持仓风控待办会触发外部行情、
    本地扫描和 AI 记录聚合。这里把首屏需要的数据聚合成一个快照，并落到本地文件缓存。
    前端优先读该快照，后台再异步刷新，避免第一次打开首页卡顿。
    """

    CACHE_VERSION = "dashboard_snapshot_v1"

    def __init__(self) -> None:
        self.cache_path = Path(settings.cache_dir) / "dashboard_snapshot.json"
        self._lock = threading.Lock()

    def get_snapshot(self, *, force_refresh: bool = False, allow_stale: bool = True) -> dict[str, Any]:
        cached = self._read_cache()
        if cached and not force_refresh and self._is_fresh(cached):
            return {**cached, "cache_hit": True, "stale": False}
        if cached and not force_refresh and allow_stale:
            self.prewarm_async(reason="stale_cache_refresh")
            return {**cached, "cache_hit": True, "stale": True, "message": "首页缓存已过期，已触发后台刷新。"}
        return self.prewarm(reason="force_refresh" if force_refresh else "cache_miss")

    def prewarm_async(self, *, reason: str = "startup") -> dict[str, Any]:
        if self._lock.locked():
            cached = self._read_cache()
            return {
                "status": "running",
                "reason": reason,
                "cache_hit": bool(cached),
                "snapshot": cached,
                "message": "首页缓存正在预热中。",
            }
        thread = threading.Thread(target=self._safe_prewarm, kwargs={"reason": reason}, daemon=True)
        thread.start()
        cached = self._read_cache()
        return {
            "status": "started",
            "reason": reason,
            "cache_hit": bool(cached),
            "snapshot": cached,
            "message": "已启动首页缓存后台预热。",
        }

    def prewarm(self, *, reason: str = "manual") -> dict[str, Any]:
        with self._lock:
            started = datetime.now(timezone.utc)
            payload = self._build_snapshot(reason=reason)
            duration_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
            payload["duration_ms"] = duration_ms
            self._write_cache(payload)
            return {**payload, "cache_hit": False, "stale": False}

    def _safe_prewarm(self, *, reason: str) -> None:
        try:
            self.prewarm(reason=reason)
        except Exception:
            # 预热失败不能影响服务启动和页面访问；下次请求会继续尝试。
            return

    def _build_snapshot(self, *, reason: str) -> dict[str, Any]:
        trading = TradingService()
        market = MarketIndexService()
        news = NewsService()
        pool = StockPoolService()
        alerts = AlertTodoService()

        sections: dict[str, Any] = {
            "indices": self._section(lambda: market.get_indices(force_refresh=False)),
            "hot_sectors": self._section(lambda: market.get_hot_sectors(limit=8, force_refresh=False)),
            "hot_news": self._section(lambda: news.get_hot_news()),
            "account": self._section(lambda: trading.account_summary()),
            "pnl_stats": self._section(lambda: trading.get_pnl_stats()),
            "positions": self._section(lambda: trading.list_positions()),
            "favorites": self._section(lambda: self._favorites_payload(pool)),
            "orders": self._section(lambda: trading.list_orders_page(PageParams(page=1, page_size=8)).to_dict()),
            "alert_todos": self._section(lambda: alerts.list_todos(limit_ai_records=100)),
        }
        ok_count = sum(1 for item in sections.values() if item.get("ok"))
        return {
            "version": self.CACHE_VERSION,
            "status": "ok" if ok_count == len(sections) else "partial",
            "reason": reason,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ttl_seconds": settings.dashboard_cache_ttl_seconds,
            "ok_count": ok_count,
            "section_count": len(sections),
            "sections": sections,
        }

    def _favorites_payload(self, pool: StockPoolService) -> dict[str, Any]:
        items = pool.list_members("favorites")
        return {"pool_code": "favorites", "items": items, "count": len(items)}

    def _section(self, fn: Callable[[], Any]) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        try:
            data = fn()
            return {
                "ok": True,
                "data": data,
                "error": None,
                "duration_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
            }
        except Exception as exc:
            return {
                "ok": False,
                "data": None,
                "error": str(exc),
                "duration_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
            }

    def _read_cache(self) -> dict[str, Any] | None:
        if not self.cache_path.exists():
            return None
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_cache(self, payload: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.cache_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp_path.replace(self.cache_path)

    def _is_fresh(self, payload: dict[str, Any]) -> bool:
        generated_at = payload.get("generated_at")
        if not generated_at:
            return False
        try:
            created = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        age = (datetime.now(timezone.utc) - created).total_seconds()
        return age <= settings.dashboard_cache_ttl_seconds
