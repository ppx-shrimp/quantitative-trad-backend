from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _add_run(service, run_id: str, symbol: str, mode: str, pnl_pct: float, drawdown: float, win_rate: float, trade_count: int) -> None:
    from quant_system.db.database import SessionLocal
    from quant_system.db.models import BacktestRunModel

    now = service._now()
    with SessionLocal() as session:
        session.add(BacktestRunModel(
            run_id=run_id,
            scope="symbol",
            symbol=symbol,
            pool_code=None,
            period="daily",
            strategy_mode=mode,
            status="ok",
            start_date="2024-01-01",
            end_date="2024-12-31",
            initial_cash=100000,
            quantity=100,
            rows_count=120,
            tested_bars=60,
            trade_count=trade_count,
            round_trip_count=max(0, trade_count // 2),
            total_pnl=100000 * pnl_pct / 100,
            total_pnl_pct=pnl_pct,
            final_equity=100000 * (1 + pnl_pct / 100),
            max_drawdown=drawdown,
            win_rate=win_rate,
            summary_json=service._to_json({"total_pnl_pct": pnl_pct, "max_drawdown": drawdown, "win_rate": win_rate, "trade_count": trade_count}),
            params_json=service._to_json({"symbol": symbol, "strategy_mode": mode}),
            rule_json=service._to_json({"mode": mode}),
            execution_rules_json=service._to_json(service._execution_rules()),
            created_at=now,
            updated_at=now,
            created_by="system",
            updated_by="system",
        ))
        session.commit()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "backtest_compare.db"
        os.environ["QUANT_DATABASE_BACKEND"] = "sqlite"
        os.environ["QUANT_DATABASE_PATH"] = str(db_path)
        os.environ.pop("QUANT_DATABASE_URL", None)

        from quant_system.db.database import init_sqlalchemy_tables
        from quant_system.services.backtest_service import BacktestService

        init_sqlalchemy_tables()
        service = BacktestService.__new__(BacktestService)

        _add_run(service, "run-low", "600001", "strict", pnl_pct=5.0, drawdown=2.0, win_rate=50.0, trade_count=6)
        _add_run(service, "run-best", "600001", "normal", pnl_pct=12.0, drawdown=3.0, win_rate=62.0, trade_count=10)
        _add_run(service, "run-risky", "600002", "loose", pnl_pct=14.0, drawdown=12.0, win_rate=55.0, trade_count=18)

        result = service.compare_runs(scope="symbol", sort_by="score", sort_order="desc", limit=10)
        assert result["count"] == 3, result
        assert result["best"]["run_id"] == "run-best", result
        assert result["items"][0]["score"] >= result["items"][1]["score"], result
        assert result["summary"]["best_run_id"] == "run-best", result

        by_pnl = service.compare_runs(scope="symbol", sort_by="total_pnl_pct", sort_order="desc", limit=1)
        assert by_pnl["best"]["run_id"] == "run-risky", by_pnl

        filtered = service.compare_runs(symbol="600001", sort_by="score", sort_order="desc", limit=10)
        assert filtered["count"] == 2, filtered
        assert {item["run_id"] for item in filtered["items"]} == {"run-low", "run-best"}, filtered
        print("backtest compare ok")


if __name__ == "__main__":
    main()
