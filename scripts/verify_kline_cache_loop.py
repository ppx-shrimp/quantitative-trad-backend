from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quant_system.services.kline_cache_service import KlineCacheService
from quant_system.services.kline_service import KlineService


class FakeCache:
    def __init__(self) -> None:
        self.enabled = True
        self.data: dict[tuple[str, str, int], list[dict]] = {}
        self.invalidated: list[tuple[str, str]] = []

    def get_klines(self, symbol: str, period: str, limit: int) -> list[dict] | None:
        return self.data.get((symbol, period, limit))

    def set_klines(self, symbol: str, period: str, limit: int, rows: list[dict]) -> bool:
        self.data[(symbol, period, limit)] = rows
        return True

    def invalidate_klines(self, symbol: str, period: str) -> int:
        self.invalidated.append((symbol, period))
        keys = [key for key in self.data if key[0] == symbol and key[1] == period]
        for key in keys:
            self.data.pop(key, None)
        return len(keys)


class FakeKlineService(KlineService):
    def __init__(self) -> None:
        self.kline_cache = FakeCache()
        self.db_rows = [
            {
                "symbol": "600487",
                "period": "daily",
                "trade_time": "2024-01-01T00:00:00",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "volume": 100000.0,
                "amount": 1020000.0,
            }
        ]
        self.saved_rows: list[dict] = []

    def _list_klines_from_db(self, symbol: str, period: str = "daily", limit: int = 120) -> list[dict]:
        return self.db_rows[-limit:]

    def save_klines(self, symbol: str, period: str, rows: list[dict]) -> int:
        self.saved_rows = rows
        self.db_rows = rows
        self._refresh_kline_cache(symbol, period)
        return len(rows)

    def _fetch_kline(self, symbol: str, period: str) -> list[dict]:
        return [
            {
                "symbol": symbol,
                "period": period,
                "trade_time": "2024-01-02T00:00:00",
                "open": 10.2,
                "high": 10.8,
                "low": 10.1,
                "close": 10.7,
                "volume": 120000.0,
                "amount": 1284000.0,
            }
        ]

    def _log_sync(self, pool_code: str | None, symbol: str, period: str, status: str, rows_count: int, message: str, **kwargs) -> None:
        return None


def main() -> None:
    service = FakeKlineService()

    rows = service.list_klines("600487", period="daily", limit=120)
    assert len(rows) == 1, rows
    assert service.kline_cache.get_klines("600487", "daily", 120) == rows

    cached_rows = service.list_klines("600487", period="daily", limit=120)
    assert cached_rows == rows

    result = service.sync_symbol_kline("600487", period="daily", tracked=False)
    assert result["status"] == "success", result
    assert result["rows_count"] == 1, result
    assert service.saved_rows[0]["trade_time"] == "2024-01-02T00:00:00"
    assert ("600487", "daily") in service.kline_cache.invalidated
    assert service.kline_cache.get_klines("600487", "daily", 120)[0]["close"] == 10.7

    status = KlineCacheService().status()
    assert "enabled" in status and "available" in status and "status" in status, status
    print("kline cache loop ok")


if __name__ == "__main__":
    main()
