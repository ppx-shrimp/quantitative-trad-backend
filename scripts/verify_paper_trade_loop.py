from __future__ import annotations

from pathlib import Path
import gc
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quant_system.brokers.sqlite_paper_broker import SQLitePaperBroker


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "paper_loop.db"
        broker = SQLitePaperBroker(initial_cash=100_000, database_path=str(db_path))

        buy = broker.buy("600519.SH", 100, 100.0, decision={"rule_snapshot": {"strategy_mode": "strict"}})
        assert buy["accepted"] is True, buy
        assert buy["price"] > 100.0, buy
        assert buy["amount"] > buy["gross_amount"], buy
        assert buy["total_fee"] > 0, buy

        positions = broker.list_positions()
        assert len(positions) == 1, positions
        assert positions[0]["quantity"] == 100, positions
        assert positions[0]["avg_price"] > 100.0, positions

        sell = broker.sell("600519.SH", None, 110.0, decision={"rule_snapshot": {"strategy_mode": "strict"}})
        assert sell["accepted"] is True, sell
        assert sell["price"] < 110.0, sell
        assert sell["amount"] < sell["gross_amount"], sell
        assert sell["total_fee"] > buy["total_fee"], sell
        assert sell["realized_pnl"] > 0, sell

        assert broker.list_positions() == []

        account = broker.account_summary()
        assert account["cash"] > 100_000, account
        assert account["realized_pnl"] == sell["realized_pnl"], account

        orders = broker.list_orders(limit=10)
        assert len(orders) == 2, orders
        assert all(order["status"] == "filled" for order in orders), orders
        assert all(order.get("requested_price") is not None for order in orders), orders
        assert all(order.get("total_fee") is not None for order in orders), orders

        flows = broker.list_cash_flows(limit=10)
        assert len(flows) == 2, flows
        assert flows[0]["amount"] > 0, flows
        assert flows[1]["amount"] < 0, flows

        del broker
        gc.collect()

    print("paper trade loop ok")


if __name__ == "__main__":
    main()
