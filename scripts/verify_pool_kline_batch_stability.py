from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quant_system.services.kline_service import KlineService


class FakeCache:
    enabled = True


class FakeStockPoolService:
    def list_members(self, pool_code: str) -> list[dict]:
        return [
            {"symbol": "600001", "name": "外部成功"},
            {"symbol": "600002", "name": "本地回退"},
            {"symbol": "600003", "name": "同步失败"},
            {"symbol": "ETF001", "name": "非股票代码应跳过"},
        ]


class BatchKlineService(KlineService):
    def __init__(self) -> None:
        self.kline_cache = FakeCache()
        self.stock_pool_service = FakeStockPoolService()

    def sync_symbol_kline(self, symbol: str, period: str = "daily", pool_code: str | None = None, tracked: bool = True) -> dict:
        normalized = self._normalize_symbol(symbol)
        if normalized == "600001":
            return {
                "symbol": normalized,
                "period": period,
                "status": "success",
                "rows_count": 100,
                "source": "eastmoney",
                "cache_enabled": True,
                "fallback_used": False,
                "attempts": 1,
                "provider_errors": [],
                "message": "ok",
            }
        if normalized == "600002":
            return {
                "symbol": normalized,
                "period": period,
                "status": "success",
                "rows_count": 80,
                "source": "local_db_fallback",
                "cache_enabled": True,
                "fallback_used": True,
                "attempts": 2,
                "provider_errors": ["provider down"],
                "message": "外部 K 线接口不可用，已回退本地数据库缓存",
            }
        return {
            "symbol": normalized,
            "period": period,
            "status": "failed",
            "rows_count": 0,
            "source": "external_provider",
            "cache_enabled": True,
            "fallback_used": False,
            "attempts": 2,
            "provider_errors": ["provider down"],
            "message": "provider down",
        }


def main() -> None:
    service = BatchKlineService()
    result = service.sync_pool_klines(pool_code="favorites", periods=["daily"], tracked=False)

    assert result["pool_code"] == "favorites", result
    assert result["symbol_count"] == 3, result
    assert result["total_tasks"] == 3, result
    assert result["status"] == "partial_success", result
    assert result["success_count"] == 2, result
    assert result["failed_count"] == 1, result
    assert result["fallback_count"] == 1, result
    assert result["external_success_count"] == 1, result
    assert result["summary"]["success_symbols"] == ["600001", "600002"], result
    assert result["summary"]["fallback_symbols"] == ["600002"], result
    assert result["summary"]["failed_symbols"] == ["600003"], result
    assert result["summary"]["failure_reasons"] == {"provider down": 1}, result
    assert len(result["groups"]["external_success"]) == 1, result
    assert len(result["groups"]["local_fallback"]) == 1, result
    assert len(result["groups"]["failed"]) == 1, result
    print("pool kline batch stability ok")


if __name__ == "__main__":
    main()
