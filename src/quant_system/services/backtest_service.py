from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
import json
from statistics import pstdev
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from quant_system.api.pagination import PageParams, PageResult, paginate
from quant_system.brokers.execution import build_trade_execution
from quant_system.core.config import settings
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import BacktestEquityModel, BacktestRunModel, BacktestTradeModel
from quant_system.services.backtest_cache_service import BacktestCacheService
from quant_system.services.kline_service import KlineService
from quant_system.services.stock_pool_service import StockPoolService


@dataclass
class BacktestPosition:
    symbol: str
    quantity: int
    avg_price: float
    entry_price: float
    entry_time: str
    cost_amount: float


class BacktestService:
    """历史 K 线回测服务。

    回测口径复用模拟交易的滑点、佣金、印花税、过户费规则，避免纸面回测和模拟交易统计口径不一致。
    """

    def __init__(self) -> None:
        init_sqlalchemy_tables()
        self.kline_service = KlineService()
        self.stock_pool_service = StockPoolService()
        self.cache = BacktestCacheService()

    def run_symbol_backtest(
        self,
        symbol: str,
        period: str = "daily",
        strategy_mode: str = "strict",
        initial_cash: float | None = None,
        quantity: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        cash = float(initial_cash or settings.default_cash)
        initial = cash
        trade_quantity = int(quantity or settings.auto_trade_quantity)

        cache_key = self.cache.build_backtest_key(
            symbol=normalized_symbol, period=period, strategy_mode=strategy_mode,
            initial_cash=initial, quantity=trade_quantity, start_date=start_date, end_date=end_date,
        )
        cached = self.cache.get_backtest(cache_key)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached

        rule = self._get_strategy_rule(strategy_mode)
        rows = self._filter_rows(self.kline_service.list_klines(normalized_symbol, period=period, limit=2000), start_date, end_date)
        result = self._run_single_symbol(
            normalized_symbol,
            rows,
            period=period,
            rule=rule,
            initial_cash=initial,
            cash=cash,
            quantity=trade_quantity,
        )
        self._persist_symbol_run(
            result,
            params={
                "symbol": normalized_symbol,
                "period": period,
                "strategy_mode": strategy_mode,
                "initial_cash": initial,
                "quantity": trade_quantity,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        if result.get("status") == "ok":
            self.cache.set_backtest(cache_key, result)
        result["_cache_hit"] = False
        return result

    def run_pool_backtest(
        self,
        pool_code: str,
        period: str = "daily",
        strategy_mode: str = "strict",
        initial_cash: float | None = None,
        quantity: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit_symbols: int | None = None,
    ) -> dict:
        cache_key = self.cache.build_backtest_key(
            pool_code=pool_code, period=period, strategy_mode=strategy_mode,
            initial_cash=initial_cash, quantity=quantity, start_date=start_date,
            end_date=end_date, limit_symbols=limit_symbols,
        )
        cached = self.cache.get_backtest(cache_key)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached

        members = self.stock_pool_service.list_members(pool_code)
        symbols = []
        seen = set()
        for member in members:
            symbol = self._normalize_symbol(member["symbol"])
            if not self._is_stock_symbol(symbol) or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
            if limit_symbols is not None and len(symbols) >= limit_symbols:
                break

        per_symbol = [
            self.run_symbol_backtest(
                symbol=symbol,
                period=period,
                strategy_mode=strategy_mode,
                initial_cash=initial_cash,
                quantity=quantity,
                start_date=start_date,
                end_date=end_date,
            )
            for symbol in symbols
        ]
        tradable = [item for item in per_symbol if item.get("status") == "ok"]
        total_pnl = round(sum(item["summary"]["total_pnl"] for item in tradable), 2)
        total_trades = sum(item["summary"]["trade_count"] for item in tradable)
        winning_symbols = sum(1 for item in tradable if item["summary"]["total_pnl"] > 0)
        result = {
            "status": "ok" if tradable else "insufficient_data",
            "pool_code": pool_code,
            "period": period,
            "strategy_mode": self._get_strategy_rule(strategy_mode)["mode"],
            "symbol_count": len(symbols),
            "tradable_symbol_count": len(tradable),
            "summary": {
                "total_pnl": total_pnl,
                "total_trades": total_trades,
                "winning_symbols": winning_symbols,
                "losing_symbols": len(tradable) - winning_symbols,
                "avg_pnl_per_symbol": round(total_pnl / len(tradable), 2) if tradable else 0,
            },
            "execution_rules": self._execution_rules(),
            "items": per_symbol,
        }
        self._persist_pool_run(
            result,
            params={
                "pool_code": pool_code,
                "period": period,
                "strategy_mode": strategy_mode,
                "initial_cash": initial_cash,
                "quantity": quantity,
                "start_date": start_date,
                "end_date": end_date,
                "limit_symbols": limit_symbols,
            },
        )
        return result

    def _run_single_symbol(
        self,
        symbol: str,
        rows: list[dict],
        period: str,
        rule: dict[str, Any],
        initial_cash: float,
        cash: float,
        quantity: int,
    ) -> dict:
        if len(rows) < 80:
            return {
                "status": "insufficient_data",
                "symbol": symbol,
                "period": period,
                "message": "本地 K 线不足，至少需要 80 条。请先同步 K 线。",
                "rows_count": len(rows),
            }

        features = self._compute_features(symbol, period, rows)
        position: BacktestPosition | None = None
        trades: list[dict] = []
        equity_curve: list[dict] = []
        realized_pnl = 0.0

        for item in features:
            close = item.get("close") or 0
            if close <= 0:
                continue
            trade_time = item["trade_time"]
            if position is None:
                decision = self._evaluate_open(item, rule)
                if decision["allowed"]:
                    execution = build_trade_execution("buy", quantity, close)
                    if execution.amount <= cash:
                        cash = round(cash - execution.amount, 2)
                        position = BacktestPosition(
                            symbol=symbol,
                            quantity=quantity,
                            avg_price=execution.amount / quantity,
                            entry_price=execution.price,
                            entry_time=trade_time,
                            cost_amount=execution.amount,
                        )
                        trades.append(self._build_trade("buy", symbol, trade_time, execution, cash, reason=decision["reason"], feature=item, decision=decision))
                    else:
                        trades.append({
                            "accepted": False,
                            "side": "buy",
                            "symbol": symbol,
                            "trade_time": trade_time,
                            "reason": "回测现金不足",
                            "requested_price": close,
                            "cash": cash,
                        })
            else:
                decision = self._evaluate_close(item, position, rule)
                if decision["allowed"]:
                    execution = build_trade_execution("sell", position.quantity, close)
                    pnl = round(execution.amount - position.cost_amount, 2)
                    realized_pnl = round(realized_pnl + pnl, 2)
                    cash = round(cash + execution.amount, 2)
                    trade = self._build_trade("sell", symbol, trade_time, execution, cash, reason=decision["reason"], feature=item, decision=decision)
                    holding_bars = self._holding_bars(features, position.entry_time, trade_time)
                    trade.update({
                        "entry_time": position.entry_time,
                        "entry_price": position.entry_price,
                        "exit_time": trade_time,
                        "exit_price": execution.price,
                        "holding_bars": holding_bars,
                        "holding_days": holding_bars,
                        "realized_pnl": pnl,
                        "realized_pnl_pct": round(pnl / position.cost_amount * 100, 2) if position.cost_amount else 0,
                    })
                    trades.append(trade)
                    position = None

            market_value = round(position.quantity * close, 2) if position else 0.0
            unrealized = round(market_value - position.cost_amount, 2) if position else 0.0
            equity_curve.append({
                "trade_time": trade_time,
                "cash": cash,
                "market_value": market_value,
                "equity": round(cash + market_value, 2),
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized,
            })

        if position is not None and features:
            last = features[-1]
            execution = build_trade_execution("sell", position.quantity, last["close"])
            pnl = round(execution.amount - position.cost_amount, 2)
            realized_pnl = round(realized_pnl + pnl, 2)
            cash = round(cash + execution.amount, 2)
            forced_decision = {
                "allowed": True,
                "reason": "回测结束强制平仓",
                "triggered_rules": [{"code": "forced_close_at_end", "reason": "回测结束强制平仓"}],
                "failed_rules": [],
                "metrics": {
                    "direction": self._direction(last),
                    "signal": last.get("signal"),
                    "trend_score": last.get("trend_score"),
                    "unrealized_pnl_pct": round((last["close"] - position.avg_price) / position.avg_price * 100, 2) if position.avg_price else 0,
                },
            }
            trade = self._build_trade("sell", symbol, last["trade_time"], execution, cash, reason="回测结束强制平仓", feature=last, decision=forced_decision)
            holding_bars = self._holding_bars(features, position.entry_time, last["trade_time"])
            trade.update({
                "entry_time": position.entry_time,
                "entry_price": position.entry_price,
                "exit_time": last["trade_time"],
                "exit_price": execution.price,
                "holding_bars": holding_bars,
                "holding_days": holding_bars,
                "realized_pnl": pnl,
                "realized_pnl_pct": round(pnl / position.cost_amount * 100, 2) if position.cost_amount else 0,
                "forced": True,
            })
            trades.append(trade)
            position = None
            equity_curve.append({
                "trade_time": last["trade_time"],
                "cash": cash,
                "market_value": 0.0,
                "equity": cash,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": 0.0,
            })

        filled_trades = [trade for trade in trades if trade.get("accepted")]
        sell_trades = [trade for trade in filled_trades if trade["side"] == "sell"]
        wins = [trade for trade in sell_trades if trade.get("realized_pnl", 0) > 0]
        losses = [trade for trade in sell_trades if trade.get("realized_pnl", 0) < 0]
        total_fees = round(sum(trade.get("total_fee", 0) or 0 for trade in filled_trades), 2)
        final_equity = equity_curve[-1]["equity"] if equity_curve else initial_cash
        total_pnl = round(final_equity - initial_cash, 2)
        max_drawdown = self._max_drawdown(equity_curve)
        win_rate = round(len(wins) / len(sell_trades) * 100, 2) if sell_trades else 0
        advanced = self._compute_advanced_metrics(
            equity_curve, sell_trades, initial_cash, final_equity, len(features), win_rate, max_drawdown,
        )
        return {
            "status": "ok",
            "symbol": symbol,
            "period": period,
            "strategy_mode": rule["mode"],
            "rows_count": len(rows),
            "tested_bars": len(features),
            "summary": {
                "initial_cash": round(initial_cash, 2),
                "final_equity": round(final_equity, 2),
                "total_pnl": total_pnl,
                "total_pnl_pct": round(total_pnl / initial_cash * 100, 2) if initial_cash else 0,
                "realized_pnl": round(realized_pnl, 2),
                "trade_count": len(filled_trades),
                "round_trip_count": len(sell_trades),
                "win_count": len(wins),
                "loss_count": len(losses),
                "win_rate": win_rate,
                "total_fees": total_fees,
                "max_drawdown": max_drawdown,
                **advanced,
            },
            "rule": rule,
            "execution_rules": self._execution_rules(),
            "trades": trades,
            "equity_curve": equity_curve,
        }

    def _persist_symbol_run(self, result: dict, params: dict) -> str | None:
        run_id = self._new_run_id("bt-symbol")
        result["run_id"] = run_id
        return self._persist_run(
            run_id=run_id,
            scope="symbol",
            result=result,
            params=params,
            symbol=result.get("symbol"),
            pool_code=None,
            items=[result],
        )

    def _persist_pool_run(self, result: dict, params: dict) -> str | None:
        run_id = self._new_run_id("bt-pool")
        result["run_id"] = run_id
        return self._persist_run(
            run_id=run_id,
            scope="pool",
            result=result,
            params=params,
            symbol=None,
            pool_code=result.get("pool_code"),
            items=[item for item in result.get("items", []) if item.get("status") == "ok"],
        )

    def _persist_run(self, run_id: str, scope: str, result: dict, params: dict, symbol: str | None, pool_code: str | None, items: list[dict]) -> str | None:
        summary = result.get("summary") or {}
        now = self._now()
        try:
            with SessionLocal() as session:
                run = BacktestRunModel(
                    run_id=run_id,
                    scope=scope,
                    symbol=symbol,
                    pool_code=pool_code,
                    period=result.get("period") or params.get("period") or "daily",
                    strategy_mode=result.get("strategy_mode") or params.get("strategy_mode") or "strict",
                    status=result.get("status") or "unknown",
                    start_date=params.get("start_date"),
                    end_date=params.get("end_date"),
                    initial_cash=self._to_float(summary.get("initial_cash") or params.get("initial_cash")),
                    quantity=params.get("quantity"),
                    rows_count=result.get("rows_count"),
                    tested_bars=result.get("tested_bars"),
                    trade_count=summary.get("trade_count") or summary.get("total_trades"),
                    round_trip_count=summary.get("round_trip_count"),
                    total_pnl=self._to_float(summary.get("total_pnl")),
                    total_pnl_pct=self._to_float(summary.get("total_pnl_pct")),
                    final_equity=self._to_float(summary.get("final_equity")),
                    max_drawdown=self._to_float(summary.get("max_drawdown")),
                    win_rate=self._to_float(summary.get("win_rate")),
                    summary_json=self._to_json(summary),
                    params_json=self._to_json(params),
                    rule_json=self._to_json(result.get("rule")),
                    execution_rules_json=self._to_json(result.get("execution_rules")),
                    created_at=now,
                    updated_at=now,
                    created_by="system",
                    updated_by="system",
                )
                session.add(run)
                for item in items:
                    item_symbol = item.get("symbol") or symbol or ""
                    item_period = item.get("period") or result.get("period") or params.get("period") or "daily"
                    for trade in item.get("trades", []):
                        session.add(BacktestTradeModel(
                            run_id=run_id,
                            symbol=item_symbol,
                            period=item_period,
                            trade_time=str(trade.get("trade_time") or ""),
                            side=str(trade.get("side") or ""),
                            accepted=bool(trade.get("accepted")),
                            quantity=trade.get("quantity"),
                            price=self._to_float(trade.get("price")),
                            requested_price=self._to_float(trade.get("requested_price")),
                            amount=self._to_float(trade.get("amount")),
                            total_fee=self._to_float(trade.get("total_fee")),
                            realized_pnl=self._to_float(trade.get("realized_pnl")),
                            reason=trade.get("reason"),
                            payload_json=self._to_json(trade),
                            created_at=now,
                            updated_at=now,
                            created_by="system",
                            updated_by="system",
                        ))
                    for point in item.get("equity_curve", []):
                        session.add(BacktestEquityModel(
                            run_id=run_id,
                            symbol=item_symbol,
                            period=item_period,
                            trade_time=str(point.get("trade_time") or ""),
                            cash=self._to_float(point.get("cash")),
                            market_value=self._to_float(point.get("market_value")),
                            equity=self._to_float(point.get("equity")),
                            realized_pnl=self._to_float(point.get("realized_pnl")),
                            unrealized_pnl=self._to_float(point.get("unrealized_pnl")),
                            created_at=now,
                            updated_at=now,
                            created_by="system",
                            updated_by="system",
                        ))
                session.commit()
            return run_id
        except Exception as exc:
            result["persist_warning"] = f"回测结果保存失败：{exc}"
            return None

    def list_runs_page(self, page_params: PageParams, scope: str | None = None, symbol: str | None = None, pool_code: str | None = None, strategy_mode: str | None = None, status: str | None = None) -> PageResult:
        stmt = select(BacktestRunModel).order_by(BacktestRunModel.created_at.desc(), BacktestRunModel.id.desc())
        if scope:
            stmt = stmt.where(BacktestRunModel.scope == scope)
        if symbol:
            stmt = stmt.where(BacktestRunModel.symbol == self._normalize_symbol(symbol))
        if pool_code:
            stmt = stmt.where(BacktestRunModel.pool_code == pool_code)
        if strategy_mode:
            stmt = stmt.where(BacktestRunModel.strategy_mode == strategy_mode)
        if status:
            stmt = stmt.where(BacktestRunModel.status == status)
        with SessionLocal() as session:
            return paginate(session, stmt, None, page_params, to_dict_fn=self._run_to_dict)

    def run_grid_optimization(
        self,
        symbol: str | None = None,
        pool_code: str | None = None,
        period: str = "daily",
        strategy_mode: str = "loose",
        initial_cash: float | None = None,
        quantity: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit_symbols: int | None = None,
        take_profit_pct: list[float] | None = None,
        stop_loss_pct: list[float] | None = None,
        min_trend_score: list[float] | None = None,
        min_confidence: list[float] | None = None,
        max_combinations: int = 24,
    ) -> dict:
        if bool(symbol) == bool(pool_code):
            raise ValueError("symbol 和 pool_code 必须且只能传一个。")

        cache_key = self.cache.build_grid_optimize_key(
            symbol=symbol, pool_code=pool_code, period=period, strategy_mode=strategy_mode,
            initial_cash=initial_cash, quantity=quantity, start_date=start_date,
            end_date=end_date, limit_symbols=limit_symbols,
            take_profit_pct=take_profit_pct, stop_loss_pct=stop_loss_pct,
            min_trend_score=min_trend_score, min_confidence=min_confidence,
        )
        cached = self.cache.get_backtest(cache_key)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached

        normalized_symbol = self._normalize_symbol(symbol) if symbol else None
        base_rule = self._get_strategy_rule(strategy_mode)
        initial = float(initial_cash or settings.default_cash)
        trade_quantity = int(quantity or settings.auto_trade_quantity)
        combinations = self._grid_rule_combinations(
            base_rule,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            min_trend_score=min_trend_score,
            min_confidence=min_confidence,
            max_combinations=max_combinations,
        )
        if normalized_symbol:
            rows = self._filter_rows(self.kline_service.list_klines(normalized_symbol, period=period, limit=2000), start_date, end_date)
            items = [
                self._grid_result_item(
                    index=index,
                    rule=rule,
                    result=self._run_single_symbol(
                        normalized_symbol,
                        rows,
                        period=period,
                        rule=rule,
                        initial_cash=initial,
                        cash=initial,
                        quantity=trade_quantity,
                    ),
                )
                for index, rule in enumerate(combinations, start=1)
            ]
        else:
            members = self.stock_pool_service.list_members(pool_code or "")
            symbols = []
            seen = set()
            for member in members:
                member_symbol = self._normalize_symbol(member["symbol"])
                if not self._is_stock_symbol(member_symbol) or member_symbol in seen:
                    continue
                seen.add(member_symbol)
                symbols.append(member_symbol)
                if limit_symbols is not None and len(symbols) >= limit_symbols:
                    break
            items = [self._grid_pool_result_item(index, rule, symbols, period, initial, trade_quantity, start_date, end_date) for index, rule in enumerate(combinations, start=1)]
        ranked = sorted(items, key=lambda item: item["score"], reverse=True)
        grid_result = {
            "scope": "symbol" if normalized_symbol else "pool",
            "symbol": normalized_symbol,
            "pool_code": pool_code,
            "period": period,
            "strategy_mode": base_rule["mode"],
            "grid": {
                "take_profit_pct": take_profit_pct or [base_rule["take_profit_pct"]],
                "stop_loss_pct": stop_loss_pct or [base_rule["stop_loss_pct"]],
                "min_trend_score": min_trend_score or [base_rule["min_trend_score"]],
                "min_confidence": min_confidence or [base_rule["min_confidence"]],
                "max_combinations": max_combinations,
            },
            "best": ranked[0] if ranked else None,
            "items": ranked,
            "summary": {
                "tested_combinations": len(ranked),
                "ok_combinations": sum(1 for item in ranked if item["status"] == "ok"),
                "best_rank": ranked[0]["rank"] if ranked else None,
                "best_score": ranked[0]["score"] if ranked else 0,
                "best_params": ranked[0]["params"] if ranked else None,
            },
        }
        self.cache.set_backtest(cache_key, grid_result)
        grid_result["_cache_hit"] = False
        return grid_result

    def _grid_rule_combinations(
        self,
        base_rule: dict[str, Any],
        take_profit_pct: list[float] | None,
        stop_loss_pct: list[float] | None,
        min_trend_score: list[float] | None,
        min_confidence: list[float] | None,
        max_combinations: int,
    ) -> list[dict[str, Any]]:
        values = product(
            take_profit_pct or [base_rule["take_profit_pct"]],
            stop_loss_pct or [base_rule["stop_loss_pct"]],
            min_trend_score or [base_rule["min_trend_score"]],
            min_confidence or [base_rule["min_confidence"]],
        )
        rules = []
        for take_profit, stop_loss, trend_score, confidence in values:
            rule = dict(base_rule)
            rule.update({
                "take_profit_pct": float(take_profit),
                "stop_loss_pct": float(stop_loss),
                "min_trend_score": float(trend_score),
                "min_confidence": float(confidence),
            })
            rules.append(rule)
            if len(rules) >= max(1, max_combinations):
                break
        return rules

    def _grid_result_item(self, index: int, rule: dict[str, Any], result: dict) -> dict:
        item = self._strategy_comparison_item(rule["mode"], result)
        item["rank"] = index
        item["params"] = self._grid_params(rule)
        item["rule"] = rule
        return item

    def _grid_pool_result_item(self, index: int, rule: dict[str, Any], symbols: list[str], period: str, initial_cash: float, quantity: int, start_date: str | None, end_date: str | None) -> dict:
        results = []
        for symbol in symbols:
            rows = self._filter_rows(self.kline_service.list_klines(symbol, period=period, limit=2000), start_date, end_date)
            results.append(self._run_single_symbol(symbol, rows, period=period, rule=rule, initial_cash=initial_cash, cash=initial_cash, quantity=quantity))
        ok_results = [item for item in results if item.get("status") == "ok"]
        summary = {
            "total_pnl": round(sum((item.get("summary") or {}).get("total_pnl", 0) for item in ok_results), 2),
            "total_pnl_pct": round(sum((item.get("summary") or {}).get("total_pnl_pct", 0) for item in ok_results) / len(ok_results), 2) if ok_results else 0,
            "final_equity": round(sum((item.get("summary") or {}).get("final_equity", 0) for item in ok_results), 2),
            "max_drawdown": round(sum((item.get("summary") or {}).get("max_drawdown", 0) for item in ok_results) / len(ok_results), 2) if ok_results else 0,
            "win_rate": round(sum((item.get("summary") or {}).get("win_rate", 0) for item in ok_results) / len(ok_results), 2) if ok_results else 0,
            "trade_count": sum((item.get("summary") or {}).get("trade_count", 0) for item in ok_results),
            "round_trip_count": sum((item.get("summary") or {}).get("round_trip_count", 0) for item in ok_results),
        }
        return self._grid_result_item(index, rule, {"status": "ok" if ok_results else "insufficient_data", "pool_code": None, "summary": summary, "rows_count": len(symbols), "tested_bars": sum(item.get("tested_bars", 0) for item in ok_results)})

    def _grid_params(self, rule: dict[str, Any]) -> dict:
        return {
            "take_profit_pct": rule["take_profit_pct"],
            "stop_loss_pct": rule["stop_loss_pct"],
            "min_trend_score": rule["min_trend_score"],
            "min_confidence": rule["min_confidence"],
        }

    def run_strategy_comparison(
        self,
        symbol: str | None = None,
        pool_code: str | None = None,
        period: str = "daily",
        strategy_modes: list[str] | None = None,
        initial_cash: float | None = None,
        quantity: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit_symbols: int | None = None,
    ) -> dict:
        if bool(symbol) == bool(pool_code):
            raise ValueError("symbol 和 pool_code 必须且只能传一个。")

        cache_key = self.cache.build_strategy_compare_key(
            symbol=symbol, pool_code=pool_code, period=period, strategy_modes=strategy_modes,
            initial_cash=initial_cash, quantity=quantity, start_date=start_date,
            end_date=end_date, limit_symbols=limit_symbols,
        )
        cached = self.cache.get_backtest(cache_key)
        if cached is not None:
            cached["_cache_hit"] = True
            return cached

        modes = self._normalize_strategy_modes(strategy_modes)
        results = []
        for mode in modes:
            if symbol:
                result = self.run_symbol_backtest(
                    symbol=symbol,
                    period=period,
                    strategy_mode=mode,
                    initial_cash=initial_cash,
                    quantity=quantity,
                    start_date=start_date,
                    end_date=end_date,
                )
            else:
                result = self.run_pool_backtest(
                    pool_code=pool_code or "",
                    period=period,
                    strategy_mode=mode,
                    initial_cash=initial_cash,
                    quantity=quantity,
                    start_date=start_date,
                    end_date=end_date,
                    limit_symbols=limit_symbols,
                )
            results.append(self._strategy_comparison_item(mode, result))
        ranked = sorted(results, key=lambda item: item["score"], reverse=True)
        comparison_result = {
            "scope": "symbol" if symbol else "pool",
            "symbol": self._normalize_symbol(symbol) if symbol else None,
            "pool_code": pool_code,
            "period": period,
            "strategy_modes": modes,
            "best": ranked[0] if ranked else None,
            "items": ranked,
            "summary": {
                "tested_modes": len(ranked),
                "ok_modes": sum(1 for item in ranked if item["status"] == "ok"),
                "best_mode": ranked[0]["strategy_mode"] if ranked else None,
                "best_score": ranked[0]["score"] if ranked else 0,
            },
        }
        self.cache.set_backtest(cache_key, comparison_result)
        comparison_result["_cache_hit"] = False
        return comparison_result

    def _strategy_comparison_item(self, mode: str, result: dict) -> dict:
        summary = result.get("summary") or {}
        item = {
            "strategy_mode": mode,
            "run_id": result.get("run_id"),
            "status": result.get("status"),
            "symbol": result.get("symbol"),
            "pool_code": result.get("pool_code"),
            "rows_count": result.get("rows_count"),
            "tested_bars": result.get("tested_bars"),
            "message": result.get("message"),
            "total_pnl": summary.get("total_pnl", 0),
            "total_pnl_pct": summary.get("total_pnl_pct", 0),
            "final_equity": summary.get("final_equity", 0),
            "max_drawdown": summary.get("max_drawdown", 0),
            "win_rate": summary.get("win_rate", 0),
            "trade_count": summary.get("trade_count") or summary.get("total_trades", 0),
            "round_trip_count": summary.get("round_trip_count", 0),
            "total_fees": summary.get("total_fees", 0),
        }
        item["score"] = self._comparison_score(item)
        item["risk_return_ratio"] = self._risk_return_ratio(item)
        return item

    def _normalize_strategy_modes(self, strategy_modes: list[str] | None) -> list[str]:
        allowed = {"strict", "normal", "loose"}
        modes = []
        for mode in strategy_modes or ["strict", "normal", "loose"]:
            normalized = str(mode or "").strip().lower()
            if normalized in allowed and normalized not in modes:
                modes.append(normalized)
        return modes or ["strict", "normal", "loose"]

    def compare_runs(
        self,
        scope: str | None = None,
        symbol: str | None = None,
        pool_code: str | None = None,
        strategy_mode: str | None = None,
        status: str | None = "ok",
        sort_by: str = "score",
        sort_order: str = "desc",
        limit: int = 20,
    ) -> dict:
        stmt = select(BacktestRunModel)
        if scope:
            stmt = stmt.where(BacktestRunModel.scope == scope)
        if symbol:
            stmt = stmt.where(BacktestRunModel.symbol == self._normalize_symbol(symbol))
        if pool_code:
            stmt = stmt.where(BacktestRunModel.pool_code == pool_code)
        if strategy_mode:
            stmt = stmt.where(BacktestRunModel.strategy_mode == strategy_mode)
        if status:
            stmt = stmt.where(BacktestRunModel.status == status)
        with SessionLocal() as session:
            runs = session.scalars(stmt).all()
        items = [self._comparison_item(run) for run in runs]
        reverse = sort_order != "asc"
        items = sorted(items, key=lambda item: self._comparison_sort_value(item, sort_by), reverse=reverse)
        if limit > 0:
            items = items[:limit]
        return {
            "count": len(items),
            "sort_by": sort_by,
            "sort_order": "desc" if reverse else "asc",
            "filters": {
                "scope": scope,
                "symbol": self._normalize_symbol(symbol) if symbol else None,
                "pool_code": pool_code,
                "strategy_mode": strategy_mode,
                "status": status,
            },
            "best": items[0] if items else None,
            "items": items,
            "summary": self._comparison_summary(items),
        }

    def _comparison_item(self, run: BacktestRunModel) -> dict:
        item = self._run_to_dict(run)
        score = self._comparison_score(item)
        item["score"] = score
        item["risk_return_ratio"] = self._risk_return_ratio(item)
        return item

    def _comparison_score(self, item: dict) -> float:
        total_pnl_pct = float(item.get("total_pnl_pct") or 0)
        max_drawdown = float(item.get("max_drawdown") or 0)
        win_rate = float(item.get("win_rate") or 0)
        trade_count = float(item.get("trade_count") or 0)
        trade_bonus = min(trade_count, 20) * 0.2
        score = total_pnl_pct * 1.6 + win_rate * 0.25 - max_drawdown * 1.2 + trade_bonus
        return round(score, 4)

    def _risk_return_ratio(self, item: dict) -> float:
        total_pnl_pct = float(item.get("total_pnl_pct") or 0)
        max_drawdown = float(item.get("max_drawdown") or 0)
        if max_drawdown <= 0:
            return round(total_pnl_pct, 4)
        return round(total_pnl_pct / max_drawdown, 4)

    def _comparison_sort_value(self, item: dict, sort_by: str) -> float:
        allowed = {
            "score",
            "total_pnl",
            "total_pnl_pct",
            "final_equity",
            "max_drawdown",
            "win_rate",
            "trade_count",
            "round_trip_count",
            "risk_return_ratio",
        }
        key = sort_by if sort_by in allowed else "score"
        return float(item.get(key) or 0)

    def _comparison_summary(self, items: list[dict]) -> dict:
        if not items:
            return {
                "avg_total_pnl_pct": 0,
                "avg_max_drawdown": 0,
                "avg_win_rate": 0,
                "best_run_id": None,
                "best_score": 0,
            }
        return {
            "avg_total_pnl_pct": round(sum(float(item.get("total_pnl_pct") or 0) for item in items) / len(items), 4),
            "avg_max_drawdown": round(sum(float(item.get("max_drawdown") or 0) for item in items) / len(items), 4),
            "avg_win_rate": round(sum(float(item.get("win_rate") or 0) for item in items) / len(items), 4),
            "best_run_id": items[0].get("run_id"),
            "best_score": items[0].get("score"),
        }

    def get_run_report(self, run_id: str, include_trades: bool = True, top_trades: int = 10) -> dict:
        detail = self.get_run_detail(
            run_id,
            include_trades=include_trades,
            include_equity=False,
            trades_page_params=PageParams(page=1, page_size=max(1, min(top_trades, 100))),
        )
        return self._build_run_report(detail)

    def export_run_report(self, run_id: str, fmt: str = "markdown", include_trades: bool = True, top_trades: int = 10) -> tuple[str, str]:
        report = self.get_run_report(run_id, include_trades=include_trades, top_trades=top_trades)
        if fmt == "html":
            return self._render_report_html(report), "text/html; charset=utf-8"
        return self._render_report_markdown(report), "text/markdown; charset=utf-8"

    def _render_report_markdown(self, report: dict) -> str:
        lines: list[str] = []
        overview = report.get("overview") or {}
        diagnosis = report.get("diagnosis") or {}
        trade_analysis = report.get("trade_analysis") or {}

        lines.append(f"# {report.get('title', '回测报告')}")
        lines.append("")
        lines.append(f"- **回测 ID**: `{report.get('run_id', '')}`")
        lines.append(f"- **标的**: {report.get('symbol') or report.get('pool_code') or '-'}")
        lines.append(f"- **周期**: {report.get('period', '-')}")
        lines.append(f"- **策略模式**: {report.get('strategy_mode', '-')}")
        lines.append(f"- **生成时间**: {report.get('created_at', '-')}")
        lines.append("")

        lines.append("## 诊断结论")
        lines.append("")
        lines.append(f"**{diagnosis.get('verdict', '-')}**")
        lines.append("")
        lines.append(f"- 综合评分: **{diagnosis.get('score', 0)}**")
        lines.append(f"- 风险收益比: **{diagnosis.get('risk_return_ratio', 0)}**")
        lines.append(f"- {diagnosis.get('sample_size_note', '')}")
        lines.append("")

        lines.append("## 核心指标")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 初始资金 | {overview.get('initial_cash', '-')} |")
        lines.append(f"| 最终权益 | {overview.get('final_equity', '-')} |")
        lines.append(f"| 总盈亏 | {overview.get('total_pnl', '-')} |")
        lines.append(f"| 总收益率 | {overview.get('total_pnl_pct', '-')}% |")
        lines.append(f"| 年化收益率 | {overview.get('annualized_return_pct', '-')}% |")
        lines.append(f"| 最大回撤 | {overview.get('max_drawdown', '-')}% |")
        lines.append(f"| 胜率 | {overview.get('win_rate', '-')}% |")
        lines.append(f"| 成交次数 | {overview.get('trade_count', '-')} |")
        lines.append(f"| 完整交易轮次 | {overview.get('round_trip_count', '-')} |")
        if overview.get("total_fees") is not None:
            lines.append(f"| 总手续费 | {overview['total_fees']} |")
        lines.append("")

        lines.append("## 风险调整指标")
        lines.append("")
        lines.append("| 指标 | 数值 | 说明 |")
        lines.append("|------|------|------|")
        lines.append(f"| Sharpe Ratio | {overview.get('sharpe_ratio', '-')} | 风险调整后收益，>1 为佳 |")
        lines.append(f"| Sortino Ratio | {overview.get('sortino_ratio', '-')} | 下行风险调整收益，>1 为佳 |")
        lines.append(f"| Calmar Ratio | {overview.get('calmar_ratio', '-')} | 年化收益/最大回撤，>1 为佳 |")
        lines.append(f"| 最大回撤持续期 | {overview.get('max_drawdown_duration', '-')} 根K线 | 回撤恢复速度 |")
        lines.append("")

        lines.append("## 交易质量指标")
        lines.append("")
        lines.append("| 指标 | 数值 | 说明 |")
        lines.append("|------|------|------|")
        lines.append(f"| 盈亏比 | {overview.get('payoff_ratio', '-')} | 平均盈利/平均亏损 |")
        lines.append(f"| 盈利因子 | {overview.get('profit_factor', '-')} | 总盈利/总亏损，>1 盈利 |")
        lines.append(f"| 期望收益 | {overview.get('expectancy', '-')} | 每笔交易期望盈亏 |")
        lines.append(f"| 平均盈利 | {overview.get('avg_win', '-')} | 盈利交易平均收益 |")
        lines.append(f"| 平均亏损 | {overview.get('avg_loss', '-')} | 亏损交易平均亏损 |")
        lines.append(f"| 最大连续盈利 | {overview.get('max_consecutive_wins', '-')} 次 | |")
        lines.append(f"| 最大连续亏损 | {overview.get('max_consecutive_losses', '-')} 次 | |")
        lines.append(f"| 恢复因子 | {overview.get('recovery_factor', '-')} | 总收益/最大回撤金额 |")
        lines.append("")

        exit_reasons = trade_analysis.get("exit_reasons") or []
        if exit_reasons:
            lines.append("## 平仓原因分布")
            lines.append("")
            for item in exit_reasons:
                lines.append(f"- **{item['reason']}**: {item['count']} 次")
            lines.append("")

        best = trade_analysis.get("best_trade")
        worst = trade_analysis.get("worst_trade")
        if best or worst:
            lines.append("## 最佳 / 最差交易")
            lines.append("")
            if best:
                lines.append(f"- **最佳**: {best.get('trade_time', '-')} {best.get('side', '')} @ {best.get('price', '-')}，"
                             f"盈亏 {best.get('realized_pnl', '-')}（{best.get('realized_pnl_pct', '-')}%），"
                             f"持仓 {best.get('holding_days', '-')} 天，原因：{best.get('reason', '-')}")
            if worst:
                lines.append(f"- **最差**: {worst.get('trade_time', '-')} {worst.get('side', '')} @ {worst.get('price', '-')}，"
                             f"盈亏 {worst.get('realized_pnl', '-')}（{worst.get('realized_pnl_pct', '-')}%），"
                             f"持仓 {worst.get('holding_days', '-')} 天，原因：{worst.get('reason', '-')}")
            lines.append("")

        top_trades = trade_analysis.get("top_trades") or []
        if top_trades:
            lines.append("## 成交明细")
            lines.append("")
            lines.append("| 序号 | 时间 | 方向 | 价格 | 数量 | 盈亏 | 收益率 | 持仓天数 | 原因 |")
            lines.append("|------|------|------|------|------|------|--------|----------|------|")
            for idx, trade in enumerate(top_trades, start=1):
                side_label = "买入" if trade.get("side") == "buy" else "卖出"
                lines.append(
                    f"| {idx} | {trade.get('trade_time', '-')} | {side_label} | {trade.get('price', '-')} | "
                    f"{trade.get('quantity', '-')} | {trade.get('realized_pnl', '-')} | "
                    f"{trade.get('realized_pnl_pct', '-')}% | {trade.get('holding_days', '-')} | "
                    f"{trade.get('reason', '-')} |"
                )
            lines.append("")

        risk_notes = report.get("risk_notes") or []
        if risk_notes:
            lines.append("## 风险提示")
            lines.append("")
            for note in risk_notes:
                lines.append(f"- {note}")
            lines.append("")

        next_actions = report.get("next_actions") or []
        if next_actions:
            lines.append("## 下一步建议")
            lines.append("")
            for action in next_actions:
                lines.append(f"- {action}")
            lines.append("")

        lines.append("---")
        lines.append(f"*由量化交易系统自动生成，回测 ID: {report.get('run_id', '')}*")
        return "\n".join(lines)

    def _render_report_html(self, report: dict) -> str:
        md = self._render_report_markdown(report)
        overview = report.get("overview") or {}
        diagnosis = report.get("diagnosis") or {}

        rows = ""
        for trade in (report.get("trade_analysis") or {}).get("top_trades") or []:
            side_label = "买入" if trade.get("side") == "buy" else "卖出"
            rows += (
                f"<tr>"
                f"<td>{trade.get('trade_time', '-')}</td>"
                f"<td>{side_label}</td>"
                f"<td>{trade.get('price', '-')}</td>"
                f"<td>{trade.get('quantity', '-')}</td>"
                f"<td>{trade.get('realized_pnl', '-')}</td>"
                f"<td>{trade.get('realized_pnl_pct', '-')}%</td>"
                f"<td>{trade.get('holding_days', '-')}</td>"
                f"<td>{trade.get('reason', '-')}</td>"
                f"</tr>\n"
            )

        risk_items = "".join(f"<li>{note}</li>\n" for note in (report.get("risk_notes") or []))
        action_items = "".join(f"<li>{action}</li>\n" for action in (report.get("next_actions") or []))
        reason_items = "".join(f"<li><strong>{r['reason']}</strong>: {r['count']} 次</li>\n" for r in (report.get("trade_analysis") or {}).get("exit_reasons") or [])

        best = (report.get("trade_analysis") or {}).get("best_trade") or {}
        worst = (report.get("trade_analysis") or {}).get("worst_trade") or {}
        best_line = (f"最佳: {best.get('trade_time', '-')} {best.get('side', '')} @ {best.get('price', '-')}，"
                     f"盈亏 {best.get('realized_pnl', '-')}（{best.get('realized_pnl_pct', '-')}%），"
                     f"原因：{best.get('reason', '-')}") if best else "-"
        worst_line = (f"最差: {worst.get('trade_time', '-')} {worst.get('side', '')} @ {worst.get('price', '-')}，"
                      f"盈亏 {worst.get('realized_pnl', '-')}（{worst.get('realized_pnl_pct', '-')}%），"
                      f"原因：{worst.get('reason', '-')}") if worst else "-"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{report.get('title', '回测报告')}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 960px; margin: 0 auto; padding: 24px; color: #1a1a2e; background: #f8f9fa; }}
  h1 {{ color: #16213e; border-bottom: 2px solid #0f3460; padding-bottom: 8px; }}
  h2 {{ color: #0f3460; margin-top: 28px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #dee2e6; padding: 8px 12px; text-align: left; font-size: 14px; }}
  th {{ background: #0f3460; color: white; }}
  tr:nth-child(even) {{ background: #f1f3f5; }}
  .verdict {{ font-size: 20px; font-weight: bold; color: #e94560; padding: 12px; background: #fff3f3; border-radius: 6px; display: inline-block; }}
  .metrics {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; margin: 16px 0; }}
  .metric-card {{ background: white; border: 1px solid #dee2e6; border-radius: 8px; padding: 16px; text-align: center; }}
  .metric-card .label {{ font-size: 13px; color: #6c757d; }}
  .metric-card .value {{ font-size: 22px; font-weight: bold; color: #16213e; margin-top: 4px; }}
  .footer {{ margin-top: 32px; padding-top: 12px; border-top: 1px solid #dee2e6; color: #6c757d; font-size: 13px; }}
</style>
</head>
<body>
<h1>{report.get('title', '回测报告')}</h1>
<p><strong>回测 ID:</strong> <code>{report.get('run_id', '')}</code> |
<strong>标的:</strong> {report.get('symbol') or report.get('pool_code') or '-'} |
<strong>周期:</strong> {report.get('period', '-')} |
<strong>策略模式:</strong> {report.get('strategy_mode', '-')} |
<strong>生成时间:</strong> {report.get('created_at', '-')}</p>

<h2>诊断结论</h2>
<div class="verdict">{diagnosis.get('verdict', '-')}</div>
<p>综合评分: <strong>{diagnosis.get('score', 0)}</strong> |
风险收益比: <strong>{diagnosis.get('risk_return_ratio', 0)}</strong> |
{diagnosis.get('sample_size_note', '')}</p>

<h2>核心指标</h2>
<div class="metrics">
  <div class="metric-card"><div class="label">初始资金</div><div class="value">{overview.get('initial_cash', '-')}</div></div>
  <div class="metric-card"><div class="label">最终权益</div><div class="value">{overview.get('final_equity', '-')}</div></div>
  <div class="metric-card"><div class="label">总盈亏</div><div class="value">{overview.get('total_pnl', '-')}</div></div>
  <div class="metric-card"><div class="label">总收益率</div><div class="value">{overview.get('total_pnl_pct', '-')}%</div></div>
  <div class="metric-card"><div class="label">年化收益率</div><div class="value">{overview.get('annualized_return_pct', '-')}%</div></div>
  <div class="metric-card"><div class="label">最大回撤</div><div class="value">{overview.get('max_drawdown', '-')}%</div></div>
  <div class="metric-card"><div class="label">胜率</div><div class="value">{overview.get('win_rate', '-')}%</div></div>
  <div class="metric-card"><div class="label">成交次数</div><div class="value">{overview.get('trade_count', '-')}</div></div>
</div>

<h2>风险调整指标</h2>
<div class="metrics">
  <div class="metric-card"><div class="label">Sharpe Ratio</div><div class="value">{overview.get('sharpe_ratio', '-')}</div></div>
  <div class="metric-card"><div class="label">Sortino Ratio</div><div class="value">{overview.get('sortino_ratio', '-')}</div></div>
  <div class="metric-card"><div class="label">Calmar Ratio</div><div class="value">{overview.get('calmar_ratio', '-')}</div></div>
  <div class="metric-card"><div class="label">最大回撤持续期</div><div class="value">{overview.get('max_drawdown_duration', '-')} 根</div></div>
</div>

<h2>交易质量指标</h2>
<div class="metrics">
  <div class="metric-card"><div class="label">盈亏比</div><div class="value">{overview.get('payoff_ratio', '-')}</div></div>
  <div class="metric-card"><div class="label">盈利因子</div><div class="value">{overview.get('profit_factor', '-')}</div></div>
  <div class="metric-card"><div class="label">期望收益</div><div class="value">{overview.get('expectancy', '-')}</div></div>
  <div class="metric-card"><div class="label">平均盈利</div><div class="value">{overview.get('avg_win', '-')}</div></div>
  <div class="metric-card"><div class="label">平均亏损</div><div class="value">{overview.get('avg_loss', '-')}</div></div>
  <div class="metric-card"><div class="label">最大连续盈利</div><div class="value">{overview.get('max_consecutive_wins', '-')} 次</div></div>
  <div class="metric-card"><div class="label">最大连续亏损</div><div class="value">{overview.get('max_consecutive_losses', '-')} 次</div></div>
  <div class="metric-card"><div class="label">恢复因子</div><div class="value">{overview.get('recovery_factor', '-')}</div></div>
</div>

{"<h2>平仓原因分布</h2><ul>" + reason_items + "</ul>" if reason_items else ""}

<h2>最佳 / 最差交易</h2>
<ul>
<li>{best_line}</li>
<li>{worst_line}</li>
</ul>

{"<h2>成交明细</h2><table><thead><tr><th>时间</th><th>方向</th><th>价格</th><th>数量</th><th>盈亏</th><th>收益率</th><th>持仓天数</th><th>原因</th></tr></thead><tbody>" + rows + "</tbody></table>" if rows else ""}

{"<h2>风险提示</h2><ul>" + risk_items + "</ul>" if risk_items else ""}

{"<h2>下一步建议</h2><ul>" + action_items + "</ul>" if action_items else ""}

<div class="footer">由量化交易系统自动生成，回测 ID: {report.get('run_id', '')}</div>
</body>
</html>"""
        return html

    def _build_run_report(self, detail: dict) -> dict:
        summary = detail.get("summary") or {}
        trades_page = detail.get("trades") or {}
        trades = trades_page.get("items") or []
        sell_trades = [trade for trade in trades if trade.get("accepted") and trade.get("side") == "sell"]
        buy_trades = [trade for trade in trades if trade.get("accepted") and trade.get("side") == "buy"]
        best_trade = max(sell_trades, key=lambda trade: float(trade.get("realized_pnl") or 0), default=None)
        worst_trade = min(sell_trades, key=lambda trade: float(trade.get("realized_pnl") or 0), default=None)
        report = {
            "run_id": detail.get("run_id"),
            "title": self._report_title(detail),
            "scope": detail.get("scope"),
            "symbol": detail.get("symbol"),
            "pool_code": detail.get("pool_code"),
            "period": detail.get("period"),
            "strategy_mode": detail.get("strategy_mode"),
            "status": detail.get("status"),
            "created_at": detail.get("created_at"),
            "overview": {
                "initial_cash": summary.get("initial_cash") or detail.get("initial_cash"),
                "final_equity": summary.get("final_equity") or detail.get("final_equity"),
                "total_pnl": summary.get("total_pnl") or detail.get("total_pnl"),
                "total_pnl_pct": summary.get("total_pnl_pct") or detail.get("total_pnl_pct"),
                "annualized_return_pct": summary.get("annualized_return_pct"),
                "max_drawdown": summary.get("max_drawdown") or detail.get("max_drawdown"),
                "win_rate": summary.get("win_rate") or detail.get("win_rate"),
                "trade_count": summary.get("trade_count") or detail.get("trade_count"),
                "round_trip_count": summary.get("round_trip_count") or detail.get("round_trip_count"),
                "total_fees": summary.get("total_fees"),
                "sharpe_ratio": summary.get("sharpe_ratio"),
                "sortino_ratio": summary.get("sortino_ratio"),
                "calmar_ratio": summary.get("calmar_ratio"),
                "max_drawdown_duration": summary.get("max_drawdown_duration"),
                "profit_factor": summary.get("profit_factor"),
                "avg_win": summary.get("avg_win"),
                "avg_loss": summary.get("avg_loss"),
                "payoff_ratio": summary.get("payoff_ratio"),
                "expectancy": summary.get("expectancy"),
                "max_consecutive_wins": summary.get("max_consecutive_wins"),
                "max_consecutive_losses": summary.get("max_consecutive_losses"),
                "recovery_factor": summary.get("recovery_factor"),
            },
            "diagnosis": self._report_diagnosis(summary, detail),
            "trade_analysis": {
                "included_trade_count": len(trades),
                "included_buy_count": len(buy_trades),
                "included_sell_count": len(sell_trades),
                "best_trade": self._compact_report_trade(best_trade),
                "worst_trade": self._compact_report_trade(worst_trade),
                "exit_reasons": self._trade_reason_counts(sell_trades),
                "top_trades": [self._compact_report_trade(trade) for trade in sell_trades[:10]],
            },
            "risk_notes": self._report_risk_notes(summary),
            "next_actions": self._report_next_actions(summary, detail),
        }
        return report

    def _report_title(self, detail: dict) -> str:
        target = detail.get("symbol") or detail.get("pool_code") or "unknown"
        return f"{target} {detail.get('period') or 'daily'} {detail.get('strategy_mode') or ''} 回测报告".strip()

    def _report_diagnosis(self, summary: dict, detail: dict) -> dict:
        total_pnl_pct = float(summary.get("total_pnl_pct") or detail.get("total_pnl_pct") or 0)
        max_drawdown = float(summary.get("max_drawdown") or detail.get("max_drawdown") or 0)
        win_rate = float(summary.get("win_rate") or detail.get("win_rate") or 0)
        trade_count = int(summary.get("trade_count") or detail.get("trade_count") or 0)
        if total_pnl_pct > 0 and max_drawdown <= max(1.0, total_pnl_pct * 2):
            verdict = "表现较稳健"
        elif total_pnl_pct > 0:
            verdict = "盈利但回撤需要关注"
        elif trade_count == 0:
            verdict = "无成交，策略条件可能过严或数据不足"
        else:
            verdict = "当前参数表现不佳"
        return {
            "verdict": verdict,
            "score": self._comparison_score({"total_pnl_pct": total_pnl_pct, "max_drawdown": max_drawdown, "win_rate": win_rate, "trade_count": trade_count}),
            "risk_return_ratio": self._risk_return_ratio({"total_pnl_pct": total_pnl_pct, "max_drawdown": max_drawdown}),
            "sample_size_note": "成交样本偏少，结论仅供观察" if trade_count < 10 else "成交样本数量相对更有参考价值",
        }

    def _compact_report_trade(self, trade: dict | None) -> dict | None:
        if not trade:
            return None
        decision = trade.get("decision") or {}
        return {
            "trade_time": trade.get("trade_time"),
            "side": trade.get("side"),
            "price": trade.get("price"),
            "quantity": trade.get("quantity"),
            "realized_pnl": trade.get("realized_pnl"),
            "realized_pnl_pct": trade.get("realized_pnl_pct"),
            "holding_days": trade.get("holding_days"),
            "reason": trade.get("reason"),
            "triggered_rules": decision.get("triggered_rules") or [],
            "metrics": decision.get("metrics") or {},
        }

    def _trade_reason_counts(self, trades: list[dict]) -> list[dict]:
        counts: dict[str, int] = {}
        for trade in trades:
            reason = str(trade.get("reason") or "unknown")
            counts[reason] = counts.get(reason, 0) + 1
        return [{"reason": reason, "count": count} for reason, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)]

    def _report_risk_notes(self, summary: dict) -> list[str]:
        notes = []
        if float(summary.get("max_drawdown") or 0) > 5:
            notes.append("最大回撤偏高，建议收紧止损或降低单次交易数量。")
        if float(summary.get("win_rate") or 0) < 45 and int(summary.get("round_trip_count") or 0) > 0:
            notes.append("胜率偏低，建议提高最低趋势分或最低置信度。")
        if int(summary.get("trade_count") or 0) < 6:
            notes.append("成交次数偏少，建议放宽策略条件或扩大回测时间范围后再判断。")
        return notes or ["暂无明显风险提示，建议结合更长时间窗口继续验证。"]

    def _report_next_actions(self, summary: dict, detail: dict) -> list[str]:
        actions = ["使用 /api/v1/backtest/strategy-compare 比较 strict、normal、loose 策略模式。"]
        if int(summary.get("trade_count") or detail.get("trade_count") or 0) >= 6:
            actions.append("使用 /api/v1/backtest/grid-optimize 对止盈、止损、趋势分和置信度继续调参。")
        actions.append("查看成交明细里的 decision 和 feature_snapshot，确认主要买卖原因是否符合预期。")
        return actions

    def get_run_detail(
        self,
        run_id: str,
        include_trades: bool = True,
        include_equity: bool = False,
        trades_page_params: PageParams | None = None,
        equity_page_params: PageParams | None = None,
        equity_stride: int = 1,
    ) -> dict:
        with SessionLocal() as session:
            run = session.scalar(select(BacktestRunModel).where(BacktestRunModel.run_id == run_id))
            if run is None:
                raise ValueError("未找到回测记录。")
            data = self._run_to_dict(run)
            data["detail_options"] = {
                "include_trades": include_trades,
                "include_equity": include_equity,
                "equity_stride": max(1, equity_stride),
            }
            if include_trades:
                trades_stmt = (
                    select(BacktestTradeModel)
                    .where(BacktestTradeModel.run_id == run_id)
                    .order_by(BacktestTradeModel.trade_time, BacktestTradeModel.id)
                )
                trades_page = paginate(
                    session,
                    trades_stmt,
                    None,
                    trades_page_params or PageParams(page=1, page_size=200),
                    to_dict_fn=self._trade_to_dict,
                )
                data["trades"] = trades_page.to_dict()
            else:
                data["trades"] = None
            if include_equity:
                equity_stmt = (
                    select(BacktestEquityModel)
                    .where(BacktestEquityModel.run_id == run_id)
                    .order_by(BacktestEquityModel.trade_time, BacktestEquityModel.id)
                )
                equity_page = paginate(
                    session,
                    equity_stmt,
                    None,
                    equity_page_params or PageParams(page=1, page_size=200),
                    to_dict_fn=self._equity_to_dict,
                )
                stride = max(1, equity_stride)
                if stride > 1:
                    equity_page.items = equity_page.items[::stride]
                data["equity_curve"] = equity_page.to_dict()
            else:
                data["equity_curve"] = None
            return data

    def _run_to_dict(self, run: BacktestRunModel) -> dict:
        return {
            "run_id": run.run_id,
            "scope": run.scope,
            "symbol": run.symbol,
            "pool_code": run.pool_code,
            "period": run.period,
            "strategy_mode": run.strategy_mode,
            "status": run.status,
            "start_date": run.start_date,
            "end_date": run.end_date,
            "initial_cash": run.initial_cash,
            "quantity": run.quantity,
            "rows_count": run.rows_count,
            "tested_bars": run.tested_bars,
            "trade_count": run.trade_count,
            "round_trip_count": run.round_trip_count,
            "total_pnl": run.total_pnl,
            "total_pnl_pct": run.total_pnl_pct,
            "final_equity": run.final_equity,
            "max_drawdown": run.max_drawdown,
            "win_rate": run.win_rate,
            "summary": self._from_json(run.summary_json),
            "params": self._from_json(run.params_json),
            "rule": self._from_json(run.rule_json),
            "execution_rules": self._from_json(run.execution_rules_json),
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }

    def _trade_to_dict(self, trade: BacktestTradeModel) -> dict:
        payload = self._from_json(trade.payload_json)
        if payload:
            return payload
        return {
            "symbol": trade.symbol,
            "period": trade.period,
            "trade_time": trade.trade_time,
            "side": trade.side,
            "accepted": trade.accepted,
            "quantity": trade.quantity,
            "price": trade.price,
            "requested_price": trade.requested_price,
            "amount": trade.amount,
            "total_fee": trade.total_fee,
            "realized_pnl": trade.realized_pnl,
            "reason": trade.reason,
        }

    def _equity_to_dict(self, item: BacktestEquityModel) -> dict:
        return {
            "symbol": item.symbol,
            "period": item.period,
            "trade_time": item.trade_time,
            "cash": item.cash,
            "market_value": item.market_value,
            "equity": item.equity,
            "realized_pnl": item.realized_pnl,
            "unrealized_pnl": item.unrealized_pnl,
        }

    def _new_run_id(self, prefix: str) -> str:
        return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"

    def _to_json(self, value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)

    def _from_json(self, value: str | None) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _compute_features(self, symbol: str, period: str, rows: list[dict]) -> list[dict]:
        closes = [self._to_float(row.get("close")) for row in rows]
        volumes = [self._to_float(row.get("volume")) for row in rows]
        highs = [self._to_float(row.get("high")) for row in rows]
        lows = [self._to_float(row.get("low")) for row in rows]
        results = []
        for i, row in enumerate(rows):
            close = closes[i]
            if close is None or i < 60:
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

    def _evaluate_open(self, item: dict, rule: dict[str, Any]) -> dict:
        confidence = self._confidence(item)
        direction = self._direction(item)
        trend_score = item.get("trend_score") or 0
        volatility = item.get("volatility_20") or 0
        price_position = item.get("price_position_60") or item.get("price_position_20") or 0
        volume_ratio = item.get("volume_ratio_5") or 0
        signal = item.get("signal")
        checks = [
            ("direction_allowed", direction in rule["allowed_directions"], "预测方向不符合当前策略模式"),
            ("confidence_min", confidence >= rule["min_confidence"], "预测置信度不足"),
            ("trend_or_signal", trend_score >= rule["min_trend_score"] or signal in rule["allowed_signals"], "趋势分或信号未达标"),
            ("volatility_max", volatility <= rule["max_volatility"], "20周期波动率过高"),
            ("price_position_range", rule["min_price_position"] <= price_position <= rule["max_price_position"], "价格区间位置不适合追入"),
            ("volume_ratio_min", volume_ratio >= rule["min_volume_ratio"], "成交量配合不足"),
            ("price_max", (item.get("close") or 0) <= rule["max_price"], "当前价格超过自动买入上限"),
        ]
        failed = [{"code": code, "reason": reason} for code, ok, reason in checks if not ok]
        passed = [code for code, ok, _reason in checks if ok]
        return {
            "allowed": not failed,
            "reason": "通过回测开仓规则" if not failed else "；".join(item["reason"] for item in failed),
            "triggered_rules": passed if not failed else [],
            "failed_rules": failed,
            "metrics": {
                "direction": direction,
                "confidence": confidence,
                "trend_score": trend_score,
                "signal": signal,
                "volatility_20": volatility,
                "price_position_60": price_position,
                "volume_ratio_5": volume_ratio,
            },
        }

    def _evaluate_close(self, item: dict, position: BacktestPosition, rule: dict[str, Any]) -> dict:
        close = item.get("close") or position.avg_price
        pnl_pct = (close - position.avg_price) / position.avg_price if position.avg_price else 0
        direction = self._direction(item)
        signal = item.get("signal")
        trend_score = item.get("trend_score") or 50
        triggered = []
        if pnl_pct >= rule["take_profit_pct"]:
            triggered.append({"code": "take_profit", "reason": "达到止盈线"})
        if pnl_pct <= -rule["stop_loss_pct"]:
            triggered.append({"code": "stop_loss", "reason": "触发止损线"})
        if direction == "down" or signal == "bearish":
            triggered.append({"code": "prediction_weakened", "reason": "预测转弱"})
        if direction == "flat" and trend_score < rule["min_trend_score"]:
            triggered.append({"code": "trend_flat_weak", "reason": "趋势分不足且方向走平"})
        return {
            "allowed": bool(triggered),
            "reason": "；".join(item["reason"] for item in triggered) if triggered else "未触发平仓规则",
            "triggered_rules": triggered,
            "failed_rules": [],
            "metrics": {
                "direction": direction,
                "signal": signal,
                "trend_score": trend_score,
                "unrealized_pnl_pct": round(pnl_pct * 100, 2),
                "take_profit_pct": round(rule["take_profit_pct"] * 100, 2),
                "stop_loss_pct": round(rule["stop_loss_pct"] * 100, 2),
            },
        }

    def _build_trade(
        self,
        side: str,
        symbol: str,
        trade_time: str,
        execution,
        cash: float,
        reason: str,
        feature: dict | None = None,
        decision: dict | None = None,
    ) -> dict:
        payload = execution.to_dict()
        feature = feature or {}
        decision = decision or {}
        decision_reason = decision.get("reason") or reason
        return {
            "accepted": True,
            "side": side,
            "symbol": symbol,
            "trade_time": trade_time,
            "reason": reason,
            "price": payload["price"],
            "requested_price": payload["requested_price"],
            "quantity": payload["quantity"],
            "gross_amount": payload["gross_amount"],
            "amount": payload["amount"],
            "commission": payload["commission"],
            "stamp_duty": payload["stamp_duty"],
            "transfer_fee": payload["transfer_fee"],
            "total_fee": payload["total_fee"],
            "cash": cash,
            "execution": payload,
            "decision": {
                "allowed": decision.get("allowed"),
                "reason": decision_reason,
                "triggered_rules": decision.get("triggered_rules") or [],
                "failed_rules": decision.get("failed_rules") or [],
                "metrics": decision.get("metrics") or {},
            },
            "explain": {
                "action": "开仓" if side == "buy" else "平仓",
                "summary": decision_reason,
                "key_metrics": decision.get("metrics") or {},
            },
            "feature_snapshot": {
                "close": feature.get("close"),
                "ma5": feature.get("ma5"),
                "ma10": feature.get("ma10"),
                "ma20": feature.get("ma20"),
                "ma60": feature.get("ma60"),
                "return_1": feature.get("return_1"),
                "return_5": feature.get("return_5"),
                "return_20": feature.get("return_20"),
                "volatility_20": feature.get("volatility_20"),
                "volume_ratio_5": feature.get("volume_ratio_5"),
                "price_position_20": feature.get("price_position_20"),
                "price_position_60": feature.get("price_position_60"),
                "trend_direction": feature.get("trend_direction"),
                "trend_score": feature.get("trend_score"),
                "signal": feature.get("signal"),
            },
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
            "allowed_directions": ["up"],
            "allowed_signals": ["bullish"],
        }
        if mode == "normal":
            base.update({
                "min_confidence": 0.52,
                "min_trend_score": 56.0,
                "max_volatility": 0.075,
                "min_price_position": 0.25,
                "max_price_position": 0.93,
                "min_volume_ratio": 0.55,
                "take_profit_pct": 0.06,
                "stop_loss_pct": 0.05,
                "allowed_signals": ["bullish", "neutral"],
            })
        elif mode == "loose":
            base.update({
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
            })
        return base

    def _filter_rows(self, rows: list[dict], start_date: str | None, end_date: str | None) -> list[dict]:
        result = []
        for row in rows:
            trade_time = str(row.get("trade_time") or row.get("date") or row.get("datetime") or "")
            if start_date and trade_time < start_date:
                continue
            if end_date and trade_time > end_date + "T23:59:59":
                continue
            result.append(row)
        return result

    def _max_drawdown(self, equity_curve: list[dict]) -> float:
        peak = None
        max_drawdown = 0.0
        for item in equity_curve:
            equity = float(item.get("equity") or 0)
            if peak is None or equity > peak:
                peak = equity
            if peak:
                drawdown = (peak - equity) / peak * 100
                max_drawdown = max(max_drawdown, drawdown)
        return round(max_drawdown, 2)

    def _max_drawdown_duration(self, equity_curve: list[dict]) -> int:
        peak = None
        max_duration = 0
        current_duration = 0
        for item in equity_curve:
            equity = float(item.get("equity") or 0)
            if peak is None or equity >= peak:
                peak = equity
                current_duration = 0
            else:
                current_duration += 1
                max_duration = max(max_duration, current_duration)
        return max_duration

    def _equity_returns(self, equity_curve: list[dict]) -> list[float]:
        equities = [float(item.get("equity") or 0) for item in equity_curve]
        returns = []
        for i in range(1, len(equities)):
            if equities[i - 1] > 0:
                returns.append((equities[i] - equities[i - 1]) / equities[i - 1])
        return returns

    def _sharpe_ratio(self, equity_curve: list[dict], risk_free_rate: float = 0.03) -> float:
        returns = self._equity_returns(equity_curve)
        if len(returns) < 2:
            return 0.0
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
        std_return = variance ** 0.5
        if std_return == 0:
            return 0.0
        daily_rf = risk_free_rate / 252
        annualized_return = mean_return * 252
        annualized_std = std_return * (252 ** 0.5)
        return round((annualized_return - risk_free_rate) / annualized_std, 4)

    def _sortino_ratio(self, equity_curve: list[dict], risk_free_rate: float = 0.03) -> float:
        returns = self._equity_returns(equity_curve)
        if len(returns) < 2:
            return 0.0
        mean_return = sum(returns) / len(returns)
        downside_returns = [min(r, 0) ** 2 for r in returns]
        downside_variance = sum(downside_returns) / len(downside_returns)
        downside_std = downside_variance ** 0.5
        if downside_std == 0:
            return 0.0
        annualized_return = mean_return * 252
        annualized_downside = downside_std * (252 ** 0.5)
        return round((annualized_return - risk_free_rate) / annualized_downside, 4)

    def _calmar_ratio(self, initial_cash: float, final_equity: float, max_drawdown: float, tested_bars: int) -> float:
        if max_drawdown == 0 or initial_cash == 0 or tested_bars == 0:
            return 0.0
        total_return = (final_equity - initial_cash) / initial_cash
        years = tested_bars / 252
        if years <= 0:
            return 0.0
        annualized_return = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1
        return round(annualized_return / (max_drawdown / 100), 4)

    def _annualized_return(self, initial_cash: float, final_equity: float, tested_bars: int) -> float:
        if initial_cash <= 0 or tested_bars <= 0:
            return 0.0
        total_return = (final_equity - initial_cash) / initial_cash
        years = tested_bars / 252
        if years <= 0:
            return 0.0
        if total_return <= -1:
            return -100.0
        annualized = ((1 + total_return) ** (1 / years) - 1) * 100
        return round(annualized, 4)

    def _profit_factor(self, sell_trades: list[dict]) -> float:
        gross_profit = sum(float(t.get("realized_pnl") or 0) for t in sell_trades if float(t.get("realized_pnl") or 0) > 0)
        gross_loss = abs(sum(float(t.get("realized_pnl") or 0) for t in sell_trades if float(t.get("realized_pnl") or 0) < 0))
        if gross_loss == 0:
            return round(gross_profit, 4) if gross_profit > 0 else 0.0
        return round(gross_profit / gross_loss, 4)

    def _avg_win_loss(self, sell_trades: list[dict]) -> tuple[float, float, float]:
        wins = [float(t.get("realized_pnl") or 0) for t in sell_trades if float(t.get("realized_pnl") or 0) > 0]
        losses = [abs(float(t.get("realized_pnl") or 0)) for t in sell_trades if float(t.get("realized_pnl") or 0) < 0]
        avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
        avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
        payoff = round(avg_win / avg_loss, 4) if avg_loss > 0 else 0.0
        return avg_win, avg_loss, payoff

    def _max_consecutive(self, sell_trades: list[dict]) -> tuple[int, int]:
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        for trade in sell_trades:
            pnl = float(trade.get("realized_pnl") or 0)
            if pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif pnl < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0
        return max_wins, max_losses

    def _expectancy(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        return round((win_rate / 100) * avg_win - ((100 - win_rate) / 100) * avg_loss, 2)

    def _compute_advanced_metrics(
        self,
        equity_curve: list[dict],
        sell_trades: list[dict],
        initial_cash: float,
        final_equity: float,
        tested_bars: int,
        win_rate: float,
        max_drawdown: float,
    ) -> dict:
        avg_win, avg_loss, payoff = self._avg_win_loss(sell_trades)
        max_consecutive_wins, max_consecutive_losses = self._max_consecutive(sell_trades)
        return {
            "annualized_return_pct": self._annualized_return(initial_cash, final_equity, tested_bars),
            "sharpe_ratio": self._sharpe_ratio(equity_curve),
            "sortino_ratio": self._sortino_ratio(equity_curve),
            "calmar_ratio": self._calmar_ratio(initial_cash, final_equity, max_drawdown, tested_bars),
            "max_drawdown_duration": self._max_drawdown_duration(equity_curve),
            "profit_factor": self._profit_factor(sell_trades),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff_ratio": payoff,
            "expectancy": self._expectancy(win_rate, avg_win, avg_loss),
            "max_consecutive_wins": max_consecutive_wins,
            "max_consecutive_losses": max_consecutive_losses,
            "recovery_factor": round(float(final_equity - initial_cash) / (max_drawdown / 100 * initial_cash), 4) if max_drawdown > 0 and initial_cash > 0 else 0.0,
        }

    def _holding_bars(self, features: list[dict], entry_time: str, exit_time: str) -> int:
        times = [item["trade_time"] for item in features]
        try:
            return times.index(exit_time) - times.index(entry_time)
        except ValueError:
            return 0

    def _execution_rules(self) -> dict:
        return {
            "slippage_bps": settings.paper_slippage_bps,
            "commission_rate": settings.paper_commission_rate,
            "min_commission": settings.paper_min_commission,
            "stamp_duty_rate": settings.paper_stamp_duty_rate,
            "transfer_fee_rate": settings.paper_transfer_fee_rate,
        }

    def _direction(self, item: dict) -> str:
        signal = item.get("signal")
        trend = item.get("trend_direction")
        if signal == "bullish" or trend in {"strong_up", "up"}:
            return "up"
        if signal == "bearish" or trend in {"strong_down", "down"}:
            return "down"
        return "flat"

    def _confidence(self, item: dict) -> float:
        base = 0.45
        trend_score = item.get("trend_score") or 50
        confidence = base + abs(trend_score - 50) / 100
        volatility = item.get("volatility_20") or 0
        if volatility > 0.06:
            confidence -= 0.08
        return round(max(0.3, min(0.82, confidence)), 4)

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
