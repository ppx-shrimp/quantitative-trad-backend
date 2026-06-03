from datetime import datetime, timedelta, timezone

from quant_system.core.config import settings
from quant_system.data.akshare_provider import AkShareMarketDataProvider
from quant_system.domain.models import MarketSnapshot


class MarketDataProvider:
    """行情数据适配层。

    当前默认使用 AkShare 接入 A 股真实数据；如果需要，也可扩展其他 provider。
    """

    def __init__(self) -> None:
        self.provider = AkShareMarketDataProvider()

    def get_stock_list(self) -> list[dict]:
        return self.provider.get_stock_list()

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        latest = self.provider.get_latest_snapshot(symbol)
        price = latest.get("price")
        change_pct = latest.get("change_pct")
        volume = latest.get("volume")
        return MarketSnapshot(
            symbol=symbol.upper(),
            price=float(price) if price is not None else 0.0,
            change_pct=float(change_pct) if change_pct is not None else 0.0,
            volume=int(volume) if volume is not None else 0,
            timestamp=datetime.now(timezone.utc),
        )

    def get_kline(self, symbol: str, days: int = 120) -> list[dict]:
        try:
            rows = self.provider.get_daily_kline(symbol)
        except Exception:
            rows = []
        if not rows:
            snapshot = self.get_snapshot(symbol)
            return [
                {
                    "trade_time": (datetime.now(timezone.utc) - timedelta(days=days - index - 1)).strftime("%Y-%m-%d"),
                    "date": (datetime.now(timezone.utc) - timedelta(days=days - index - 1)).strftime("%Y-%m-%d"),
                    "day": index + 1,
                    "open": round(snapshot.price * (1 + index / 10_000), 2),
                    "high": round(snapshot.price * (1.01 + index / 10_000), 2),
                    "low": round(snapshot.price * (0.99 + index / 10_000), 2),
                    "close": round(snapshot.price * (1 + (index % 7 - 3) / 1_000), 2),
                    "volume": snapshot.volume + index * 1000,
                }
                for index in range(days)
            ]
        rows = rows[-days:]
        enriched = []
        for index, row in enumerate(rows, start=1):
            enriched.append({
                "day": index,
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
                "date": row.get("date"),
            })
        return enriched

    def get_daily_kline(self, symbol: str, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
        return self.provider.get_daily_kline(symbol, start_date=start_date, end_date=end_date)

    def get_minute_kline(self, symbol: str, period: str = "5") -> list[dict]:
        return self.provider.get_minute_kline(symbol, period=period)
