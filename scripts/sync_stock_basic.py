from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from quant_system.data.eastmoney_provider import EastMoneyMarketDataProvider
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import StockBasicModel


def upsert_stock_basic() -> int:
    init_sqlalchemy_tables()
    rows = EastMoneyMarketDataProvider().get_stock_list(force_refresh=True)
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with SessionLocal() as session:
        for row in rows:
            ts_code = row.get("ts_code")
            symbol = row.get("symbol") or row.get("code")
            name = row.get("name")
            if not ts_code or not symbol or not name:
                continue
            item = session.scalar(select(StockBasicModel).where(StockBasicModel.ts_code == ts_code))
            if item is None:
                item = StockBasicModel(
                    ts_code=ts_code,
                    symbol=symbol,
                    name=name,
                    created_at=now,
                    updated_at=now,
                    created_by="sync_stock_basic",
                    updated_by="sync_stock_basic",
                )
                session.add(item)
            item.symbol = symbol
            item.name = name
            item.area = row.get("area") or ""
            item.industry = row.get("industry") or ""
            item.market = row.get("market") or ""
            item.exchange = row.get("exchange") or ""
            item.list_date = row.get("list_date") or ""
            item.is_active = bool(row.get("is_active", True))
            item.source = row.get("source") or "eastmoney"
            item.updated_at = now
            item.updated_by = "sync_stock_basic"
            count += 1
        session.commit()
        total = int(session.scalar(select(func.count(StockBasicModel.id))) or 0)
    print(f"stock_basic table total rows: {total}")
    return count


if __name__ == "__main__":
    total = upsert_stock_basic()
    print(f"synced stock_basic rows: {total}")
