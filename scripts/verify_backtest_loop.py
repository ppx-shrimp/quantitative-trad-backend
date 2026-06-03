from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from quant_system.services.backtest_service import BacktestService


def main() -> None:
    service = BacktestService.__new__(BacktestService)
    rows = _sample_rows()
    result = service._run_single_symbol(
        symbol="TEST01",
        rows=rows,
        period="daily",
        rule=service._get_strategy_rule("loose"),
        initial_cash=100_000,
        cash=100_000,
        quantity=100,
    )
    assert result["status"] == "ok", result
    assert result["rows_count"] == len(rows), result
    assert result["summary"]["trade_count"] >= 2, result
    assert result["summary"]["round_trip_count"] >= 1, result
    assert result["summary"]["total_fees"] > 0, result
    assert result["summary"]["final_equity"] > 0, result
    assert result["trades"][0]["side"] == "buy", result["trades"]
    assert any(trade["side"] == "sell" for trade in result["trades"]), result["trades"]
    assert all("total_fee" in trade for trade in result["trades"] if trade.get("accepted")), result["trades"]
    assert result["execution_rules"]["slippage_bps"] >= 0, result
    print("backtest loop ok")


def _sample_rows() -> list[dict]:
    rows = []
    price = 40.0
    for i in range(140):
        if i < 70:
            price += 0.05
        elif i < 95:
            price += 0.55
        elif i < 115:
            price -= 0.75
        else:
            price += 0.03
        rows.append({
            "trade_time": f"2024-01-{(i % 28) + 1:02d}T00:00:00" if i < 28 else f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}T00:00:00",
            "open": round(price * 0.995, 2),
            "high": round(price * 1.02, 2),
            "low": round(price * 0.98, 2),
            "close": round(price, 2),
            "volume": 100000 + i * 1000,
            "amount": round(price * (100000 + i * 1000), 2),
        })
    return rows


if __name__ == "__main__":
    main()
