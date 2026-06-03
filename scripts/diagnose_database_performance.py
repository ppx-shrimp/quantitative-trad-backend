from __future__ import annotations

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from quant_system.db.database import engine, get_database_url
from quant_system.db.models import (
    BacktestEquityModel,
    BacktestRunModel,
    BacktestTradeModel,
    KlineSyncLogModel,
    PaperCashFlowModel,
    PaperOrderModel,
    StockFeatureModel,
    StockKlineModel,
    TaskExecutionRecordModel,
)

EXPECTED_INDEXES: dict[str, set[str]] = {
    "paper_orders": {
        "ix_paper_orders_symbol_strategy_status_created",
        "ix_paper_orders_strategy_status_created",
    },
    "paper_cash_flows": {
        "ix_paper_cash_flows_created",
    },
    "task_execution_records": {
        "ix_task_execution_records_task_status_started",
        "ix_task_execution_records_type_status_started",
    },
    "backtest_runs": {
        "ix_backtest_runs_scope_status_strategy_created",
        "ix_backtest_runs_symbol_status_created",
        "ix_backtest_runs_pool_status_created",
    },
    "backtest_trades": {
        "ix_backtest_trades_run_trade_time",
    },
    "backtest_equity_curve": {
        "ix_backtest_equity_curve_run_trade_time",
    },
    "kline_sync_logs": {
        "ix_kline_sync_logs_symbol_period_created",
    },
}

TABLE_MODELS = {
    "stock_klines": StockKlineModel,
    "stock_features": StockFeatureModel,
    "paper_orders": PaperOrderModel,
    "paper_cash_flows": PaperCashFlowModel,
    "task_execution_records": TaskExecutionRecordModel,
    "backtest_runs": BacktestRunModel,
    "backtest_trades": BacktestTradeModel,
    "backtest_equity_curve": BacktestEquityModel,
    "kline_sync_logs": KlineSyncLogModel,
}


def main() -> None:
    print("数据库性能诊断")
    print({"database_url": _safe_database_url(), "dialect": engine.dialect.name})
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    print("\n表行数：")
    with engine.connect() as conn:
        for table_name, model in TABLE_MODELS.items():
            if table_name not in existing_tables:
                print(f"- {table_name}: missing")
                continue
            try:
                count = conn.scalar(select(text("count(*)")).select_from(model.__table__))
                print(f"- {table_name}: {int(count or 0)}")
            except SQLAlchemyError as exc:
                print(f"- {table_name}: error: {exc}")

    print("\n关键索引检查：")
    missing_total = 0
    for table_name, expected_indexes in EXPECTED_INDEXES.items():
        if table_name not in existing_tables:
            print(f"- {table_name}: table missing")
            missing_total += len(expected_indexes)
            continue
        actual_indexes = {item["name"] for item in inspector.get_indexes(table_name)}
        missing = sorted(expected_indexes - actual_indexes)
        if missing:
            missing_total += len(missing)
            print(f"- {table_name}: missing {missing}")
        else:
            print(f"- {table_name}: ok")

    print("\n高频查询编译检查：")
    for name, stmt in _sample_queries().items():
        try:
            compiled = stmt.compile(engine, compile_kwargs={"literal_binds": True})
            print(f"- {name}: ok")
            if engine.dialect.name == "mysql":
                with engine.connect() as conn:
                    plan = conn.execute(text(f"EXPLAIN {compiled}")).fetchall()
                    print(f"  explain_rows: {len(plan)}")
        except Exception as exc:
            print(f"- {name}: error: {exc}")

    if missing_total:
        print(f"\n诊断结果：发现 {missing_total} 个关键索引缺失，请先执行 alembic upgrade head。")
    else:
        print("\n诊断结果：关键索引齐全。")


def _safe_database_url() -> str:
    url = get_database_url()
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    _credentials, host = rest.split("@", 1)
    return f"{scheme}://***:***@{host}"


def _sample_queries() -> dict[str, object]:
    return {
        "kline_page": (
            select(StockKlineModel)
            .where(StockKlineModel.symbol == "000001", StockKlineModel.period == "daily")
            .order_by(StockKlineModel.trade_time.desc())
            .limit(50)
        ),
        "feature_page": (
            select(StockFeatureModel)
            .where(StockFeatureModel.symbol == "000001", StockFeatureModel.period == "daily")
            .order_by(StockFeatureModel.trade_time.desc())
            .limit(50)
        ),
        "orders_page": (
            select(PaperOrderModel)
            .where(PaperOrderModel.strategy_mode == "strict", PaperOrderModel.status == "filled")
            .order_by(PaperOrderModel.created_at.desc())
            .limit(50)
        ),
        "task_execution_page": (
            select(TaskExecutionRecordModel)
            .where(TaskExecutionRecordModel.task_name == "sync_pool_klines", TaskExecutionRecordModel.status == "success")
            .order_by(TaskExecutionRecordModel.started_at.desc())
            .limit(50)
        ),
        "backtest_runs_page": (
            select(BacktestRunModel)
            .where(BacktestRunModel.scope == "symbol", BacktestRunModel.status == "ok")
            .order_by(BacktestRunModel.created_at.desc(), BacktestRunModel.id.desc())
            .limit(50)
        ),
        "backtest_trades_detail": (
            select(BacktestTradeModel)
            .where(BacktestTradeModel.run_id == "bt-symbol-demo")
            .order_by(BacktestTradeModel.trade_time, BacktestTradeModel.id)
            .limit(200)
        ),
        "backtest_equity_detail": (
            select(BacktestEquityModel)
            .where(BacktestEquityModel.run_id == "bt-symbol-demo")
            .order_by(BacktestEquityModel.trade_time, BacktestEquityModel.id)
            .limit(200)
        ),
    }


if __name__ == "__main__":
    main()
