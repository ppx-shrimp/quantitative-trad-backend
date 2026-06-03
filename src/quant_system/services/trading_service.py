import json
import threading
from typing import Any

from sqlalchemy import func, select

from quant_system.api.pagination import PageParams, PageResult
from quant_system.api.schemas import ClosePositionRequest, OpenPositionRequest
from quant_system.brokers.base import TradingBroker
from quant_system.brokers.factory import create_broker, describe_broker
from quant_system.core.config import settings
from quant_system.data.market_data import MarketDataProvider
from quant_system.db.database import SessionLocal
from quant_system.db.models import AIAnalysisRecordModel, StockKlineModel
from quant_system.services.feature_service import FeatureService
from quant_system.services.kline_cache_service import KlineCacheService
from quant_system.services.risk_service import RiskService
from quant_system.services.stock_pool_service import StockPoolService
from quant_system.strategies.opening_strategy import OpeningPredictionStrategy


_AUTO_CLOSE_LOCK = threading.Lock()


class TradingService:
    def __init__(self) -> None:
        self.market_data = MarketDataProvider()
        self.strategy = OpeningPredictionStrategy()
        self._broker: TradingBroker | None = None
        self.feature_service = FeatureService()
        self.stock_pool_service = StockPoolService()
        self.risk_service = RiskService()
        self.kline_cache = KlineCacheService()

    @property
    def broker(self) -> TradingBroker:
        """懒加载交易通道，避免服务启动时因数据库不可用而卡死。"""
        if self._broker is None:
            self._broker = create_broker()
        return self._broker

    @broker.setter
    def broker(self, value: TradingBroker) -> None:
        self._broker = value

    def broker_status(self) -> dict:
        return describe_broker(self._broker)

    def _trade_response(
        self,
        *,
        action: str,
        direction: str,
        symbol: str,
        accepted: bool,
        reason: str | None = None,
        market_price: float | None = None,
        requested_price: float | None = None,
        order: dict | None = None,
        guard: dict | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict:
        """统一手动交易接口响应，兼容旧的顶层订单字段。"""
        order_accepted = bool(order.get("accepted", accepted)) if order else accepted
        status = "filled" if order_accepted else "rejected"
        payload: dict[str, Any] = {
            "accepted": order_accepted,
            "status": status,
            "action": action,
            "direction": direction,
            "symbol": symbol.upper(),
            "reason": reason or (order or {}).get("reason"),
            "market_price": round(float(market_price), 4) if market_price is not None else None,
            "requested_price": round(float(requested_price), 4) if requested_price is not None else None,
            "order": order,
            "guard": guard,
            "execution_rules": self._execution_rules(),
        }
        if extra:
            payload.update(extra)
        if order:
            # 兼容已有前端/脚本直接读取 order_id、price、amount 等顶层字段。
            payload.update(order)
            payload["status"] = status
            payload["order"] = order
            if payload.get("reason") is None:
                payload["reason"] = order.get("reason")
        return payload

    def open_position(self, request: OpenPositionRequest) -> dict:
        allowed, reason = self.strategy.should_open(request.symbol)
        snapshot = self.market_data.get_snapshot(request.symbol)
        guard = self.risk_service.check_trade_guard(
            action="manual_open",
            symbol=request.symbol,
            quantity=request.quantity,
            price=snapshot.price,
            broker=self.broker,
        )
        if not guard["allowed"]:
            return self._trade_response(
                action="manual_open",
                direction="buy",
                symbol=request.symbol,
                accepted=False,
                reason=guard.get("reason"),
                market_price=snapshot.price,
                requested_price=request.max_price,
                guard=guard,
            )
        if request.max_price is not None and snapshot.price > request.max_price:
            return self._trade_response(
                action="manual_open",
                direction="buy",
                symbol=request.symbol,
                accepted=False,
                reason="当前价格高于允许买入价",
                market_price=snapshot.price,
                requested_price=request.max_price,
                guard=guard,
                extra={"max_price": request.max_price},
            )
        if not allowed and not request.force:
            return self._trade_response(
                action="manual_open",
                direction="buy",
                symbol=request.symbol,
                accepted=False,
                reason=f"策略未建议买入：{reason}",
                market_price=snapshot.price,
                requested_price=request.max_price,
                guard=guard,
                extra={
                    "requires_confirmation": True,
                    "confirmation_action": "force_manual_buy",
                    "strategy_warning": reason,
                },
            )
        strategy_mode = request.strategy or "manual"
        if strategy_mode in {"manual_open", "manual_close", "opening_prediction"}:
            strategy_mode = "manual"
        audit = self._source_audit_payload(request.audit, fallback_source="manual_open")
        decision = {"source": audit.get("source_type") or "manual_open", "strategy": strategy_mode, "audit": audit}
        if request.force and not allowed:
            decision["force_manual_buy"] = True
            decision["strategy_warning"] = reason
        order = self.broker.buy(
            request.symbol.upper(),
            request.quantity,
            snapshot.price,
            decision=decision,
        )
        order["strategy_reason"] = reason
        order["strategy_allowed"] = allowed
        order["force_manual_buy"] = bool(request.force and not allowed)
        order["guard"] = guard
        self._link_ai_analysis_order(audit, order)
        return self._trade_response(
            action="manual_open",
            direction="buy",
            symbol=request.symbol,
            accepted=bool(order.get("accepted")),
            reason=order.get("reason"),
            market_price=snapshot.price,
            requested_price=request.max_price,
            order=order,
            guard=guard,
        )

    def close_position(self, request: ClosePositionRequest) -> dict:
        snapshot = self.market_data.get_snapshot(request.symbol)
        guard = self.risk_service.check_trade_guard(
            action="manual_close",
            symbol=request.symbol,
            quantity=request.quantity,
            price=snapshot.price,
            broker=self.broker,
        )
        if not guard["allowed"]:
            return self._trade_response(
                action="manual_close",
                direction="sell",
                symbol=request.symbol,
                accepted=False,
                reason=guard.get("reason"),
                market_price=snapshot.price,
                requested_price=request.min_price,
                guard=guard,
            )
        if request.min_price is not None and snapshot.price < request.min_price:
            return self._trade_response(
                action="manual_close",
                direction="sell",
                symbol=request.symbol,
                accepted=False,
                reason="当前价格低于允许卖出价",
                market_price=snapshot.price,
                requested_price=request.min_price,
                guard=guard,
                extra={"min_price": request.min_price},
            )
        audit = self._source_audit_payload(request.audit, fallback_source="manual_close")
        order = self.broker.sell(
            request.symbol.upper(),
            request.quantity,
            snapshot.price,
            decision={"source": audit.get("source_type") or "manual_close", "strategy": "manual", "reason": request.reason, "audit": audit},
        )
        order["close_reason"] = request.reason
        order["guard"] = guard
        self._link_ai_analysis_order(audit, order)
        return self._trade_response(
            action="manual_close",
            direction="sell",
            symbol=request.symbol,
            accepted=bool(order.get("accepted")),
            reason=order.get("reason"),
            market_price=snapshot.price,
            requested_price=request.min_price,
            order=order,
            guard=guard,
        )

    def _source_audit_payload(self, audit: Any, *, fallback_source: str) -> dict[str, Any]:
        if audit is None:
            return {"source_type": fallback_source}
        if hasattr(audit, "model_dump"):
            payload = audit.model_dump(exclude_none=True)
        elif isinstance(audit, dict):
            payload = {key: value for key, value in audit.items() if value is not None}
        else:
            payload = {}
        payload["source_type"] = str(payload.get("source_type") or fallback_source).strip() or fallback_source
        if payload.get("source_memo"):
            payload["source_memo"] = str(payload["source_memo"]).strip()[:1000]
        payload["audit_version"] = "order_source_v1"
        return payload

    def _link_ai_analysis_order(self, audit: dict[str, Any], order: dict[str, Any]) -> None:
        analysis_id = str(audit.get("source_id") or "").strip()
        if not analysis_id or not analysis_id.startswith("ai_"):
            return
        order_id = str(order.get("order_id") or "").strip()
        if not order_id:
            return
        order_summary = {
            "order_id": order_id,
            "status": order.get("status") or ("filled" if order.get("accepted") else "rejected"),
            "side": order.get("side"),
            "symbol": order.get("symbol"),
            "quantity": order.get("quantity"),
            "price": order.get("price"),
            "amount": order.get("amount"),
            "source_type": audit.get("source_type"),
            "source_action": audit.get("source_action"),
        }
        with SessionLocal() as session:
            record = session.scalar(select(AIAnalysisRecordModel).where(AIAnalysisRecordModel.analysis_id == analysis_id))
            if record is None:
                return
            record.linked_order_id = order_id
            record.linked_order_status = str(order_summary.get("status") or "") or None
            record.linked_order_side = str(order.get("side") or "") or None
            try:
                record.linked_order_quantity = int(order.get("quantity")) if order.get("quantity") is not None else None
            except (TypeError, ValueError):
                record.linked_order_quantity = None
            try:
                record.linked_order_price = float(order.get("price")) if order.get("price") is not None else None
            except (TypeError, ValueError):
                record.linked_order_price = None
            record.linked_order_at = order.get("created_at") or order.get("time") or order.get("created_time")
            record.linked_order_json = json.dumps(order_summary, ensure_ascii=False, default=str)
            session.commit()

    def list_positions(self) -> dict:
        positions = self._get_positions_with_pnl()
        return {
            "positions": positions,
            "cash": self.broker.cash,
            "mode": settings.trade_mode,
            "backend": settings.paper_broker_backend,
            "execution_rules": self._execution_rules(),
        }

    def get_positions_pnl(self) -> dict:
        """持仓浮盈浮亏详情，含汇总统计。"""
        positions = self._get_positions_with_pnl()
        account = self.broker.account_summary() if hasattr(self.broker, "account_summary") else {}
        cash = float(account.get("cash", self.broker.cash))
        initial_cash = float(account.get("initial_cash", settings.default_cash))
        realized_pnl = float(account.get("realized_pnl", 0))
        total_market_value = sum(p.get("market_value", 0) or 0 for p in positions)
        total_unrealized_pnl = sum(p.get("unrealized_pnl", 0) or 0 for p in positions)
        total_cost = sum((p.get("market_cost", 0) or 0) for p in positions)
        total_asset = cash + total_market_value
        total_pnl = realized_pnl + total_unrealized_pnl
        total_pnl_pct = round(total_pnl / initial_cash * 100, 2) if initial_cash else 0
        return {
            "positions": positions,
            "summary": {
                "initial_cash": round(initial_cash, 2),
                "cash": round(cash, 2),
                "total_market_value": round(total_market_value, 2),
                "total_cost": round(total_cost, 2),
                "total_asset": round(total_asset, 2),
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round(total_unrealized_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": total_pnl_pct,
                "position_count": len(positions),
            },
            "mode": settings.trade_mode,
            "backend": settings.paper_broker_backend,
            "execution_rules": self._execution_rules(),
        }

    def list_orders(self, symbol: str | None = None, limit: int = 100) -> dict:
        if not hasattr(self.broker, "list_orders"):
            return {"count": 0, "items": [], "message": "当前内存模拟券商不支持订单落库，请将 QUANT_PAPER_BROKER_BACKEND 设置为 sqlite。"}
        items = self.broker.list_orders(symbol=symbol, limit=limit)
        return {"count": len(items), "items": items, "mode": settings.trade_mode, "backend": settings.paper_broker_backend, "execution_rules": self._execution_rules()}

    def list_orders_page(
        self,
        page_params: PageParams,
        symbol: str | None = None,
        side: str | None = None,
        status: str | None = None,
        strategy_mode: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict | PageResult:
        if not hasattr(self.broker, "list_orders_page"):
            return {"count": 0, "items": [], "message": "当前内存模拟券商不支持订单落库，请将 QUANT_PAPER_BROKER_BACKEND 设置为 sqlite。"}
        result = self.broker.list_orders_page(
            page_params,
            symbol=symbol,
            side=side,
            status=status,
            strategy_mode=strategy_mode,
            start_date=start_date,
            end_date=end_date,
        )
        data = result.to_dict()
        data["mode"] = settings.trade_mode
        data["backend"] = settings.paper_broker_backend
        data["execution_rules"] = self._execution_rules()
        return data

    def list_cash_flows(self, limit: int = 100) -> dict:
        if not hasattr(self.broker, "list_cash_flows"):
            return {"count": 0, "items": [], "message": "当前内存模拟券商不支持资金流水落库，请将 QUANT_PAPER_BROKER_BACKEND 设置为 sqlite。"}
        items = self.broker.list_cash_flows(limit=limit)
        return {"count": len(items), "items": items, "mode": settings.trade_mode, "backend": settings.paper_broker_backend}

    def list_cash_flows_page(self, page_params: PageParams) -> dict | PageResult:
        if not hasattr(self.broker, "list_cash_flows_page"):
            return {"count": 0, "items": [], "message": "当前内存模拟券商不支持资金流水落库，请将 QUANT_PAPER_BROKER_BACKEND 设置为 sqlite。"}
        result = self.broker.list_cash_flows_page(page_params)
        data = result.to_dict()
        data["mode"] = settings.trade_mode
        data["backend"] = settings.paper_broker_backend
        data["execution_rules"] = self._execution_rules()
        return data

    def get_pnl_stats(
        self,
        strategy_mode: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        if not hasattr(self.broker, "get_pnl_stats"):
            return {"message": "当前内存模拟券商不支持盈亏统计，请将 QUANT_PAPER_BROKER_BACKEND 设置为 sqlalchemy。"}
        stats = self.broker.get_pnl_stats(
            strategy_mode=strategy_mode,
            start_date=start_date,
            end_date=end_date,
        )
        stats["mode"] = settings.trade_mode
        stats["backend"] = settings.paper_broker_backend
        return stats

    def _get_positions_with_pnl(self) -> list[dict]:
        """获取持仓列表并附带浮盈浮亏数据。"""
        positions = self.broker.list_positions()
        if not positions:
            return positions
        symbols = [str(pos["symbol"]) for pos in positions if pos.get("symbol")]
        price_map, price_meta = self._build_position_price_map(symbols)
        result = []
        for pos in positions:
            symbol = pos["symbol"]
            avg_price = pos.get("avg_price", 0)
            quantity = pos.get("quantity", 0)
            price_info = price_map.get(symbol, {})
            current_price = price_info.get("price")
            if current_price is not None and current_price > 0 and avg_price > 0:
                market_value = round(current_price * quantity, 2)
                market_cost = round(avg_price * quantity, 2)
                unrealized_pnl = round(market_value - market_cost, 2)
                unrealized_pnl_pct = round((current_price - avg_price) / avg_price * 100, 2)
            else:
                market_value = None
                market_cost = round(avg_price * quantity, 2) if avg_price > 0 else 0
                unrealized_pnl = None
                unrealized_pnl_pct = None
            pos["current_price"] = current_price
            pos["price_source"] = price_info.get("source")
            pos["price_time"] = price_info.get("trade_time")
            pos["market_value"] = market_value
            pos["market_cost"] = market_cost
            pos["unrealized_pnl"] = unrealized_pnl
            pos["unrealized_pnl_pct"] = unrealized_pnl_pct
            result.append(pos)
        if price_meta:
            for pos in result:
                pos["price_meta"] = price_meta
        return result

    def _build_position_price_map(self, symbols: list[str]) -> tuple[dict[str, dict], dict]:
        """构建持仓最新价映射：优先缓存/本地 K 线，最后才批量行情降级。

        持仓页和账户页可能一次性查询大量股票。如果每只股票都实时拉行情，容易把外部
        数据源和本服务打满。这里先从 Redis K 线缓存和本地 stock_klines 表取最近 close；
        只有本地完全缺价的股票，才通过一次 get_stock_list() 批量行情兜底。
        """
        normalized_symbols = [self._normalize_symbol(symbol) for symbol in symbols if symbol]
        if not normalized_symbols:
            return {}, {"source_priority": ["redis_kline", "local_kline", "market_list_fallback"]}

        price_map: dict[str, dict] = {}
        cache_hits = 0
        for symbol in normalized_symbols:
            cached = self._get_latest_price_from_kline_cache(symbol)
            if cached is not None:
                price_map[symbol] = cached
                cache_hits += 1

        missing_symbols = [symbol for symbol in normalized_symbols if symbol not in price_map]
        local_map = self._get_latest_prices_from_local_klines(missing_symbols)
        price_map.update(local_map)

        missing_symbols = [symbol for symbol in normalized_symbols if symbol not in price_map]
        fallback_map = self._get_latest_prices_from_market_list(missing_symbols)
        price_map.update(fallback_map)

        return price_map, {
            "source_priority": ["redis_kline", "local_kline", "market_list_fallback"],
            "symbol_count": len(normalized_symbols),
            "cache_hit_count": cache_hits,
            "local_kline_count": len(local_map),
            "fallback_count": len(fallback_map),
            "missing_count": len([symbol for symbol in normalized_symbols if symbol not in price_map]),
        }

    def _get_latest_price_from_kline_cache(self, symbol: str) -> dict | None:
        for period in ("minute", "daily"):
            cached_rows = self.kline_cache.get_klines(symbol, period, 120)
            if not cached_rows:
                continue
            for row in reversed(cached_rows):
                price = self._to_positive_float(row.get("close"))
                if price is not None:
                    return {
                        "price": price,
                        "source": f"redis_{period}_kline",
                        "trade_time": row.get("trade_time") or row.get("datetime") or row.get("date"),
                    }
        return None

    def _get_latest_prices_from_local_klines(self, symbols: list[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        normalized_symbols = list(dict.fromkeys(self._normalize_symbol(symbol) for symbol in symbols if symbol))
        result: dict[str, dict] = {}
        with SessionLocal() as session:
            for period in ("minute", "daily"):
                pending = [symbol for symbol in normalized_symbols if symbol not in result]
                if not pending:
                    break
                latest_subquery = (
                    select(
                        StockKlineModel.symbol.label("symbol"),
                        func.max(StockKlineModel.trade_time).label("latest_time"),
                    )
                    .where(
                        StockKlineModel.symbol.in_(pending),
                        StockKlineModel.period == period,
                        StockKlineModel.close.isnot(None),
                    )
                    .group_by(StockKlineModel.symbol)
                    .subquery()
                )
                rows = session.execute(
                    select(StockKlineModel)
                    .join(
                        latest_subquery,
                        (StockKlineModel.symbol == latest_subquery.c.symbol)
                        & (StockKlineModel.trade_time == latest_subquery.c.latest_time),
                    )
                    .where(StockKlineModel.period == period)
                ).scalars().all()
                for row in rows:
                    price = self._to_positive_float(row.close)
                    if price is None:
                        continue
                    result[row.symbol] = {
                        "price": price,
                        "source": f"local_{period}_kline",
                        "trade_time": row.trade_time,
                    }
        return result

    def _get_latest_prices_from_market_list(self, symbols: list[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        wanted = {self._normalize_symbol(symbol) for symbol in symbols if symbol}
        if not wanted:
            return {}
        try:
            rows = self.market_data.get_stock_list()
        except Exception:
            return {}
        result: dict[str, dict] = {}
        for row in rows:
            symbol = self._normalize_symbol(str(row.get("symbol") or row.get("ts_code") or ""))
            if symbol not in wanted:
                continue
            price = self._to_positive_float(row.get("price"))
            if price is None:
                continue
            result[symbol] = {
                "price": price,
                "source": "market_list_fallback",
                "trade_time": None,
            }
        return result

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper().split(".")[0]

    def _to_positive_float(self, value: Any) -> float | None:
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        return price

    def _execution_rules(self) -> dict:
        return {
            "slippage_bps": settings.paper_slippage_bps,
            "commission_rate": settings.paper_commission_rate,
            "min_commission": settings.paper_min_commission,
            "stamp_duty_rate": settings.paper_stamp_duty_rate,
            "transfer_fee_rate": settings.paper_transfer_fee_rate,
            "price_source": "manual接口使用实时行情快照；持仓浮盈浮亏和自动平仓风控优先使用 Redis K 线缓存/本地最近 K 线 close，仅缺价时降级批量行情；broker 层按滑点生成实际成交价",
            "auto_close_tiers": {
                "stop_loss": ["下跌超过5%减半仓", "下跌超过10%清仓"],
                "take_profit": ["上涨超过10%减三分之一仓位", "上涨超过20%减半仓", "上涨超过50%清仓"],
                "base_price": "持仓成本价",
            },
        }

    def account_summary(self) -> dict:
        if hasattr(self.broker, "account_summary"):
            summary = self.broker.account_summary()
            # 用含浮盈浮亏的持仓替换原始持仓
            summary["positions"] = self._get_positions_with_pnl()
            # 追加浮盈浮亏汇总
            total_unrealized = sum(p.get("unrealized_pnl", 0) or 0 for p in summary["positions"])
            total_market_value = sum(p.get("market_value", 0) or 0 for p in summary["positions"])
            cash = float(summary.get("cash", 0))
            summary["total_market_value"] = round(total_market_value, 2)
            summary["total_asset"] = round(cash + total_market_value, 2)
            summary["unrealized_pnl"] = round(total_unrealized, 2)
            summary["total_pnl"] = round(float(summary.get("realized_pnl", 0)) + total_unrealized, 2)
            summary["mode"] = settings.trade_mode
            summary["backend"] = settings.paper_broker_backend
            summary["execution_rules"] = self._execution_rules()
            return summary
        return self.list_positions()

    def get_strategy_evaluation(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """策略表现评估：各策略深度分析与横向对比。"""
        if not hasattr(self.broker, "get_strategy_evaluation"):
            return {"message": "当前内存模拟券商不支持策略评估，请将 QUANT_PAPER_BROKER_BACKEND 设置为 sqlalchemy。"}
        result = self.broker.get_strategy_evaluation(
            start_date=start_date,
            end_date=end_date,
        )
        result["mode"] = settings.trade_mode
        result["backend"] = settings.paper_broker_backend
        return result

    def get_daily_report(self, date: str | None = None) -> dict:
        """交易日报：汇总指定日期的交易、持仓和账户状态。"""
        if not hasattr(self.broker, "get_daily_report"):
            return {"message": "当前内存模拟券商不支持交易日报，请将 QUANT_PAPER_BROKER_BACKEND 设置为 sqlalchemy。"}

        report = self.broker.get_daily_report(date=date)

        # 附加持仓浮盈浮亏
        positions = self._get_positions_with_pnl()
        total_market_value = sum(p.get("market_value", 0) or 0 for p in positions)
        total_unrealized_pnl = sum(p.get("unrealized_pnl", 0) or 0 for p in positions)

        # 账户快照
        account = self.broker.account_summary() if hasattr(self.broker, "account_summary") else {}
        cash = float(account.get("cash", self.broker.cash))
        initial_cash = float(account.get("initial_cash", settings.default_cash))
        realized_pnl = float(account.get("realized_pnl", 0))
        total_asset = cash + total_market_value

        report["account_snapshot"] = {
            "cash": round(cash, 2),
            "total_market_value": round(total_market_value, 2),
            "total_asset": round(total_asset, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_pnl": round(realized_pnl + total_unrealized_pnl, 2),
            "position_count": len(positions),
        }
        report["positions"] = positions
        report["mode"] = settings.trade_mode
        report["backend"] = settings.paper_broker_backend
        report["execution_rules"] = self._execution_rules()
        return report

    def reset_paper_account(self) -> dict:
        if hasattr(self.broker, "reset"):
            summary = self.broker.reset()
            summary["mode"] = settings.trade_mode
            summary["backend"] = settings.paper_broker_backend
            summary["execution_rules"] = self._execution_rules()
            return summary
        self._broker = create_broker()
        return self.list_positions()

    def run_opening_auto_buy(
        self,
        pools: list[str] | None = None,
        limit_symbols: int | None = None,
        strategy_mode: str = "strict",
        tracked: bool = True,
    ) -> dict:
        if tracked:
            from quant_system.services.task_execution_service import TaskExecutionService

            return TaskExecutionService().run_tracked(
                task_name="manual_auto_buy",
                task_type="auto_trade",
                trigger_type="manual_api",
                params={"pools": pools, "limit_symbols": limit_symbols, "strategy_mode": strategy_mode},
                fn=lambda: self.run_opening_auto_buy(
                    pools=pools,
                    limit_symbols=limit_symbols,
                    strategy_mode=strategy_mode,
                    tracked=False,
                ),
            )
        rule = self._get_strategy_rule(strategy_mode)
        pool_codes = pools or settings.auto_trade_pools
        candidates = self._collect_candidate_symbols(pool_codes, limit_symbols=limit_symbols)
        results = []
        for symbol in candidates:
            decision = self._evaluate_open_candidate(symbol, rule=rule)
            if decision["allowed"]:
                guard = self.risk_service.check_trade_guard(
                    action="auto_buy",
                    symbol=symbol,
                    quantity=settings.auto_trade_quantity,
                    price=decision["market_price"],
                    broker=self.broker,
                )
                if not guard["allowed"]:
                    results.append({"accepted": False, "symbol": symbol.upper(), "reason": guard["reason"], "decision": decision, "guard": guard})
                    continue
                order = self._broker_buy(symbol.upper(), settings.auto_trade_quantity, decision["market_price"], decision=decision)
                order["decision"] = decision
                order["guard"] = guard
                results.append(order)
            else:
                results.append({"accepted": False, "symbol": symbol.upper(), "reason": decision["reason"], "decision": decision})
        return {
            "mode": settings.trade_mode,
            "pools": pool_codes,
            "strategy_mode": rule["mode"],
            "rule": rule,
            "candidate_count": len(candidates),
            "accepted_count": sum(1 for item in results if item.get("accepted")),
            "rejected_count": sum(1 for item in results if not item.get("accepted")),
            "cash": self.broker.cash,
            "positions": self.broker.list_positions(),
            "execution_rules": self._execution_rules(),
            "results": results,
        }

    def run_scheduled_auto_close(
        self,
        strategy_mode: str = "strict",
        scheduled: bool | None = None,
        tracked: bool = True,
        mode: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        close_mode = mode or ("force_close_all" if scheduled else "risk_scan")
        if close_mode not in {"risk_scan", "force_close_all"}:
            close_mode = "risk_scan"
        if tracked:
            from quant_system.services.task_execution_service import TaskExecutionService

            return TaskExecutionService().run_tracked(
                task_name="manual_auto_close",
                task_type="auto_trade",
                trigger_type="manual_api",
                params={"strategy_mode": strategy_mode, "mode": close_mode, "dry_run": dry_run},
                fn=lambda: self.run_scheduled_auto_close(
                    strategy_mode=strategy_mode,
                    tracked=False,
                    mode=close_mode,
                    dry_run=dry_run,
                ),
            )
        if not _AUTO_CLOSE_LOCK.acquire(blocking=False):
            return {
                "mode": settings.trade_mode,
                "close_mode": close_mode,
                "dry_run": dry_run,
                "accepted": False,
                "reason": "已有平仓任务正在执行，本次跳过以防止重复卖出",
                "position_count": 0,
                "closed_count": 0,
                "kept_count": 0,
                "results": [],
            }
        try:
            return self._run_auto_close_locked(strategy_mode=strategy_mode, close_mode=close_mode, dry_run=dry_run)
        finally:
            _AUTO_CLOSE_LOCK.release()

    def _run_auto_close_locked(self, strategy_mode: str, close_mode: str, dry_run: bool) -> dict:
        rule = self._get_strategy_rule(strategy_mode)
        positions = self.broker.list_positions()
        results = []
        for position in positions:
            symbol = position["symbol"]
            decision = self._evaluate_close_candidate(symbol, rule=rule, close_mode=close_mode)
            if decision["allowed"]:
                close_quantity = decision.get("close_quantity") or position.get("quantity")
                guard = self.risk_service.check_trade_guard(
                    action="auto_close",
                    symbol=symbol,
                    quantity=close_quantity,
                    price=decision["market_price"],
                    broker=self.broker,
                )
                if not guard["allowed"]:
                    results.append({"accepted": False, "symbol": symbol, "reason": guard["reason"], "decision": decision, "guard": guard})
                    continue
                if dry_run:
                    results.append(
                        {
                            "accepted": True,
                            "preview": True,
                            "symbol": symbol,
                            "side": "sell",
                            "quantity": close_quantity,
                            "price": decision["market_price"],
                            "reason": decision["reason"],
                            "decision": decision,
                            "guard": guard,
                        }
                    )
                    continue
                order = self._broker_sell(symbol, close_quantity, decision["market_price"], decision=decision)
                order["decision"] = decision
                order["guard"] = guard
                results.append(order)
            else:
                results.append({"accepted": False, "symbol": symbol, "reason": decision["reason"], "decision": decision})
        return {
            "mode": settings.trade_mode,
            "close_mode": close_mode,
            "dry_run": dry_run,
            "strategy_mode": rule["mode"],
            "rule": rule,
            "position_count": len(positions),
            "closed_count": sum(1 for item in results if item.get("accepted")),
            "kept_count": sum(1 for item in results if not item.get("accepted")),
            "cash": self.broker.cash,
            "positions": self.broker.list_positions(),
            "execution_rules": self._execution_rules(),
            "results": results,
        }

    def _collect_candidate_symbols(self, pool_codes: list[str], limit_symbols: int | None = None) -> list[str]:
        symbols = []
        seen = set()
        blacklist = {self._normalize_symbol(item["symbol"]) for item in self.stock_pool_service.list_members("blacklist")}
        for pool_code in pool_codes:
            try:
                members = self.stock_pool_service.list_members(pool_code)
            except ValueError:
                continue
            for member in members:
                symbol = self._normalize_symbol(member["symbol"])
                if not self._is_stock_symbol(symbol) or symbol in seen or symbol in blacklist:
                    continue
                seen.add(symbol)
                symbols.append(symbol)
                if limit_symbols is not None and len(symbols) >= limit_symbols:
                    return symbols
        return symbols

    def _evaluate_open_candidate(self, symbol: str, rule: dict[str, Any]) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        if self.broker.has_position(normalized_symbol):
            return {"allowed": False, "symbol": normalized_symbol, "reason": "已有持仓，不重复买入"}
        if self.broker.position_count() >= settings.auto_trade_max_positions:
            return {"allowed": False, "symbol": normalized_symbol, "reason": "已达到最大持仓数量"}

        prediction = self.feature_service.predict_symbol(normalized_symbol, period="daily")
        features = prediction.get("features") or {}
        scores = prediction.get("scores") or {}
        direction = prediction.get("direction")
        confidence = prediction.get("confidence") or 0
        trend_score = scores.get("trend") or features.get("trend_score") or 0
        volatility = features.get("volatility_20") or 0
        price_position = features.get("price_position_60") or features.get("price_position_20") or 0
        volume_ratio = features.get("volume_ratio_5") or 0
        signal = features.get("signal")
        market_price = features.get("close") or prediction.get("target_price") or 0
        if market_price <= 0:
            return {"allowed": False, "symbol": normalized_symbol, "reason": "本地特征价格不可用", "market_price": market_price, "prediction": prediction}
        if market_price > rule["max_price"]:
            return {"allowed": False, "symbol": normalized_symbol, "reason": "当前价格超过自动买入上限", "market_price": market_price, "prediction": prediction}

        allowed_directions = rule["allowed_directions"]
        checks = [
            (direction in allowed_directions, "预测方向不符合当前策略模式"),
            (confidence >= rule["min_confidence"], "预测置信度不足"),
            (trend_score >= rule["min_trend_score"] or signal in rule["allowed_signals"], "趋势分或信号未达标"),
            (volatility <= rule["max_volatility"], "20周期波动率过高"),
            (rule["min_price_position"] <= price_position <= rule["max_price_position"], "价格区间位置不适合追入"),
            (volume_ratio >= rule["min_volume_ratio"], "成交量配合不足"),
        ]
        failed = [reason for ok, reason in checks if not ok]
        return {
            "allowed": not failed,
            "symbol": normalized_symbol,
            "reason": "通过自动买入规则" if not failed else "；".join(failed),
            "market_price": market_price,
            "prediction": prediction,
            "rule_snapshot": {
                "direction": direction,
                "confidence": confidence,
                "trend_score": trend_score,
                "volatility_20": volatility,
                "price_position": price_position,
                "volume_ratio_5": volume_ratio,
                "signal": signal,
                "strategy_mode": rule["mode"],
            },
        }

    def _evaluate_close_candidate(self, symbol: str, rule: dict[str, Any], scheduled: bool = False, close_mode: str | None = None) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        position = self.broker.get_position(normalized_symbol)
        if position is None:
            return {"allowed": False, "symbol": normalized_symbol, "reason": "没有持仓"}

        effective_close_mode = close_mode or ("force_close_all" if scheduled else "risk_scan")
        force_close_all = effective_close_mode == "force_close_all"
        price_info = self._build_position_price_map([normalized_symbol])[0].get(normalized_symbol, {})
        market_price = price_info.get("price") or position.avg_price
        pnl_pct = (market_price - position.avg_price) / position.avg_price if position.avg_price else 0
        quantity = int(position.quantity)
        trigger_key = self._tiered_trigger_key(pnl_pct, force_close_all=force_close_all)
        triggered_keys = self._get_triggered_close_keys(normalized_symbol)
        if trigger_key and trigger_key in triggered_keys:
            return {
                "allowed": False,
                "symbol": normalized_symbol,
                "reason": "当前仓位档位已触发过，本次跳过重复平仓",
                "market_price": market_price,
                "price_source": price_info.get("source") or "position_avg_price_fallback",
                "price_time": price_info.get("trade_time"),
                "avg_price": position.avg_price,
                "pnl_pct": round(pnl_pct, 6),
                "position_quantity": quantity,
                "close_quantity": 0,
                "close_ratio": 0,
                "trigger_key": trigger_key,
                "triggered_keys": sorted(triggered_keys),
            }
        close_quantity = self._calculate_tiered_close_quantity(quantity, pnl_pct, force_close_all=force_close_all)
        tier_reason = self._tiered_close_reason(pnl_pct, force_close_all=force_close_all)

        prediction: dict[str, Any] = {}
        features: dict[str, Any] = {}
        scores: dict[str, Any] = {}
        direction = None
        signal = None
        trend_score = 50
        try:
            prediction = self.feature_service.predict_symbol(normalized_symbol, period="daily")
            features = prediction.get("features") or {}
            scores = prediction.get("scores") or {}
            direction = prediction.get("direction")
            signal = features.get("signal")
            trend_score = scores.get("trend") or features.get("trend_score") or 50
        except Exception as exc:
            prediction = {"error": str(exc)}

        reasons = []
        if tier_reason:
            reasons.append(tier_reason)
        if close_quantity <= 0 and (direction == "down" or signal == "bearish"):
            close_quantity = quantity
            reasons.append("预测转弱，清仓处理")
        if close_quantity <= 0 and direction == "flat" and trend_score < rule["min_trend_score"]:
            close_quantity = quantity
            reasons.append("趋势分不足且方向走平，清仓处理")

        return {
            "allowed": close_quantity > 0,
            "symbol": normalized_symbol,
            "reason": "；".join(reasons) if reasons else "未触发平仓规则",
            "close_mode": effective_close_mode,
            "close_reason_detail": self._build_close_reason_detail(
                symbol=normalized_symbol,
                reason="；".join(reasons) if reasons else "未触发平仓规则",
                avg_price=position.avg_price,
                market_price=market_price,
                pnl_pct=pnl_pct,
                close_quantity=close_quantity,
                position_quantity=quantity,
                trigger_key=trigger_key,
                price_source=price_info.get("source") or "position_avg_price_fallback",
            ),
            "market_price": market_price,
            "price_source": price_info.get("source") or "position_avg_price_fallback",
            "price_time": price_info.get("trade_time"),
            "avg_price": position.avg_price,
            "pnl_pct": round(pnl_pct, 6),
            "position_quantity": quantity,
            "close_quantity": close_quantity,
            "close_ratio": round(close_quantity / quantity, 6) if quantity else 0,
            "trigger_key": trigger_key,
            "triggered_keys": sorted(triggered_keys),
            "trigger_recorded": bool(trigger_key and close_quantity > 0),
            "prediction": prediction,
            "rule_snapshot": {
                "direction": direction,
                "signal": signal,
                "trend_score": trend_score,
                "take_profit_tiers": rule["take_profit_tiers"],
                "stop_loss_tiers": rule["stop_loss_tiers"],
                "strategy_mode": rule["mode"],
                "close_mode": effective_close_mode,
                "force_close_all": force_close_all,
            },
        }

    def _get_triggered_close_keys(self, symbol: str) -> set[str]:
        if not hasattr(self.broker, "list_orders"):
            return set()
        try:
            orders = self.broker.list_orders(symbol=symbol, limit=500)
        except Exception:
            return set()
        keys: set[str] = set()
        for order in orders:
            if order.get("side") != "sell" or order.get("status") != "filled":
                continue
            decision = order.get("decision") or {}
            trigger_key = decision.get("trigger_key")
            if isinstance(trigger_key, str) and trigger_key:
                keys.add(trigger_key)
        return keys

    def _tiered_trigger_key(self, pnl_pct: float, scheduled: bool = False, force_close_all: bool = False) -> str | None:
        if scheduled or force_close_all:
            return "scheduled_force_close"
        if pnl_pct <= -0.10:
            return "loss_10_clear_all"
        if pnl_pct <= -0.05:
            return "loss_5_reduce_half"
        if pnl_pct >= 0.50:
            return "profit_50_clear_all"
        if pnl_pct >= 0.20:
            return "profit_20_reduce_half"
        if pnl_pct >= 0.10:
            return "profit_10_reduce_one_third"
        return None

    def _round_sell_quantity(self, quantity: int, ratio: float) -> int:
        if quantity <= 100:
            return quantity
        close_quantity = int(quantity * ratio)
        close_quantity = max(100, (close_quantity // 100) * 100)
        return min(quantity, max(0, close_quantity))

    def _calculate_tiered_close_quantity(self, quantity: int, pnl_pct: float, scheduled: bool = False, force_close_all: bool = False) -> int:
        if quantity <= 0:
            return 0
        if scheduled or force_close_all:
            return quantity
        if pnl_pct <= -0.10:
            return quantity
        if pnl_pct <= -0.05:
            return self._round_sell_quantity(quantity, 0.5)
        if pnl_pct >= 0.50:
            return quantity
        if pnl_pct >= 0.20:
            return self._round_sell_quantity(quantity, 0.5)
        if pnl_pct >= 0.10:
            return self._round_sell_quantity(quantity, 1 / 3)
        return 0

    def _tiered_close_reason(self, pnl_pct: float, scheduled: bool = False, force_close_all: bool = False) -> str | None:
        if scheduled or force_close_all:
            return "定时/强制清仓任务触发，清仓处理"
        if pnl_pct <= -0.10:
            return "下跌超过10%，清仓处理"
        if pnl_pct <= -0.05:
            return "下跌超过5%，减半仓处理"
        if pnl_pct >= 0.50:
            return "上涨超过50%，清仓处理"
        if pnl_pct >= 0.20:
            return "上涨超过20%，减半仓处理"
        if pnl_pct >= 0.10:
            return "上涨超过10%，减三分之一仓位"
        return None

    def _build_close_reason_detail(
        self,
        *,
        symbol: str,
        reason: str,
        avg_price: float,
        market_price: float,
        pnl_pct: float,
        close_quantity: int,
        position_quantity: int,
        trigger_key: str | None,
        price_source: str,
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "reason": reason,
            "trigger_key": trigger_key,
            "avg_price": round(float(avg_price), 4),
            "market_price": round(float(market_price), 4),
            "pnl_pct": round(float(pnl_pct), 6),
            "pnl_pct_display": f"{pnl_pct * 100:.2f}%",
            "position_quantity": position_quantity,
            "close_quantity": close_quantity,
            "remaining_quantity": max(position_quantity - close_quantity, 0),
            "close_ratio": round(close_quantity / position_quantity, 6) if position_quantity else 0,
            "price_source": price_source,
            "lot_rule": "A股最小交易单位100股；100股持仓触发减仓时默认清仓",
        }

    def _get_strategy_rule(self, strategy_mode: str) -> dict[str, Any]:
        mode = strategy_mode if strategy_mode in {"strict", "normal", "loose"} else "strict"
        base = {
            "mode": mode,
            "max_price": settings.auto_trade_max_price,
            "min_confidence": settings.auto_trade_min_confidence,
            "min_trend_score": settings.auto_trade_min_trend_score,
            "max_volatility": settings.auto_trade_max_volatility,
            "min_price_position": settings.auto_trade_min_price_position,
            "max_price_position": settings.auto_trade_max_price_position,
            "min_volume_ratio": settings.auto_trade_min_volume_ratio,
            "take_profit_pct": settings.auto_trade_take_profit_pct,
            "stop_loss_pct": settings.auto_trade_stop_loss_pct,
            "take_profit_tiers": [
                {"threshold_pct": 0.10, "action": "sell_ratio", "ratio": 1 / 3, "description": "上涨超过10%，减三分之一仓位"},
                {"threshold_pct": 0.20, "action": "sell_ratio", "ratio": 0.5, "description": "上涨超过20%，减半仓"},
                {"threshold_pct": 0.50, "action": "sell_all", "ratio": 1.0, "description": "上涨超过50%，清仓"},
            ],
            "stop_loss_tiers": [
                {"threshold_pct": -0.05, "action": "sell_ratio", "ratio": 0.5, "description": "下跌超过5%，减半仓"},
                {"threshold_pct": -0.10, "action": "sell_all", "ratio": 1.0, "description": "下跌超过10%，清仓"},
            ],
            "allowed_directions": ["up"],
            "allowed_signals": ["bullish"],
        }
        if mode == "normal":
            base.update(
                {
                    "min_confidence": 0.52,
                    "min_trend_score": 56.0,
                    "max_volatility": 0.075,
                    "min_price_position": 0.25,
                    "max_price_position": 0.93,
                    "min_volume_ratio": 0.55,
                    "take_profit_pct": 0.06,
                    "stop_loss_pct": 0.05,
                    "allowed_signals": ["bullish", "neutral"],
                }
            )
        elif mode == "loose":
            base.update(
                {
                    "min_confidence": 0.45,
                    "min_trend_score": 50.0,
                    "max_volatility": 0.12,
                    "min_price_position": 0.10,
                    "max_price_position": 0.98,
                    "min_volume_ratio": 0.20,
                    "take_profit_pct": 0.04,
                    "stop_loss_pct": 0.06,
                    "allowed_directions": ["up", "flat"],
                    "allowed_signals": ["bullish", "neutral"],
                }
            )
        return base

    def _broker_buy(self, symbol: str, quantity: int, price: float, decision: dict | None = None) -> dict:
        return self.broker.buy(symbol, quantity, price, decision=decision)

    def _broker_sell(self, symbol: str, quantity: int | None, price: float, decision: dict | None = None) -> dict:
        return self.broker.sell(symbol, quantity, price, decision=decision)

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper().split(".")[0]

    def _is_stock_symbol(self, symbol: str) -> bool:
        normalized = self._normalize_symbol(symbol)
        return len(normalized) == 6 and normalized.isdigit() and not normalized.startswith("88")
