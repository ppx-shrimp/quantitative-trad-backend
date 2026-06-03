from __future__ import annotations

from pathlib import Path
import os
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


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


class FakeKlineService:
    def list_klines(self, symbol: str, period: str = "daily", limit: int = 120) -> list[dict]:
        return _sample_rows()


class FakeStockPoolService:
    def list_members(self, pool_code: str) -> list[dict]:
        return [{"symbol": "TEST01", "name": "测试股票"}]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "backtest_persistence.db"
        os.environ["QUANT_DATABASE_BACKEND"] = "sqlite"
        os.environ["QUANT_DATABASE_PATH"] = str(db_path)
        os.environ.pop("QUANT_DATABASE_URL", None)

        from quant_system.db.database import init_sqlalchemy_tables
        from quant_system.services.backtest_service import BacktestService

        init_sqlalchemy_tables()
        service = BacktestService()
        service.kline_service = FakeKlineService()
        service.stock_pool_service = FakeStockPoolService()

        result = service.run_symbol_backtest(
            symbol="TEST01",
            period="daily",
            strategy_mode="loose",
            initial_cash=100_000,
            quantity=100,
        )
        assert result["status"] == "ok", result
        assert result.get("run_id"), result

        page = service.list_runs_page(page_params=type("PageParams", (), {"page": 1, "page_size": 20, "offset": 0, "limit": 20})())
        data = page.to_dict()
        assert data["total"] >= 1, data
        assert data["items"][0]["run_id"] == result["run_id"], data

        detail = service.get_run_detail(result["run_id"], include_equity=True)
        assert detail["run_id"] == result["run_id"], detail
        assert detail["symbol"] == "TEST01", detail
        assert detail["summary"]["trade_count"] >= 2, detail
        assert detail["trades"]["total"] >= 2, detail
        assert len(detail["trades"]["items"]) >= 2, detail
        assert detail["equity_curve"]["total"] > 0, detail
        assert len(detail["equity_curve"]["items"]) > 0, detail
        print("backtest persistence ok")


if __name__ == "__main__":
    main()
