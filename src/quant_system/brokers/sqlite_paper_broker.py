from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_system.brokers.execution import build_trade_execution
from quant_system.core.config import settings
from quant_system.domain.models import Position


class SQLitePaperBroker:
    """基于 SQLite 的模拟券商。

    保存模拟账户现金、当前持仓、成交订单和资金流水。仍然只做模拟交易，不连接真实券商。
    """

    def __init__(self, initial_cash: float, database_path: str | None = None) -> None:
        self.database_path = Path(database_path or settings.database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initial_cash = initial_cash
        self.initialize()

    @property
    def cash(self) -> float:
        account = self._get_account()
        return float(account["cash"])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    initial_cash REAL NOT NULL,
                    cash REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity INTEGER NOT NULL,
                    avg_price REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    strategy_mode TEXT,
                    decision_json TEXT,
                    requested_price REAL,
                    gross_amount REAL,
                    commission REAL,
                    stamp_duty REAL,
                    transfer_fee REAL,
                    total_fee REAL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol_created ON paper_orders(symbol, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_cash_flows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT,
                    symbol TEXT,
                    side TEXT NOT NULL,
                    amount REAL NOT NULL,
                    cash_after REAL NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._ensure_order_columns(conn)
            now = self._now()
            conn.execute(
                """
                INSERT OR IGNORE INTO paper_accounts (account_id, initial_cash, cash, realized_pnl, created_at, updated_at)
                VALUES ('default', ?, ?, 0, ?, ?)
                """,
                (self.initial_cash, self.initial_cash, now, now),
            )

    def buy(self, symbol: str, quantity: int, price: float, decision: dict | None = None) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        execution = build_trade_execution("buy", quantity, price)
        cost = execution.amount
        now = self._now()
        with self._connect() as conn:
            account = self._get_account(conn)
            cash = float(account["cash"])
            if cost > cash:
                order = self._build_order_result(False, "buy", normalized_symbol, quantity, execution.price, cash, "模拟账户现金不足", execution)
                self._insert_order(conn, order, decision=decision, status="rejected")
                return order

            current = conn.execute("SELECT * FROM paper_positions WHERE symbol = ?", (normalized_symbol,)).fetchone()
            if current:
                total_quantity = int(current["quantity"]) + quantity
                avg_price = ((float(current["avg_price"]) * int(current["quantity"])) + cost) / total_quantity
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET quantity = ?, avg_price = ?, updated_at = ?
                    WHERE symbol = ?
                    """,
                    (total_quantity, avg_price, now, normalized_symbol),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO paper_positions (symbol, quantity, avg_price, opened_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (normalized_symbol, quantity, cost / quantity, now, now),
                )

            cash_after = round(cash - cost, 2)
            conn.execute(
                """
                UPDATE paper_accounts
                SET cash = ?, updated_at = ?
                WHERE account_id = 'default'
                """,
                (cash_after, now),
            )
            order = self._build_order_result(True, "buy", normalized_symbol, quantity, execution.price, cash_after, None, execution)
            self._insert_order(conn, order, decision=decision, status="filled")
            self._insert_cash_flow(conn, order, amount=execution.cash_delta, cash_after=cash_after, note="模拟买入（含滑点和交易费用）")
            return order

    def sell(self, symbol: str, quantity: int | None, price: float, decision: dict | None = None) -> dict:
        normalized_symbol = self._normalize_symbol(symbol)
        now = self._now()
        with self._connect() as conn:
            current = conn.execute("SELECT * FROM paper_positions WHERE symbol = ?", (normalized_symbol,)).fetchone()
            cash = float(self._get_account(conn)["cash"])
            if not current:
                order = self._build_order_result(False, "sell", normalized_symbol, quantity or 0, price, cash, "没有可平仓持仓")
                self._insert_order(conn, order, decision=decision, status="rejected")
                return order

            current_quantity = int(current["quantity"])
            sell_quantity = quantity or current_quantity
            if sell_quantity > current_quantity:
                order = self._build_order_result(False, "sell", normalized_symbol, sell_quantity, price, cash, "平仓数量超过持仓数量")
                self._insert_order(conn, order, decision=decision, status="rejected")
                return order

            execution = build_trade_execution("sell", sell_quantity, price)
            amount = execution.amount
            cash_after = round(cash + amount, 2)
            realized_pnl = amount - (float(current["avg_price"]) * sell_quantity)
            remain_quantity = current_quantity - sell_quantity
            if remain_quantity == 0:
                conn.execute("DELETE FROM paper_positions WHERE symbol = ?", (normalized_symbol,))
            else:
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET quantity = ?, updated_at = ?
                    WHERE symbol = ?
                    """,
                    (remain_quantity, now, normalized_symbol),
                )
            conn.execute(
                """
                UPDATE paper_accounts
                SET cash = ?, realized_pnl = realized_pnl + ?, updated_at = ?
                WHERE account_id = 'default'
                """,
                (cash_after, realized_pnl, now),
            )
            order = self._build_order_result(True, "sell", normalized_symbol, sell_quantity, execution.price, cash_after, None, execution)
            order["realized_pnl"] = round(realized_pnl, 2)
            self._insert_order(conn, order, decision=decision, status="filled")
            self._insert_cash_flow(conn, order, amount=execution.cash_delta, cash_after=cash_after, note="模拟卖出（含滑点和交易费用）")
            return order

    def get_position(self, symbol: str) -> Position | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM paper_positions WHERE symbol = ?", (self._normalize_symbol(symbol),)).fetchone()
            if row is None:
                return None
            return Position(symbol=row["symbol"], quantity=int(row["quantity"]), avg_price=float(row["avg_price"]), opened_at=datetime.fromisoformat(row["opened_at"]))

    def has_position(self, symbol: str) -> bool:
        return self.get_position(symbol) is not None

    def position_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM paper_positions").fetchone()
            return int(row["count"])

    def list_positions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, quantity, avg_price, opened_at, updated_at
                FROM paper_positions
                ORDER BY opened_at ASC
                """
            ).fetchall()
            return [
                {
                    "symbol": row["symbol"],
                    "quantity": int(row["quantity"]),
                    "avg_price": round(float(row["avg_price"]), 2),
                    "opened_at": row["opened_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]

    def list_orders(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            if symbol:
                rows = conn.execute(
                    """
                    SELECT * FROM paper_orders
                    WHERE symbol = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (self._normalize_symbol(symbol), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM paper_orders
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def list_cash_flows(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM paper_cash_flows
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def account_summary(self) -> dict:
        account = self._get_account()
        positions = self.list_positions()
        return {
            "account_id": account["account_id"],
            "initial_cash": float(account["initial_cash"]),
            "cash": float(account["cash"]),
            "realized_pnl": round(float(account["realized_pnl"]), 2),
            "position_count": len(positions),
            "positions": positions,
            "updated_at": account["updated_at"],
        }

    def reset(self) -> dict:
        now = self._now()
        with self._connect() as conn:
            conn.execute("DELETE FROM paper_positions")
            conn.execute("DELETE FROM paper_orders")
            conn.execute("DELETE FROM paper_cash_flows")
            conn.execute(
                """
                UPDATE paper_accounts
                SET cash = ?, realized_pnl = 0, updated_at = ?
                WHERE account_id = 'default'
                """,
                (self.initial_cash, now),
            )
        return self.account_summary()

    def _get_account(self, conn: sqlite3.Connection | None = None) -> sqlite3.Row:
        if conn is not None:
            return conn.execute("SELECT * FROM paper_accounts WHERE account_id = 'default'").fetchone()
        with self._connect() as inner_conn:
            return inner_conn.execute("SELECT * FROM paper_accounts WHERE account_id = 'default'").fetchone()

    def _ensure_order_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(paper_orders)").fetchall()}
        migrations = {
            "requested_price": "ALTER TABLE paper_orders ADD COLUMN requested_price REAL",
            "gross_amount": "ALTER TABLE paper_orders ADD COLUMN gross_amount REAL",
            "commission": "ALTER TABLE paper_orders ADD COLUMN commission REAL",
            "stamp_duty": "ALTER TABLE paper_orders ADD COLUMN stamp_duty REAL",
            "transfer_fee": "ALTER TABLE paper_orders ADD COLUMN transfer_fee REAL",
            "total_fee": "ALTER TABLE paper_orders ADD COLUMN total_fee REAL",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)

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

    def _insert_order(self, conn: sqlite3.Connection, order: dict, decision: dict | None, status: str) -> None:
        conn.execute(
            """
            INSERT INTO paper_orders (
                order_id, symbol, side, quantity, price, amount, status, reason, strategy_mode, decision_json,
                requested_price, gross_amount, commission, stamp_duty, transfer_fee, total_fee, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order["order_id"],
                order["symbol"],
                order["side"],
                order["quantity"],
                order["price"],
                order["amount"],
                status,
                order.get("reason"),
                self._extract_strategy_mode(decision),
                json.dumps(decision or {}, ensure_ascii=False, default=str),
                order.get("requested_price"),
                order.get("gross_amount"),
                order.get("commission"),
                order.get("stamp_duty"),
                order.get("transfer_fee"),
                order.get("total_fee"),
                self._now(),
            ),
        )

    def _insert_cash_flow(self, conn: sqlite3.Connection, order: dict, amount: float, cash_after: float, note: str) -> None:
        conn.execute(
            """
            INSERT INTO paper_cash_flows (order_id, symbol, side, amount, cash_after, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order["order_id"], order["symbol"], order["side"], amount, cash_after, note, self._now()),
        )

    def _extract_strategy_mode(self, decision: dict | None) -> str | None:
        if not decision:
            return None
        rule_snapshot = decision.get("rule_snapshot") or {}
        return rule_snapshot.get("strategy_mode")

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item.update(
            self._amount_semantics(
                side=item.get("side"),
                status=item.get("status"),
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
        return item

    def _amount_semantics(
        self,
        *,
        side: str | None,
        status: str | None,
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

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.strip().upper().split(".")[0]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
