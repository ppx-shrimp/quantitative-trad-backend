from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, inspect, select, text

from quant_system.api.pagination import PageParams, PageResult, paginate
from quant_system.brokers.execution import build_trade_execution
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import PaperAccountModel, PaperCashFlowModel, PaperOrderModel, PaperPositionModel
from quant_system.domain.models import Position


class SQLAlchemyPaperBroker:
    """基于 SQLAlchemy 的模拟券商，可通过 QUANT_DATABASE_URL 切换 SQLite/MySQL。"""

    def __init__(self, initial_cash: float) -> None:
        self.initial_cash = initial_cash
        self.initialize()

    @property
    def cash(self) -> float:
        account = self._get_account()
        return float(account.cash)

    def initialize(self) -> None:
        init_sqlalchemy_tables()
        self._ensure_schema_compatibility()
        now = self._now()
        with SessionLocal() as session:
            account = session.get(PaperAccountModel, "default")
            if account is None:
                session.add(
                    PaperAccountModel(
                        account_id="default",
                        initial_cash=self.initial_cash,
                        cash=self.initial_cash,
                        realized_pnl=0,
                        created_at=now,
                        updated_at=now,
                        created_by="system",
                        updated_by="system",
                    )
                )
                session.commit()

    def _ensure_schema_compatibility(self) -> None:
        """补齐 create_all 不会自动迁移的旧模拟交易表字段。"""
        with SessionLocal() as session:
            bind = session.get_bind()
            inspector = inspect(bind)
            if not inspector.has_table("paper_orders"):
                return
            columns = {column["name"] for column in inspector.get_columns("paper_orders")}
            statements: list[str] = []
            if "updated_at" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN updated_at VARCHAR(64)")
            if "created_by" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN created_by VARCHAR(64) DEFAULT 'system'")
            if "updated_by" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN updated_by VARCHAR(64) DEFAULT 'system'")
            if "strategy_mode" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN strategy_mode VARCHAR(32)")
            if "decision_json" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN decision_json TEXT")
            if "source_type" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN source_type VARCHAR(64)")
            if "source_id" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN source_id VARCHAR(128)")
            if "source_action" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN source_action VARCHAR(32)")
            if "source_confidence" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN source_confidence FLOAT")
            if "source_memo" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN source_memo TEXT")
            if "audit_json" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN audit_json TEXT")
            if "requested_price" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN requested_price FLOAT")
            if "gross_amount" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN gross_amount FLOAT")
            if "commission" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN commission FLOAT")
            if "stamp_duty" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN stamp_duty FLOAT")
            if "transfer_fee" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN transfer_fee FLOAT")
            if "total_fee" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN total_fee FLOAT")
            if "realized_pnl" not in columns:
                statements.append("ALTER TABLE paper_orders ADD COLUMN realized_pnl FLOAT")
            if not statements:
                return

            for statement in statements:
                session.execute(text(statement))
            session.execute(text("UPDATE paper_orders SET updated_at = COALESCE(updated_at, created_at), created_by = COALESCE(created_by, 'system'), updated_by = COALESCE(updated_by, 'system')"))
            session.commit()

    def buy(self, symbol: str, quantity: int, price: float, decision: dict | None = None) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        now = self._now()
        execution = build_trade_execution("buy", quantity, price)
        cost = execution.amount
        with SessionLocal() as session:
            account = self._get_account_for_update(session)
            cash = float(account.cash)
            if cost > cash:
                order = self._build_order_result(False, "buy", normalized_symbol, quantity, execution.price, cash, "模拟账户现金不足", execution)
                self._insert_order(session, order, decision=decision, status="rejected")
                session.commit()
                return order

            current = self._get_position_for_update(session, normalized_symbol)
            if current:
                total_quantity = int(current.quantity) + quantity
                current.avg_price = ((float(current.avg_price) * int(current.quantity)) + cost) / total_quantity
                current.quantity = total_quantity
                current.updated_at = now
                current.updated_by = "system"
            else:
                session.add(
                    PaperPositionModel(
                        symbol=normalized_symbol,
                        quantity=quantity,
                        avg_price=cost / quantity,
                        opened_at=now,
                        created_at=now,
                        updated_at=now,
                        created_by="system",
                        updated_by="system",
                    )
                )

            cash_after = round(cash - cost, 2)
            account.cash = cash_after
            account.updated_at = now
            account.updated_by = "system"
            order = self._build_order_result(True, "buy", normalized_symbol, quantity, execution.price, cash_after, None, execution)
            self._insert_order(session, order, decision=decision, status="filled")
            self._insert_cash_flow(session, order, amount=execution.cash_delta, cash_after=cash_after, note="模拟买入（含滑点和交易费用）")
            session.commit()
            return order

    def sell(self, symbol: str, quantity: int | None, price: float, decision: dict | None = None) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        now = self._now()
        with SessionLocal() as session:
            account = self._get_account_for_update(session)
            current = self._get_position_for_update(session, normalized_symbol)
            cash = float(account.cash)
            if not current:
                order = self._build_order_result(False, "sell", normalized_symbol, quantity or 0, price, cash, "没有可平仓持仓")
                self._insert_order(session, order, decision=decision, status="rejected")
                session.commit()
                return order

            current_quantity = int(current.quantity)
            sell_quantity = quantity or current_quantity
            if sell_quantity > current_quantity:
                order = self._build_order_result(False, "sell", normalized_symbol, sell_quantity, price, cash, "平仓数量超过持仓数量")
                self._insert_order(session, order, decision=decision, status="rejected")
                session.commit()
                return order

            execution = build_trade_execution("sell", sell_quantity, price)
            amount = execution.amount
            cash_after = round(cash + amount, 2)
            realized_pnl = amount - (float(current.avg_price) * sell_quantity)
            remain_quantity = current_quantity - sell_quantity
            if remain_quantity == 0:
                session.delete(current)
            else:
                current.quantity = remain_quantity
                current.updated_at = now
                current.updated_by = "system"
            account.cash = cash_after
            account.realized_pnl = float(account.realized_pnl) + realized_pnl
            account.updated_at = now
            account.updated_by = "system"
            order = self._build_order_result(True, "sell", normalized_symbol, sell_quantity, execution.price, cash_after, None, execution)
            order["realized_pnl"] = round(realized_pnl, 2)
            self._insert_order(session, order, decision=decision, status="filled")
            self._insert_cash_flow(session, order, amount=execution.cash_delta, cash_after=cash_after, note="模拟卖出（含滑点和交易费用）")
            session.commit()
            return order

    def get_position(self, symbol: str) -> Position | None:
        with SessionLocal() as session:
            row = session.get(PaperPositionModel, self._normalize_symbol(symbol))
            if row is None:
                return None
            return Position(symbol=row.symbol, quantity=int(row.quantity), avg_price=float(row.avg_price), opened_at=datetime.fromisoformat(row.opened_at))

    def has_position(self, symbol: str) -> bool:
        return self.get_position(symbol) is not None

    def position_count(self) -> int:
        with SessionLocal() as session:
            return int(session.scalar(select(func.count(PaperPositionModel.symbol))) or 0)

    def list_positions(self) -> list[dict]:
        with SessionLocal() as session:
            rows = session.scalars(select(PaperPositionModel).order_by(PaperPositionModel.opened_at.asc())).all()
            return [
                {
                    "symbol": row.symbol,
                    "quantity": int(row.quantity),
                    "avg_price": round(float(row.avg_price), 2),
                    "opened_at": row.opened_at,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "created_by": row.created_by,
                    "updated_by": row.updated_by,
                }
                for row in rows
            ]

    def list_orders(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
        with SessionLocal() as session:
            stmt = select(PaperOrderModel)
            if symbol:
                stmt = stmt.where(PaperOrderModel.symbol == self._normalize_symbol(symbol))
            rows = session.scalars(stmt.order_by(PaperOrderModel.created_at.desc()).limit(limit)).all()
            return [self._order_to_dict(row) for row in rows]

    def list_orders_page(
        self,
        page_params: PageParams,
        symbol: str | None = None,
        side: str | None = None,
        status: str | None = None,
        strategy_mode: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PageResult:
        stmt = select(PaperOrderModel).order_by(PaperOrderModel.created_at.desc())
        if symbol:
            stmt = stmt.where(PaperOrderModel.symbol == self._normalize_symbol(symbol))
        if side:
            stmt = stmt.where(PaperOrderModel.side == side)
        if status:
            stmt = stmt.where(PaperOrderModel.status == status)
        if strategy_mode:
            stmt = stmt.where(PaperOrderModel.strategy_mode == strategy_mode)
        if start_date:
            stmt = stmt.where(PaperOrderModel.created_at >= start_date)
        if end_date:
            stmt = stmt.where(PaperOrderModel.created_at <= end_date + "T23:59:59")
        with SessionLocal() as session:
            return paginate(session, stmt, None, page_params, to_dict_fn=self._order_to_dict)

    def list_cash_flows(self, limit: int = 100) -> list[dict]:
        with SessionLocal() as session:
            rows = session.scalars(select(PaperCashFlowModel).order_by(PaperCashFlowModel.created_at.desc()).limit(limit)).all()
            return [self._cash_flow_to_dict(row) for row in rows]

    def list_cash_flows_page(self, page_params: PageParams) -> PageResult:
        stmt = select(PaperCashFlowModel).order_by(PaperCashFlowModel.created_at.desc())
        with SessionLocal() as session:
            return paginate(session, stmt, None, page_params, to_dict_fn=self._cash_flow_to_dict)

    def get_daily_buy_amount(self, date: str | None = None) -> float:
        """返回指定日期已成交买入订单的实际支出金额。"""
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        day_start = date + "T00:00:00"
        day_end = date + "T23:59:59"
        with SessionLocal() as session:
            total = session.scalar(
                select(func.sum(PaperOrderModel.amount)).where(
                    PaperOrderModel.side == "buy",
                    PaperOrderModel.status == "filled",
                    PaperOrderModel.created_at >= day_start,
                    PaperOrderModel.created_at <= day_end,
                )
            )
            return round(float(total or 0), 2)

    def get_pnl_stats(
        self,
        strategy_mode: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """盈亏统计：从已成交卖出订单聚合计算。"""
        with SessionLocal() as session:
            # 基础过滤：只看已成交的卖出订单
            base_filters = [
                PaperOrderModel.side == "sell",
                PaperOrderModel.status == "filled",
                PaperOrderModel.realized_pnl.isnot(None),
            ]
            if strategy_mode:
                base_filters.append(PaperOrderModel.strategy_mode == strategy_mode)
            if start_date:
                base_filters.append(PaperOrderModel.created_at >= start_date)
            if end_date:
                base_filters.append(PaperOrderModel.created_at <= end_date + "T23:59:59")

            # 总体统计
            overall = session.execute(
                select(
                    func.count(PaperOrderModel.id).label("total_trades"),
                    func.sum(PaperOrderModel.realized_pnl).label("total_pnl"),
                    func.sum(PaperOrderModel.amount).label("total_turnover"),
                    func.avg(PaperOrderModel.realized_pnl).label("avg_pnl"),
                ).where(*base_filters)
            ).one()

            total_trades = int(overall.total_trades or 0)
            total_pnl = round(float(overall.total_pnl or 0), 2)
            total_turnover = round(float(overall.total_turnover or 0), 2)
            avg_pnl = round(float(overall.avg_pnl or 0), 2) if total_trades > 0 else 0

            # 盈利/亏损笔数
            win_filters = base_filters + [PaperOrderModel.realized_pnl > 0]
            loss_filters = base_filters + [PaperOrderModel.realized_pnl < 0]

            win_count = int(session.scalar(select(func.count(PaperOrderModel.id)).where(*win_filters)) or 0)
            loss_count = int(session.scalar(select(func.count(PaperOrderModel.id)).where(*loss_filters)) or 0)
            even_count = total_trades - win_count - loss_count

            gross_profit = round(float(session.scalar(select(func.sum(PaperOrderModel.realized_pnl)).where(*win_filters)) or 0), 2)
            gross_loss = round(float(session.scalar(select(func.sum(PaperOrderModel.realized_pnl)).where(*loss_filters)) or 0), 2)

            avg_win = round(gross_profit / win_count, 2) if win_count > 0 else 0
            avg_loss = round(gross_loss / loss_count, 2) if loss_count > 0 else 0
            win_rate = round(win_count / total_trades * 100, 2) if total_trades > 0 else 0
            profit_factor = round(abs(gross_profit / gross_loss), 2) if gross_loss != 0 else None

            # 按股票分组
            symbol_rows = session.execute(
                select(
                    PaperOrderModel.symbol,
                    func.count(PaperOrderModel.id).label("trades"),
                    func.sum(PaperOrderModel.realized_pnl).label("pnl"),
                )
                .where(*base_filters)
                .group_by(PaperOrderModel.symbol)
                .order_by(func.sum(PaperOrderModel.realized_pnl).asc())
            ).all()

            by_symbol = [
                {
                    "symbol": row.symbol,
                    "trades": int(row.trades),
                    "pnl": round(float(row.pnl or 0), 2),
                }
                for row in symbol_rows
            ]

            # 按策略模式分组
            mode_rows = session.execute(
                select(
                    PaperOrderModel.strategy_mode,
                    func.count(PaperOrderModel.id).label("trades"),
                    func.sum(PaperOrderModel.realized_pnl).label("pnl"),
                )
                .where(*base_filters)
                .group_by(PaperOrderModel.strategy_mode)
                .order_by(func.sum(PaperOrderModel.realized_pnl).asc())
            ).all()

            by_strategy = [
                {
                    "strategy_mode": row.strategy_mode or "unknown",
                    "trades": int(row.trades),
                    "pnl": round(float(row.pnl or 0), 2),
                }
                for row in mode_rows
            ]

            # 买入统计
            buy_filters = [PaperOrderModel.side == "buy", PaperOrderModel.status == "filled"]
            if strategy_mode:
                buy_filters.append(PaperOrderModel.strategy_mode == strategy_mode)
            if start_date:
                buy_filters.append(PaperOrderModel.created_at >= start_date)
            if end_date:
                buy_filters.append(PaperOrderModel.created_at <= end_date + "T23:59:59")

            buy_count = int(session.scalar(select(func.count(PaperOrderModel.id)).where(*buy_filters)) or 0)
            buy_amount = round(float(session.scalar(select(func.sum(PaperOrderModel.amount)).where(*buy_filters)) or 0), 2)

            return {
                "total_trades": total_trades,
                "total_pnl": total_pnl,
                "total_turnover": total_turnover,
                "total_turnover_semantics": "卖出订单 amount 汇总，表示卖出净回款汇总，不是毛成交额汇总",
                "amount_semantics": self._amount_semantics_meta(),
                "avg_pnl": avg_pnl,
                "win_count": win_count,
                "loss_count": loss_count,
                "even_count": even_count,
                "win_rate": win_rate,
                "gross_profit": gross_profit,
                "gross_loss": gross_loss,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_factor": profit_factor,
                "buy_count": buy_count,
                "buy_amount": buy_amount,
                "by_symbol": by_symbol,
                "by_strategy": by_strategy,
            }

    def get_strategy_evaluation(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """策略表现评估：对每种策略模式做深度分析并横向对比。

        评估指标仍以已平仓/已卖出的 realized_pnl 为准；但策略模式来源改为所有已成交订单，
        这样只有买入未平仓时也能返回买入统计和“待结算”状态，避免前端空白。
        """
        with SessionLocal() as session:
            mode_filters = [PaperOrderModel.status == "filled"]
            if start_date:
                mode_filters.append(PaperOrderModel.created_at >= start_date)
            if end_date:
                mode_filters.append(PaperOrderModel.created_at <= end_date + "T23:59:59")

            raw_modes = session.scalars(
                select(PaperOrderModel.strategy_mode)
                .where(*mode_filters)
                .distinct()
            ).all()

            strategies = {}
            for raw_mode in raw_modes:
                display_name = raw_mode or "unknown"

                buy_filters = [
                    PaperOrderModel.side == "buy",
                    PaperOrderModel.status == "filled",
                    PaperOrderModel.strategy_mode == raw_mode,
                ]
                if start_date:
                    buy_filters.append(PaperOrderModel.created_at >= start_date)
                if end_date:
                    buy_filters.append(PaperOrderModel.created_at <= end_date + "T23:59:59")
                buy_count = int(session.scalar(select(func.count(PaperOrderModel.id)).where(*buy_filters)) or 0)
                buy_amount = round(float(session.scalar(select(func.sum(PaperOrderModel.amount)).where(*buy_filters)) or 0), 2)

                sell_filters = [
                    PaperOrderModel.side == "sell",
                    PaperOrderModel.status == "filled",
                    PaperOrderModel.realized_pnl.isnot(None),
                    PaperOrderModel.strategy_mode == raw_mode,
                ]
                if start_date:
                    sell_filters.append(PaperOrderModel.created_at >= start_date)
                if end_date:
                    sell_filters.append(PaperOrderModel.created_at <= end_date + "T23:59:59")

                pnl_rows = session.scalars(
                    select(PaperOrderModel.realized_pnl)
                    .where(*sell_filters)
                    .order_by(PaperOrderModel.created_at.asc())
                ).all()
                pnl_list = [round(float(p or 0), 2) for p in pnl_rows]
                total_trades = len(pnl_list)

                if total_trades == 0:
                    strategies[display_name] = {
                        "total_trades": 0,
                        "win_count": 0,
                        "loss_count": 0,
                        "even_count": 0,
                        "win_rate": 0,
                        "total_pnl": 0,
                        "avg_pnl": 0,
                        "gross_profit": 0,
                        "gross_loss": 0,
                        "avg_win": 0,
                        "avg_loss": 0,
                        "profit_factor": None,
                        "best_trade": 0,
                        "worst_trade": 0,
                        "max_drawdown": 0,
                        "max_consecutive_wins": 0,
                        "max_consecutive_losses": 0,
                        "std_dev": 0,
                        "sharpe_like": None,
                        "buy_count": buy_count,
                        "buy_amount": buy_amount,
                        "by_symbol": [],
                        "settlement_status": "pending",
                        "message": "该策略已有买入记录，但暂无已结算卖出订单，平仓后会生成盈亏评估。",
                    }
                    continue

                total_pnl = round(sum(pnl_list), 2)
                wins = [p for p in pnl_list if p > 0]
                losses = [p for p in pnl_list if p < 0]
                evens = [p for p in pnl_list if p == 0]
                win_count = len(wins)
                loss_count = len(losses)
                win_rate = round(win_count / total_trades * 100, 2)
                avg_pnl = round(total_pnl / total_trades, 2)
                gross_profit = round(sum(wins), 2)
                gross_loss = round(sum(losses), 2)
                avg_win = round(gross_profit / win_count, 2) if win_count else 0
                avg_loss = round(gross_loss / loss_count, 2) if loss_count else 0
                profit_factor = round(abs(gross_profit / gross_loss), 2) if gross_loss != 0 else None
                best_trade = max(pnl_list)
                worst_trade = min(pnl_list)

                mean = total_pnl / total_trades
                variance = sum((p - mean) ** 2 for p in pnl_list) / total_trades if total_trades > 1 else 0
                std_dev = round(variance ** 0.5, 2)
                sharpe_like = round(mean / std_dev, 2) if std_dev > 0 else None

                cumulative = 0.0
                peak = 0.0
                max_drawdown = 0.0
                for p in pnl_list:
                    cumulative += p
                    if cumulative > peak:
                        peak = cumulative
                    dd = peak - cumulative
                    if dd > max_drawdown:
                        max_drawdown = dd
                max_drawdown = round(max_drawdown, 2)

                max_consec_wins = 0
                max_consec_losses = 0
                cur_wins = 0
                cur_losses = 0
                for p in pnl_list:
                    if p > 0:
                        cur_wins += 1
                        cur_losses = 0
                        max_consec_wins = max(max_consec_wins, cur_wins)
                    elif p < 0:
                        cur_losses += 1
                        cur_wins = 0
                        max_consec_losses = max(max_consec_losses, cur_losses)
                    else:
                        cur_wins = 0
                        cur_losses = 0

                symbol_rows = session.execute(
                    select(
                        PaperOrderModel.symbol,
                        func.count(PaperOrderModel.id).label("trades"),
                        func.sum(PaperOrderModel.realized_pnl).label("pnl"),
                        func.sum(PaperOrderModel.amount).label("turnover"),
                    )
                    .where(*sell_filters)
                    .group_by(PaperOrderModel.symbol)
                    .order_by(func.sum(PaperOrderModel.realized_pnl).desc())
                ).all()
                by_symbol = [
                    {
                        "symbol": r.symbol,
                        "trades": int(r.trades),
                        "pnl": round(float(r.pnl or 0), 2),
                        "turnover": round(float(r.turnover or 0), 2),
                    }
                    for r in symbol_rows
                ]

                strategies[display_name] = {
                    "total_trades": total_trades,
                    "win_count": win_count,
                    "loss_count": loss_count,
                    "even_count": len(evens),
                    "win_rate": win_rate,
                    "total_pnl": total_pnl,
                    "avg_pnl": avg_pnl,
                    "gross_profit": gross_profit,
                    "gross_loss": gross_loss,
                    "avg_win": avg_win,
                    "avg_loss": avg_loss,
                    "profit_factor": profit_factor,
                    "best_trade": best_trade,
                    "worst_trade": worst_trade,
                    "max_drawdown": max_drawdown,
                    "max_consecutive_wins": max_consec_wins,
                    "max_consecutive_losses": max_consec_losses,
                    "std_dev": std_dev,
                    "sharpe_like": sharpe_like,
                    "buy_count": buy_count,
                    "buy_amount": buy_amount,
                    "by_symbol": by_symbol,
                    "settlement_status": "settled",
                }

        ranked = sorted(
            [(mode, s) for mode, s in strategies.items() if s.get("total_trades", 0) > 0],
            key=lambda x: x[1].get("total_pnl", 0),
            reverse=True,
        )
        ranking = [
            {
                "rank": i + 1,
                "strategy_mode": mode,
                "total_pnl": s["total_pnl"],
                "win_rate": s["win_rate"],
                "profit_factor": s.get("profit_factor"),
                "max_drawdown": s["max_drawdown"],
                "total_trades": s["total_trades"],
                "buy_count": s.get("buy_count", 0),
                "buy_amount": s.get("buy_amount", 0),
                "settlement_status": s.get("settlement_status"),
            }
            for i, (mode, s) in enumerate(ranked)
        ]

        return {
            "strategies": strategies,
            "ranking": ranking,
            "total_modes": len(strategies),
            "amount_semantics": self._amount_semantics_meta(),
        }

    def get_daily_report(self, date: str | None = None) -> dict:
        """交易日报：汇总指定日期的订单、资金流水和账户快照。"""
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        day_start = date + "T00:00:00"
        day_end = date + "T23:59:59"

        with SessionLocal() as session:
            # 当日订单
            order_rows = session.scalars(
                select(PaperOrderModel)
                .where(PaperOrderModel.created_at >= day_start, PaperOrderModel.created_at <= day_end)
                .order_by(PaperOrderModel.created_at.asc())
            ).all()
            orders = [self._order_to_dict(row) for row in order_rows]

            # 当日资金流水
            flow_rows = session.scalars(
                select(PaperCashFlowModel)
                .where(PaperCashFlowModel.created_at >= day_start, PaperCashFlowModel.created_at <= day_end)
                .order_by(PaperCashFlowModel.created_at.asc())
            ).all()
            cash_flows = [self._cash_flow_to_dict(row) for row in flow_rows]

        # 买入/卖出汇总
        filled_buys = [o for o in orders if o["side"] == "buy" and o["status"] == "filled"]
        filled_sells = [o for o in orders if o["side"] == "sell" and o["status"] == "filled"]
        rejected = [o for o in orders if o["status"] == "rejected"]

        buy_count = len(filled_buys)
        sell_count = len(filled_sells)
        buy_amount = round(sum(o["amount"] for o in filled_buys), 2)
        sell_amount = round(sum(o["amount"] for o in filled_sells), 2)
        daily_realized_pnl = round(sum(o.get("realized_pnl") or 0 for o in filled_sells), 2)
        net_cash_flow = round(sell_amount - buy_amount, 2)

        # 按股票分组
        symbol_trades: dict[str, list[dict]] = {}
        for o in orders:
            symbol_trades.setdefault(o["symbol"], []).append(o)

        per_symbol = []
        for symbol, trades in symbol_trades.items():
            sym_buys = [t for t in trades if t["side"] == "buy" and t["status"] == "filled"]
            sym_sells = [t for t in trades if t["side"] == "sell" and t["status"] == "filled"]
            sym_pnl = round(sum(t.get("realized_pnl") or 0 for t in sym_sells), 2)
            per_symbol.append({
                "symbol": symbol,
                "buy_count": len(sym_buys),
                "sell_count": len(sym_sells),
                "buy_amount": round(sum(t["amount"] for t in sym_buys), 2),
                "sell_amount": round(sum(t["amount"] for t in sym_sells), 2),
                "realized_pnl": sym_pnl,
            })

        # 当日最后一笔流水后的现金余额
        latest_cash = cash_flows[-1]["cash_after"] if cash_flows else None

        return {
            "date": date,
            "trade_summary": {
                "total_orders": len(orders),
                "filled_buy_count": buy_count,
                "filled_sell_count": sell_count,
                "rejected_count": len(rejected),
                "buy_amount": buy_amount,
                "buy_amount_semantics": "买入订单 amount 汇总，表示含费用买入成本",
                "sell_amount": sell_amount,
                "sell_amount_semantics": "卖出订单 amount 汇总，表示扣费后卖出回款",
                "net_cash_flow": net_cash_flow,
                "net_cash_flow_semantics": "sell_amount - buy_amount，表示当日交易净现金流",
                "daily_realized_pnl": daily_realized_pnl,
            },
            "amount_semantics": self._amount_semantics_meta(),
            "per_symbol": per_symbol,
            "orders": orders,
            "cash_flows": cash_flows,
            "latest_cash": latest_cash,
        }

    def account_summary(self) -> dict:
        account = self._get_account()
        positions = self.list_positions()
        return {
            "account_id": account.account_id,
            "initial_cash": float(account.initial_cash),
            "cash": float(account.cash),
            "realized_pnl": round(float(account.realized_pnl), 2),
            "position_count": len(positions),
            "positions": positions,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
            "created_by": account.created_by,
            "updated_by": account.updated_by,
        }

    def reset(self) -> dict:
        now = self._now()
        with SessionLocal() as session:
            session.query(PaperPositionModel).delete()
            session.query(PaperOrderModel).delete()
            session.query(PaperCashFlowModel).delete()
            account = self._get_account(session)
            account.cash = self.initial_cash
            account.realized_pnl = 0
            account.updated_at = now
            account.updated_by = "system"
            session.commit()
        return self.account_summary()

    def _get_account(self, session=None) -> PaperAccountModel:
        if session is not None:
            account = session.get(PaperAccountModel, "default")
            if account is None:
                raise RuntimeError("模拟账户不存在，请先初始化数据库。")
            return account
        with SessionLocal() as inner_session:
            account = inner_session.get(PaperAccountModel, "default")
            if account is None:
                raise RuntimeError("模拟账户不存在，请先初始化数据库。")
            inner_session.expunge(account)
            return account

    def _get_account_for_update(self, session) -> PaperAccountModel:
        account = session.execute(
            select(PaperAccountModel)
            .where(PaperAccountModel.account_id == "default")
            .with_for_update()
        ).scalar_one_or_none()
        if account is None:
            raise RuntimeError("模拟账户不存在，请先初始化数据库。")
        return account

    def _get_position_for_update(self, session, symbol: str) -> PaperPositionModel | None:
        return session.execute(
            select(PaperPositionModel)
            .where(PaperPositionModel.symbol == symbol)
            .with_for_update()
        ).scalar_one_or_none()

    def _build_order_result(self, accepted: bool, side: str, symbol: str, quantity: int, price: float, cash: float, reason: str | None, execution=None) -> dict:
        order_id = f"PAPER-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        result = {
            "accepted": accepted,
            "order_id": order_id,
            "side": side,
            "symbol": symbol,
            "quantity": quantity,
            "price": round(price, 4),
            "amount": round(quantity * price, 2),
            "cash": round(cash, 2),
        }
        if execution is not None:
            execution_payload = execution.to_dict()
            result.update(
                {
                    "requested_price": execution_payload["requested_price"],
                    "price": execution_payload["price"],
                    "gross_amount": execution_payload["gross_amount"],
                    "commission": execution_payload["commission"],
                    "stamp_duty": execution_payload["stamp_duty"],
                    "transfer_fee": execution_payload["transfer_fee"],
                    "total_fee": execution_payload["total_fee"],
                    "amount": execution_payload["amount"],
                    "cash_delta": execution_payload["cash_delta"],
                    "net_cash_amount": execution_payload["amount"],
                    "execution": execution_payload,
                }
            )
        result.update(
            self._amount_semantics(
                side=side,
                status="filled" if accepted else "rejected",
                amount=result.get("amount"),
                gross_amount=result.get("gross_amount"),
                total_fee=result.get("total_fee"),
                cash_delta=result.get("cash_delta"),
                realized_pnl=result.get("realized_pnl"),
            )
        )
        if reason:
            result["reason"] = reason
        return result

    def _insert_order(self, session, order: dict, decision: dict | None, status: str) -> None:
        now = self._now()
        order["created_at"] = now
        order["updated_at"] = now
        session.add(
            PaperOrderModel(
                order_id=order["order_id"],
                symbol=order["symbol"],
                side=order["side"],
                quantity=order["quantity"],
                price=order["price"],
                amount=order["amount"],
                status=status,
                reason=order.get("reason"),
                strategy_mode=self._extract_strategy_mode(decision),
                decision_json=json.dumps(decision or {}, ensure_ascii=False, default=str),
                source_type=self._extract_audit_text(decision, "source_type"),
                source_id=self._extract_audit_text(decision, "source_id"),
                source_action=self._extract_audit_text(decision, "source_action"),
                source_confidence=self._extract_audit_float(decision, "source_confidence"),
                source_memo=self._extract_audit_text(decision, "source_memo"),
                audit_json=json.dumps((decision or {}).get("audit") or {}, ensure_ascii=False, default=str),
                requested_price=order.get("requested_price"),
                gross_amount=order.get("gross_amount"),
                commission=order.get("commission"),
                stamp_duty=order.get("stamp_duty"),
                transfer_fee=order.get("transfer_fee"),
                total_fee=order.get("total_fee"),
                realized_pnl=order.get("realized_pnl"),
                created_at=now,
                updated_at=now,
                created_by="system",
                updated_by="system",
            )
        )

    def _insert_cash_flow(self, session, order: dict, amount: float, cash_after: float, note: str) -> None:
        now = self._now()
        session.add(
            PaperCashFlowModel(
                order_id=order["order_id"],
                symbol=order["symbol"],
                side=order["side"],
                amount=amount,
                cash_after=cash_after,
                note=note,
                created_at=now,
                updated_at=now,
                created_by="system",
                updated_by="system",
            )
        )

    def _extract_strategy_mode(self, decision: dict | None) -> str | None:
        if not decision:
            return "manual"
        rule_snapshot = decision.get("rule_snapshot") or {}
        mode = rule_snapshot.get("strategy_mode") or decision.get("strategy") or "manual"
        if mode in {"manual_open", "manual_close", "opening_prediction"}:
            return "manual"
        return mode

    def _extract_audit(self, decision: dict | None) -> dict:
        audit = (decision or {}).get("audit") or {}
        return audit if isinstance(audit, dict) else {}

    def _extract_audit_text(self, decision: dict | None, key: str) -> str | None:
        value = self._extract_audit(decision).get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _extract_audit_float(self, decision: dict | None, key: str) -> float | None:
        value = self._extract_audit(decision).get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _amount_semantics(
        self,
        *,
        side: str,
        status: str,
        amount,
        gross_amount=None,
        total_fee=None,
        cash_delta=None,
        realized_pnl=None,
    ) -> dict:
        normalized_side = str(side or "").lower()
        normalized_status = str(status or "").lower()
        amount_value = round(float(amount), 2) if amount is not None else None
        gross_value = round(float(gross_amount), 2) if gross_amount is not None else None
        fee_value = round(float(total_fee), 2) if total_fee is not None else None
        realized_value = round(float(realized_pnl), 2) if realized_pnl is not None else None
        if cash_delta is not None:
            cash_delta_value = round(float(cash_delta), 2)
        elif amount_value is None or normalized_status != "filled":
            cash_delta_value = None
        elif normalized_side == "buy":
            cash_delta_value = -amount_value
        elif normalized_side == "sell":
            cash_delta_value = amount_value
        else:
            cash_delta_value = None

        return {
            "net_cash_amount": amount_value,
            "cash_delta": cash_delta_value,
            "amount_semantics": {
                "amount": "净现金成交额：买入=毛成交额+费用，卖出=毛成交额-费用；不要当作毛成交额使用",
                "net_cash_amount": "amount 的明确别名，表示本笔订单对现金口径的成交额",
                "gross_amount": "毛成交额：实际成交价 price × 数量 quantity，不含佣金/印花税/过户费",
                "total_fee": "本笔订单总费用；买入计入成本，卖出从回款中扣除",
                "cash_delta": "现金账户变动：买入为负，卖出为正",
                "realized_pnl": "已实现盈亏：仅卖出/平仓订单有值，按卖出净回款 - 对应持仓成本计算",
            },
            "amount_formula": {
                "side": normalized_side,
                "amount": amount_value,
                "net_cash_amount": amount_value,
                "gross_amount": gross_value,
                "total_fee": fee_value,
                "cash_delta": cash_delta_value,
                "realized_pnl": realized_value,
                "formula": "amount = gross_amount + total_fee; cash_delta = -amount" if normalized_side == "buy" else "amount = gross_amount - total_fee; cash_delta = amount" if normalized_side == "sell" else None,
            },
        }

    def _amount_semantics_meta(self) -> dict:
        return {
            "amount": "净现金成交额，不是毛成交额；买入含费用成本，卖出为扣费后回款",
            "net_cash_amount": "amount 的明确别名，用于前端避免误把 amount 当毛成交额",
            "gross_amount": "毛成交额，等于实际成交价 price × quantity，不含费用",
            "total_fee": "commission + stamp_duty + transfer_fee",
            "realized_pnl": "已实现盈亏，仅卖出/平仓订单有值；口径为卖出净回款 - 对应持仓成本",
            "total_turnover": "当前统计沿用卖出订单 amount 汇总，表示卖出净回款汇总，不是毛成交额汇总",
            "buy_amount": "已成交买入订单 amount 汇总，表示含费用买入成本",
            "sell_amount": "已成交卖出订单 amount 汇总，表示扣费后卖出回款",
            "net_cash_flow": "sell_amount - buy_amount，表示当日交易净现金流",
        }

    def _order_to_dict(self, row: PaperOrderModel) -> dict:
        item = {
            "id": row.id,
            "order_id": row.order_id,
            "symbol": row.symbol,
            "side": row.side,
            "quantity": row.quantity,
            "price": row.price,
            "amount": row.amount,
            "status": row.status,
            "reason": row.reason,
            "strategy_mode": row.strategy_mode,
            "decision_json": row.decision_json,
            "source_type": getattr(row, "source_type", None),
            "source_id": getattr(row, "source_id", None),
            "source_action": getattr(row, "source_action", None),
            "source_confidence": round(float(row.source_confidence), 4) if getattr(row, "source_confidence", None) is not None else None,
            "source_memo": getattr(row, "source_memo", None),
            "audit_json": getattr(row, "audit_json", None),
            "requested_price": round(float(row.requested_price), 4) if row.requested_price is not None else None,
            "gross_amount": round(float(row.gross_amount), 2) if row.gross_amount is not None else None,
            "commission": round(float(row.commission), 2) if row.commission is not None else None,
            "stamp_duty": round(float(row.stamp_duty), 2) if row.stamp_duty is not None else None,
            "transfer_fee": round(float(row.transfer_fee), 2) if row.transfer_fee is not None else None,
            "total_fee": round(float(row.total_fee), 2) if row.total_fee is not None else None,
            "realized_pnl": round(float(row.realized_pnl), 2) if row.realized_pnl is not None else None,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "created_by": row.created_by,
            "updated_by": row.updated_by,
        }
        item.update(
            self._amount_semantics(
                side=item["side"],
                status=item["status"],
                amount=item.get("amount"),
                gross_amount=item.get("gross_amount"),
                total_fee=item.get("total_fee"),
                realized_pnl=item.get("realized_pnl"),
            )
        )
        if item.get("decision_json"):
            try:
                item["decision"] = json.loads(item["decision_json"])
            except Exception:
                item["decision"] = None
        if item.get("audit_json"):
            try:
                item["audit"] = json.loads(item["audit_json"])
            except Exception:
                item["audit"] = None
        return item

    def _cash_flow_to_dict(self, row: PaperCashFlowModel) -> dict:
        return {
            "id": row.id,
            "order_id": row.order_id,
            "symbol": row.symbol,
            "side": row.side,
            "amount": row.amount,
            "cash_after": row.cash_after,
            "note": row.note,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "created_by": row.created_by,
            "updated_by": row.updated_by,
        }

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper().split(".")[0]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
