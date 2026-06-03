from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import StockFeatureModel, StockKlineModel
from quant_system.services.stock_pool_service import StockPoolService


@dataclass(frozen=True)
class ReadinessThresholds:
    min_kline_rows: int = 60
    min_feature_rows: int = 20
    min_overlap_rows: int = 20


class DataReadinessService:
    """诊断真实数据进入回测前是否就绪。"""

    def __init__(self) -> None:
        self.stock_pool_service = StockPoolService()
        self.thresholds = ReadinessThresholds()
        self.initialize()

    def initialize(self) -> None:
        init_sqlalchemy_tables()

    def check_symbol(self, symbol: str, period: str = "daily") -> dict:
        normalized = self._normalize_symbol(symbol)
        with SessionLocal() as session:
            kline = self._kline_stats(session, normalized, period)
            feature = self._feature_stats(session, normalized, period)
        return self._build_symbol_result(normalized, period, kline, feature)

    def check_pool(self, pool_code: str, period: str = "daily", limit_symbols: int | None = None) -> dict:
        members = self.stock_pool_service.list_members(pool_code)
        stock_members = [member for member in members if self._is_stock_symbol(member["symbol"])]
        if limit_symbols is not None:
            stock_members = stock_members[:limit_symbols]
        items = [self.check_symbol(member["symbol"], period=period) for member in stock_members]
        ready_items = [item for item in items if item["ready"]]
        not_ready_items = [item for item in items if not item["ready"]]
        return {
            "scope": "pool",
            "pool_code": pool_code,
            "period": period,
            "symbol_count": len(items),
            "ready_count": len(ready_items),
            "not_ready_count": len(not_ready_items),
            "ready": bool(items) and not not_ready_items,
            "status": "ready" if items and not not_ready_items else "not_ready",
            "thresholds": self._thresholds_dict(),
            "summary": {
                "ready_symbols": [item["symbol"] for item in ready_items],
                "not_ready_symbols": [item["symbol"] for item in not_ready_items],
                "top_issues": self._top_issues(not_ready_items),
            },
            "items": items,
        }

    def _build_symbol_result(self, symbol: str, period: str, kline: dict, feature: dict) -> dict:
        issues = []
        suggestions = []
        if kline["rows_count"] <= 0:
            issues.append("missing_klines")
            suggestions.append("先调用 POST /api/v1/stocks/{symbol}/klines/sync 同步 K 线。")
        elif kline["rows_count"] < self.thresholds.min_kline_rows:
            issues.append("insufficient_klines")
            suggestions.append(f"K 线数量不足，建议至少 {self.thresholds.min_kline_rows} 条后再回测。")

        if feature["rows_count"] <= 0:
            issues.append("missing_features")
            suggestions.append("先调用 POST /api/v1/stocks/{symbol}/features/compute 计算特征。")
        elif feature["rows_count"] < self.thresholds.min_feature_rows:
            issues.append("insufficient_features")
            suggestions.append(f"特征数量不足，建议至少 {self.thresholds.min_feature_rows} 条。")

        overlap = self._estimate_overlap(kline, feature)
        if overlap < self.thresholds.min_overlap_rows:
            issues.append("insufficient_kline_feature_overlap")
            suggestions.append("K 线和特征的时间范围重叠不足，请重新同步 K 线后计算特征。")

        if kline["last_time"] and feature["last_time"] and str(feature["last_time"]) < str(kline["last_time"]):
            issues.append("features_not_latest")
            suggestions.append("特征不是最新，建议重新计算特征。")

        ready = not issues
        return {
            "scope": "symbol",
            "symbol": symbol,
            "period": period,
            "ready": ready,
            "status": "ready" if ready else "not_ready",
            "thresholds": self._thresholds_dict(),
            "kline": kline,
            "feature": feature,
            "estimated_overlap_rows": overlap,
            "issues": issues,
            "suggestions": suggestions,
        }

    def _kline_stats(self, session, symbol: str, period: str) -> dict:
        row = session.execute(
            select(
                func.count(StockKlineModel.id).label("rows_count"),
                func.min(StockKlineModel.trade_time).label("first_time"),
                func.max(StockKlineModel.trade_time).label("last_time"),
                func.max(StockKlineModel.updated_at).label("last_updated_at"),
            ).where(StockKlineModel.symbol == symbol, StockKlineModel.period == period)
        ).one()
        return {
            "rows_count": int(row.rows_count or 0),
            "first_time": row.first_time,
            "last_time": row.last_time,
            "last_updated_at": row.last_updated_at,
            "meets_min_rows": int(row.rows_count or 0) >= self.thresholds.min_kline_rows,
        }

    def _feature_stats(self, session, symbol: str, period: str) -> dict:
        row = session.execute(
            select(
                func.count(StockFeatureModel.id).label("rows_count"),
                func.min(StockFeatureModel.trade_time).label("first_time"),
                func.max(StockFeatureModel.trade_time).label("last_time"),
                func.max(StockFeatureModel.updated_at).label("last_updated_at"),
            ).where(StockFeatureModel.symbol == symbol, StockFeatureModel.period == period)
        ).one()
        return {
            "rows_count": int(row.rows_count or 0),
            "first_time": row.first_time,
            "last_time": row.last_time,
            "last_updated_at": row.last_updated_at,
            "meets_min_rows": int(row.rows_count or 0) >= self.thresholds.min_feature_rows,
        }

    def _estimate_overlap(self, kline: dict, feature: dict) -> int:
        if not kline["first_time"] or not kline["last_time"] or not feature["first_time"] or not feature["last_time"]:
            return 0
        if str(feature["last_time"]) < str(kline["first_time"]) or str(kline["last_time"]) < str(feature["first_time"]):
            return 0
        return min(int(kline["rows_count"] or 0), int(feature["rows_count"] or 0))

    def _top_issues(self, items: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            for issue in item.get("issues", []):
                counts[issue] = counts.get(issue, 0) + 1
        return dict(sorted(counts.items(), key=lambda value: value[1], reverse=True))

    def _thresholds_dict(self) -> dict:
        return {
            "min_kline_rows": self.thresholds.min_kline_rows,
            "min_feature_rows": self.thresholds.min_feature_rows,
            "min_overlap_rows": self.thresholds.min_overlap_rows,
        }

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper().split(".")[0]

    def _is_stock_symbol(self, symbol: str) -> bool:
        normalized = self._normalize_symbol(symbol)
        return len(normalized) == 6 and normalized.isdigit()
