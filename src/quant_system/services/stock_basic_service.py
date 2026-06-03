from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import StockBasicModel


class StockBasicService:
    """股票基础信息服务。

    优先从本地数据库读取全量 A 股基础信息。这个表应该存所有上市公司基础资料，
    外部行情接口只作为初始化或兜底来源，不再作为股票池页面的主数据源。
    """

    def list_stocks(self, keyword: str | None = None, exclude_st: bool = False, market: str | None = None) -> list[dict]:
        init_sqlalchemy_tables()
        with SessionLocal() as session:
            return self._list_stocks(session, keyword=keyword, exclude_st=exclude_st, market=market)

    def _list_stocks(self, session: Session, keyword: str | None = None, exclude_st: bool = False, market: str | None = None) -> list[dict]:
        stmt = select(StockBasicModel).order_by(StockBasicModel.ts_code.asc())
        if exclude_st:
            stmt = stmt.where(~StockBasicModel.name.like("%ST%"), ~StockBasicModel.name.like("%退%"))
        if market and market.strip():
            symbols = self._market_symbol_prefixes(market)
            if symbols:
                stmt = stmt.where(or_(*[StockBasicModel.symbol.like(f"{prefix}%") for prefix in symbols]))
        if keyword and keyword.strip():
            normalized_keyword = keyword.strip()
            like_keyword = f"%{normalized_keyword}%"
            compact_keyword = normalized_keyword.replace(".", "")
            stmt = stmt.where(
                or_(
                    StockBasicModel.ts_code.like(like_keyword),
                    StockBasicModel.symbol.like(f"%{compact_keyword}%"),
                    StockBasicModel.name.like(like_keyword),
                )
            )
        rows = session.scalars(stmt).all()
        return [self._to_dict(row) for row in rows]

    def count(self) -> int:
        init_sqlalchemy_tables()
        with SessionLocal() as session:
            return len(self._list_stocks(session))

    def _market_symbol_prefixes(self, market: str) -> list[str]:
        normalized = market.strip().lower()
        if normalized in {"main", "主板"}:
            return ["000", "001", "002", "003", "600", "601", "603", "605"]
        if normalized in {"gem", "创业板"}:
            return ["300", "301"]
        if normalized in {"star", "科创板"}:
            return ["688", "689"]
        if normalized in {"bj", "北交所"}:
            return ["4", "8"]
        return []

    def _infer_board(self, symbol: str, market: str | None = None) -> str:
        if symbol.startswith(("300", "301")):
            return "创业板"
        if symbol.startswith(("688", "689")):
            return "科创板"
        if symbol.startswith(("4", "8")):
            return "北交所"
        if symbol.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
            return "主板"
        return market or "-"

    def _to_dict(self, row: StockBasicModel) -> dict:
        return {
            "id": row.id,
            "ts_code": row.ts_code,
            "symbol": row.symbol,
            "code": row.symbol,
            "name": row.name,
            "area": row.area or "",
            "industry": row.industry or "",
            "market": self._infer_board(row.symbol, row.market),
            "exchange": row.exchange or "",
            "list_date": row.list_date or "",
            "is_active": bool(row.is_active),
            "source": row.source,
        }
