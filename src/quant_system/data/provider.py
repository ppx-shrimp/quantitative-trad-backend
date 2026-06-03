from __future__ import annotations

from typing import Protocol


class MarketDataProviderProtocol(Protocol):
    def get_stock_list(self) -> list[dict]:
        ...

    def get_daily_kline(self, symbol: str, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        ...

    def get_minute_kline(self, symbol: str, period: str = "5") -> list[dict]:
        ...

    def get_latest_snapshot(self, symbol: str) -> dict:
        ...
