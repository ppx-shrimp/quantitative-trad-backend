from __future__ import annotations

from datetime import datetime, timezone
from statistics import pstdev
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from quant_system.api.pagination import PageParams, PageResult, paginate
from quant_system.db.database import SessionLocal, engine, init_sqlalchemy_tables
from quant_system.db.models import StockFeatureModel
from quant_system.services.kline_service import KlineService
from quant_system.services.stock_pool_service import StockPoolService


class FeatureService:
    def __init__(self) -> None:
        self.kline_service = KlineService()
        self.stock_pool_service = StockPoolService()
        self.initialize()

    def initialize(self) -> None:
        init_sqlalchemy_tables()

    def compute_pool_features(
        self,
        pool_code: str,
        period: str = "daily",
        limit_symbols: int | None = None,
        tracked: bool = True,
    ) -> dict:
        if tracked:
            from quant_system.services.task_execution_service import TaskExecutionService

            return TaskExecutionService().run_tracked(
                task_name="compute_pool_features",
                task_type="feature_compute",
                trigger_type="manual_api",
                params={"pool_code": pool_code, "period": period, "limit_symbols": limit_symbols},
                fn=lambda: self.compute_pool_features(
                    pool_code=pool_code,
                    period=period,
                    limit_symbols=limit_symbols,
                    tracked=False,
                ),
            )
        members = self.stock_pool_service.list_members(pool_code)
        stock_members = [member for member in members if self._is_stock_symbol(member["symbol"])]
        if limit_symbols is not None:
            stock_members = stock_members[:limit_symbols]
        results = [self.compute_symbol_features(member["symbol"], period=period, tracked=False) for member in stock_members]
        return {
            "pool_code": pool_code,
            "period": period,
            "symbol_count": len(stock_members),
            "success_count": sum(1 for item in results if item["status"] == "success"),
            "failed_count": sum(1 for item in results if item["status"] == "failed"),
            "results": results,
        }

    def compute_symbol_features(self, symbol: str, period: str = "daily", tracked: bool = True) -> dict:
        if tracked:
            from quant_system.services.task_execution_service import TaskExecutionService

            return TaskExecutionService().run_tracked(
                task_name="compute_symbol_features",
                task_type="feature_compute",
                trigger_type="manual_api",
                params={"symbol": symbol, "period": period},
                fn=lambda: self.compute_symbol_features(symbol=symbol, period=period, tracked=False),
            )
        normalized_symbol = self._normalize_symbol(symbol)
        try:
            klines = self.kline_service.list_klines(normalized_symbol, period=period, limit=300)
            if len(klines) < 20:
                return {
                    "symbol": normalized_symbol,
                    "period": period,
                    "status": "failed",
                    "rows_count": 0,
                    "message": "K 线数量不足，至少需要 20 条。请先同步 K 线。",
                }
            features = self._compute_features_from_klines(normalized_symbol, period, klines)
            saved_count = self.save_features(features)
            return {
                "symbol": normalized_symbol,
                "period": period,
                "status": "success",
                "rows_count": saved_count,
                "message": "ok",
                "latest": features[-1] if features else None,
            }
        except Exception as exc:
            return {
                "symbol": normalized_symbol,
                "period": period,
                "status": "failed",
                "rows_count": 0,
                "message": str(exc),
            }

    def save_features(self, features: list[dict]) -> int:
        now = self._now()
        values_list = [
            {
                "symbol": item["symbol"],
                "period": item["period"],
                "trade_time": item["trade_time"],
                "close": item.get("close"),
                "ma5": item.get("ma5"),
                "ma10": item.get("ma10"),
                "ma20": item.get("ma20"),
                "ma60": item.get("ma60"),
                "return_1": item.get("return_1"),
                "return_5": item.get("return_5"),
                "return_20": item.get("return_20"),
                "volatility_20": item.get("volatility_20"),
                "volume_ratio_5": item.get("volume_ratio_5"),
                "price_position_20": item.get("price_position_20"),
                "price_position_60": item.get("price_position_60"),
                "trend_direction": item.get("trend_direction"),
                "trend_score": item.get("trend_score"),
                "signal": item.get("signal"),
                "created_at": now,
                "updated_at": now,
                "created_by": "system",
                "updated_by": "system",
            }
            for item in features
        ]
        if not values_list:
            return 0
        with SessionLocal() as session:
            self._upsert_features(session, values_list)
            session.commit()
        return len(values_list)

    def get_latest_feature(self, symbol: str, period: str = "daily") -> dict | None:
        with SessionLocal() as session:
            row = session.scalar(
                select(StockFeatureModel)
                .where(
                    StockFeatureModel.symbol == self._normalize_symbol(symbol),
                    StockFeatureModel.period == period,
                )
                .order_by(StockFeatureModel.trade_time.desc())
                .limit(1)
            )
            return self._feature_to_dict(row) if row else None

    def list_features(self, symbol: str, period: str = "daily", limit: int = 120) -> list[dict]:
        with SessionLocal() as session:
            rows = session.scalars(
                select(StockFeatureModel)
                .where(
                    StockFeatureModel.symbol == self._normalize_symbol(symbol),
                    StockFeatureModel.period == period,
                )
                .order_by(StockFeatureModel.trade_time.desc())
                .limit(limit)
            ).all()
            return [self._feature_to_dict(row) for row in reversed(rows)]

    def list_features_page(self, symbol: str, period: str, page_params: PageParams) -> PageResult:
        normalized = self._normalize_symbol(symbol)
        stmt = (
            select(StockFeatureModel)
            .where(StockFeatureModel.symbol == normalized, StockFeatureModel.period == period)
            .order_by(StockFeatureModel.trade_time.desc())
        )
        with SessionLocal() as session:
            result = paginate(session, stmt, None, page_params, to_dict_fn=self._feature_to_dict)
            result.items = list(reversed(result.items))
            return result

    def analyze_symbol(self, symbol: str, period: str = "daily") -> dict:
        latest = self.get_latest_feature(symbol, period=period)
        if latest is None:
            compute_result = self.compute_symbol_features(symbol, period=period, tracked=False)
            if compute_result["status"] != "success":
                return {
                    "symbol": self._normalize_symbol(symbol),
                    "period": period,
                    "status": "insufficient_data",
                    "summary": compute_result["message"],
                    "scores": {"technical": 50, "trend": 50, "momentum": 50, "risk": 50},
                }
            latest = compute_result["latest"]
        scores = self._score_feature(latest)
        return {
            "symbol": self._normalize_symbol(symbol),
            "period": period,
            "status": "ok",
            "latest_feature": latest,
            "scores": scores,
            "summary": self._build_summary(latest, scores),
        }

    def predict_symbol(self, symbol: str, period: str = "daily") -> dict:
        analysis = self.analyze_symbol(symbol, period=period)
        latest = analysis.get("latest_feature")
        if not latest:
            return {
                "symbol": self._normalize_symbol(symbol),
                "direction": "unknown",
                "confidence": 0.0,
                "target_price": None,
                "horizon": "intraday_to_3_days",
                "reason": analysis.get("summary", "特征数据不足。"),
            }
        direction = self._predict_direction(latest)
        confidence = self._predict_confidence(latest, analysis["scores"])
        close = latest.get("close") or 0
        expected_move = self._expected_move(latest, direction, confidence)
        return {
            "symbol": self._normalize_symbol(symbol),
            "direction": direction,
            "confidence": confidence,
            "target_price": round(close * (1 + expected_move), 2) if close else None,
            "horizon": "intraday_to_3_days",
            "reason": self._prediction_reason(latest, direction, confidence),
            "features": latest,
            "scores": analysis["scores"],
        }

    def _upsert_features(self, session, values_list: list[dict]) -> None:
        dialect = engine.dialect.name
        if dialect == "mysql":
            stmt = mysql_insert(StockFeatureModel).values(values_list)
            session.execute(
                stmt.on_duplicate_key_update(
                    close=getattr(stmt.inserted, "close"),
                    ma5=stmt.inserted.ma5,
                    ma10=stmt.inserted.ma10,
                    ma20=stmt.inserted.ma20,
                    ma60=stmt.inserted.ma60,
                    return_1=getattr(stmt.inserted, "return_1"),
                    return_5=getattr(stmt.inserted, "return_5"),
                    return_20=getattr(stmt.inserted, "return_20"),
                    volatility_20=stmt.inserted.volatility_20,
                    volume_ratio_5=stmt.inserted.volume_ratio_5,
                    price_position_20=stmt.inserted.price_position_20,
                    price_position_60=stmt.inserted.price_position_60,
                    trend_direction=stmt.inserted.trend_direction,
                    trend_score=stmt.inserted.trend_score,
                    signal=stmt.inserted.signal,
                    updated_at=stmt.inserted.updated_at,
                    updated_by=stmt.inserted.updated_by,
                )
            )
            return
        if dialect == "sqlite":
            stmt = sqlite_insert(StockFeatureModel).values(values_list)
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["symbol", "period", "trade_time"],
                    set_={
                        "close": getattr(stmt.excluded, "close"),
                        "ma5": stmt.excluded.ma5,
                        "ma10": stmt.excluded.ma10,
                        "ma20": stmt.excluded.ma20,
                        "ma60": stmt.excluded.ma60,
                        "return_1": getattr(stmt.excluded, "return_1"),
                        "return_5": getattr(stmt.excluded, "return_5"),
                        "return_20": getattr(stmt.excluded, "return_20"),
                        "volatility_20": stmt.excluded.volatility_20,
                        "volume_ratio_5": stmt.excluded.volume_ratio_5,
                        "price_position_20": stmt.excluded.price_position_20,
                        "price_position_60": stmt.excluded.price_position_60,
                        "trend_direction": stmt.excluded.trend_direction,
                        "trend_score": stmt.excluded.trend_score,
                        "signal": stmt.excluded.signal,
                        "updated_at": stmt.excluded.updated_at,
                        "updated_by": stmt.excluded.updated_by,
                    },
                )
            )
            return
        for values in values_list:
            existing = session.scalar(
                select(StockFeatureModel).where(
                    StockFeatureModel.symbol == values["symbol"],
                    StockFeatureModel.period == values["period"],
                    StockFeatureModel.trade_time == values["trade_time"],
                )
            )
            if existing is None:
                session.add(StockFeatureModel(**values))
            else:
                for key, value in values.items():
                    if key != "created_at":
                        setattr(existing, key, value)

    def _feature_to_dict(self, row: StockFeatureModel) -> dict:
        return {
            "id": row.id,
            "symbol": row.symbol,
            "period": row.period,
            "trade_time": row.trade_time,
            "close": row.close,
            "ma5": row.ma5,
            "ma10": row.ma10,
            "ma20": row.ma20,
            "ma60": row.ma60,
            "return_1": row.return_1,
            "return_5": row.return_5,
            "return_20": row.return_20,
            "volatility_20": row.volatility_20,
            "volume_ratio_5": row.volume_ratio_5,
            "price_position_20": row.price_position_20,
            "price_position_60": row.price_position_60,
            "trend_direction": row.trend_direction,
            "trend_score": row.trend_score,
            "signal": row.signal,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "created_by": row.created_by,
            "updated_by": row.updated_by,
        }

    def _compute_features_from_klines(self, symbol: str, period: str, klines: list[dict]) -> list[dict]:
        closes = [self._to_float(row.get("close")) for row in klines]
        volumes = [self._to_float(row.get("volume")) for row in klines]
        highs = [self._to_float(row.get("high")) for row in klines]
        lows = [self._to_float(row.get("low")) for row in klines]
        results = []
        for i, row in enumerate(klines):
            close = closes[i]
            if close is None:
                continue
            item = {
                "symbol": symbol,
                "period": period,
                "trade_time": str(row.get("trade_time") or row.get("date") or row.get("datetime")),
                "close": close,
                "ma5": self._ma(closes, i, 5),
                "ma10": self._ma(closes, i, 10),
                "ma20": self._ma(closes, i, 20),
                "ma60": self._ma(closes, i, 60),
                "return_1": self._return(closes, i, 1),
                "return_5": self._return(closes, i, 5),
                "return_20": self._return(closes, i, 20),
                "volatility_20": self._volatility(closes, i, 20),
                "volume_ratio_5": self._volume_ratio(volumes, i, 5),
                "price_position_20": self._price_position(close, highs, lows, i, 20),
                "price_position_60": self._price_position(close, highs, lows, i, 60),
            }
            item["trend_direction"] = self._trend_direction(item)
            item["trend_score"] = self._trend_score(item)
            item["signal"] = self._signal(item)
            results.append(item)
        return results

    def _ma(self, values: list[float | None], index: int, window: int) -> float | None:
        if index + 1 < window:
            return None
        chunk = [value for value in values[index + 1 - window:index + 1] if value is not None]
        if len(chunk) < window:
            return None
        return round(sum(chunk) / window, 4)

    def _return(self, values: list[float | None], index: int, window: int) -> float | None:
        if index < window or values[index] is None or values[index - window] in (None, 0):
            return None
        return round((values[index] - values[index - window]) / values[index - window], 6)

    def _volatility(self, values: list[float | None], index: int, window: int) -> float | None:
        if index < window:
            return None
        returns = []
        for offset in range(index + 1 - window, index + 1):
            ret = self._return(values, offset, 1)
            if ret is not None:
                returns.append(ret)
        if len(returns) < window - 1:
            return None
        return round(pstdev(returns), 6)

    def _volume_ratio(self, volumes: list[float | None], index: int, window: int) -> float | None:
        if index + 1 < window or volumes[index] is None:
            return None
        history = [value for value in volumes[index + 1 - window:index] if value not in (None, 0)]
        if not history:
            return None
        return round(volumes[index] / (sum(history) / len(history)), 4)

    def _price_position(self, close: float, highs: list[float | None], lows: list[float | None], index: int, window: int) -> float | None:
        if index + 1 < window:
            return None
        high_values = [value for value in highs[index + 1 - window:index + 1] if value is not None]
        low_values = [value for value in lows[index + 1 - window:index + 1] if value is not None]
        if not high_values or not low_values:
            return None
        highest = max(high_values)
        lowest = min(low_values)
        if highest == lowest:
            return 0.5
        return round((close - lowest) / (highest - lowest), 4)

    def _trend_direction(self, item: dict) -> str:
        close = item.get("close")
        ma5 = item.get("ma5")
        ma20 = item.get("ma20")
        ma60 = item.get("ma60")
        if close is None or ma5 is None or ma20 is None:
            return "unknown"
        if ma60 is not None and close > ma5 > ma20 > ma60:
            return "strong_up"
        if close > ma5 > ma20:
            return "up"
        if ma60 is not None and close < ma5 < ma20 < ma60:
            return "strong_down"
        if close < ma5 < ma20:
            return "down"
        return "sideways"

    def _trend_score(self, item: dict) -> float:
        score = 50.0
        for ret_key, weight in [("return_1", 80), ("return_5", 120), ("return_20", 150)]:
            ret = item.get(ret_key)
            if ret is not None:
                score += ret * weight
        price_position = item.get("price_position_20")
        if price_position is not None:
            score += (price_position - 0.5) * 20
        if item.get("trend_direction") in {"strong_up", "up"}:
            score += 10
        elif item.get("trend_direction") in {"strong_down", "down"}:
            score -= 10
        return round(max(0, min(100, score)), 2)

    def _signal(self, item: dict) -> str:
        trend = item.get("trend_direction")
        score = item.get("trend_score") or 50
        volatility = item.get("volatility_20") or 0
        if trend in {"strong_up", "up"} and score >= 65 and volatility < 0.06:
            return "bullish"
        if trend in {"strong_down", "down"} and score <= 40:
            return "bearish"
        return "neutral"

    def _score_feature(self, latest: dict) -> dict:
        trend_score = latest.get("trend_score") or 50
        return_5 = latest.get("return_5") or 0
        volatility = latest.get("volatility_20") or 0
        technical = max(0, min(100, trend_score + return_5 * 100))
        momentum = max(0, min(100, 50 + return_5 * 300))
        risk = max(0, min(100, 100 - volatility * 800))
        return {
            "technical": round(technical, 2),
            "trend": round(trend_score, 2),
            "momentum": round(momentum, 2),
            "risk": round(risk, 2),
        }

    def _build_summary(self, latest: dict, scores: dict) -> str:
        trend = latest.get("trend_direction")
        signal = latest.get("signal")
        ret5 = latest.get("return_5")
        vol = latest.get("volatility_20")
        return f"趋势={trend}，信号={signal}，5周期涨跌幅={self._pct(ret5)}，20周期波动率={self._pct(vol)}，技术分={scores['technical']}。"

    def _predict_direction(self, latest: dict) -> str:
        signal = latest.get("signal")
        trend = latest.get("trend_direction")
        if signal == "bullish" or trend in {"strong_up", "up"}:
            return "up"
        if signal == "bearish" or trend in {"strong_down", "down"}:
            return "down"
        return "flat"

    def _predict_confidence(self, latest: dict, scores: dict) -> float:
        base = 0.45
        trend_score = scores.get("trend", 50)
        confidence = base + abs(trend_score - 50) / 100
        volatility = latest.get("volatility_20") or 0
        if volatility > 0.06:
            confidence -= 0.08
        return round(max(0.3, min(0.82, confidence)), 4)

    def _expected_move(self, latest: dict, direction: str, confidence: float) -> float:
        volatility = latest.get("volatility_20") or 0.01
        move = min(0.08, max(0.005, volatility * confidence))
        if direction == "down":
            return -move
        if direction == "flat":
            return 0.0
        return move

    def _prediction_reason(self, latest: dict, direction: str, confidence: float) -> str:
        return (
            f"基于均线排列、近周期涨跌幅、20周期波动率、成交量变化和价格区间位置判断，"
            f"当前方向={direction}，置信度={confidence}。该预测为规则特征模型，不代表实盘建议。"
        )

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper().split(".")[0]

    def _is_stock_symbol(self, symbol: str) -> bool:
        normalized = self._normalize_symbol(symbol)
        return len(normalized) == 6 and normalized.isdigit() and not normalized.startswith("88")

    def _to_float(self, value: Any) -> float | None:
        if value in (None, "", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _pct(self, value: float | None) -> str:
        if value is None:
            return "未知"
        return f"{value * 100:.2f}%"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
