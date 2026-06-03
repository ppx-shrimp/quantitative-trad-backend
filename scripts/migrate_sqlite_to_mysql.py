"""SQLite -> MySQL 数据迁移脚本。

从本地 SQLite 文件读取历史数据，写入当前配置的 MySQL 数据库。
支持重复运行：已有记录会 upsert 跳过或覆盖，不会报主键冲突。

用法：
    # 先确认 .env 中 QUANT_DATABASE_URL 指向目标 MySQL
    python scripts/migrate_sqlite_to_mysql.py                        # 迁移全部表
    python scripts/migrate_sqlite_to_mysql.py --dry-run              # 仅预览，不写入
    python scripts/migrate_sqlite_to_mysql.py --tables stock_pools   # 只迁移指定表
    python scripts/migrate_sqlite_to_mysql.py --sqlite-path data/quant_system.db  # 指定 SQLite 路径
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from quant_system.core.config import settings
from quant_system.db.database import SessionLocal, engine as mysql_engine, init_sqlalchemy_tables
from quant_system.db.models import (
    KlineSyncLogModel,
    PaperAccountModel,
    PaperCashFlowModel,
    PaperOrderModel,
    PaperPositionModel,
    StockFeatureModel,
    StockKlineModel,
    StockPoolMemberModel,
    StockPoolModel,
)

# 迁移顺序：先主表，再子表
MIGRATION_TABLES = [
    "stock_pools",
    "stock_pool_members",
    "stock_klines",
    "kline_sync_logs",
    "stock_features",
    "paper_accounts",
    "paper_positions",
    "paper_orders",
    "paper_cash_flows",
]

# 表 -> (SQLite 读取列, ORM 模型, 主键列列表, upsert 时需要更新的列)
TABLE_CONFIG: dict[str, dict] = {}

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# SQLite 旧表可能没有审计字段，需要补默认值
def _audit_defaults() -> dict:
    now = _now_iso()
    return {
        "created_at": now,
        "updated_at": now,
        "created_by": "migration",
        "updated_by": "migration",
    }


def build_table_config() -> None:
    """构建每张表的迁移配置。"""
    global TABLE_CONFIG
    audit = _audit_defaults()
    TABLE_CONFIG = {
        "stock_pools": {
            "model": StockPoolModel,
            "pk": ["code"],
            "columns": ["code", "name", "description", "pool_type"],
            "defaults": audit,
        },
        "stock_pool_members": {
            "model": StockPoolMemberModel,
            "pk": ["pool_code", "symbol"],
            "columns": ["pool_code", "symbol", "name", "reason", "tags", "source", "enabled"],
            "defaults": {**audit, "source": "manual", "enabled": 1},
        },
        "stock_klines": {
            "model": StockKlineModel,
            "pk": ["symbol", "period", "trade_time"],
            "columns": ["symbol", "period", "trade_time", "open", "high", "low", "close",
                        "volume", "amount", "change_pct", "turnover_rate", "source"],
            "defaults": {**audit, "source": "akshare"},
        },
        "kline_sync_logs": {
            "model": KlineSyncLogModel,
            "pk": ["id"],
            "columns": ["id", "pool_code", "symbol", "period", "status", "rows_count", "message"],
            "defaults": audit,
        },
        "stock_features": {
            "model": StockFeatureModel,
            "pk": ["symbol", "period", "trade_time"],
            "columns": ["symbol", "period", "trade_time", "close", "ma5", "ma10", "ma20", "ma60",
                        "return_1", "return_5", "return_20", "volatility_20", "volume_ratio_5",
                        "price_position_20", "price_position_60", "trend_direction", "trend_score", "signal"],
            "defaults": audit,
        },
        "paper_accounts": {
            "model": PaperAccountModel,
            "pk": ["account_id"],
            "columns": ["account_id", "initial_cash", "cash", "realized_pnl"],
            "defaults": audit,
        },
        "paper_positions": {
            "model": PaperPositionModel,
            "pk": ["symbol"],
            "columns": ["symbol", "quantity", "avg_price", "opened_at"],
            "defaults": audit,
        },
        "paper_orders": {
            "model": PaperOrderModel,
            "pk": ["order_id"],
            "columns": ["order_id", "symbol", "side", "quantity", "price", "amount",
                        "status", "reason", "strategy_mode", "decision_json"],
            "defaults": audit,
        },
        "paper_cash_flows": {
            "model": PaperCashFlowModel,
            "pk": ["order_id"],
            "columns": ["order_id", "symbol", "side", "amount", "cash_after", "note"],
            "defaults": audit,
        },
    }


def detect_sqlite_path(cli_path: str | None) -> str:
    """确定 SQLite 文件路径：优先命令行参数，其次 settings.database_path。"""
    if cli_path:
        return cli_path
    db_path = Path(settings.database_path)
    if db_path.exists():
        return str(db_path)
    return ""


def create_sqlite_engine(path: str) -> Engine:
    """创建指向 SQLite 文件的 SQLAlchemy engine。"""
    abs_path = Path(path).resolve()
    url = f"sqlite:///{abs_path.as_posix()}"
    return create_engine(url, connect_args={"check_same_thread": False})


def read_sqlite_table(sqlite_eng: Engine, table_name: str) -> list[dict] | None:
    """从 SQLite 读取指定表的全部数据。表不存在时返回 None。"""
    with sqlite_eng.connect() as conn:
        exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND :name = name"),
            {"name": table_name},
        ).fetchone()
        if exists is None:
            return None
        result = conn.execute(text(f'SELECT * FROM "{table_name}"'))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]


def upsert_rows(table_name: str, rows: list[dict], dry_run: bool = False) -> int:
    """将数据写入目标数据库（MySQL），使用 dialect-aware upsert。

    返回实际写入/更新的行数。
    """
    if not rows:
        return 0

    config = TABLE_CONFIG[table_name]
    model = config["model"]
    pk_cols = config["pk"]
    all_columns = config["columns"]
    defaults = config.get("defaults", {})

    dialect = mysql_engine.dialect.name
    now = _now_iso()
    audit_defaults = {**_audit_defaults(), "created_at": now, "updated_at": now}

    count = 0
    with SessionLocal() as session:
        for raw_row in rows:
            # 构建写入值，补缺失列的默认值
            values: dict = {}
            for col in all_columns:
                val = raw_row.get(col)
                if val is None and col in defaults:
                    val = defaults[col]
                values[col] = val

            # 补审计字段
            for audit_col, default_val in audit_defaults.items():
                if audit_col not in values or values[audit_col] is None:
                    values[audit_col] = raw_row.get(audit_col, default_val)

            if dialect == "mysql":
                _mysql_upsert(session, model, values, all_columns, pk_cols)
            elif dialect == "sqlite":
                _sqlite_upsert(session, model, values, all_columns, pk_cols)
            else:
                _generic_upsert(session, model, values, all_columns, pk_cols)

            count += 1

        session.commit()

    return count


def _mysql_upsert(session, model, values: dict, columns: list[str], pk_cols: list[str]) -> None:
    """MySQL: INSERT ... ON DUPLICATE KEY UPDATE。"""
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    stmt = mysql_insert(model).values(**values)
    update_cols = {col: getattr(stmt.inserted, col) for col in columns if col not in pk_cols}
    # 保留原 created_at，不覆盖
    update_cols.pop("created_at", None)
    update_cols.pop("created_by", None)
    stmt = stmt.on_duplicate_key_update(**update_cols)
    session.execute(stmt)


def _sqlite_upsert(session, model, values: dict, columns: list[str], pk_cols: list[str]) -> None:
    """SQLite: INSERT ... ON CONFLICT DO UPDATE。"""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    stmt = sqlite_insert(model).values(**values)
    set_dict = {col: getattr(stmt.excluded, col) for col in columns if col not in pk_cols}
    set_dict.pop("created_at", None)
    set_dict.pop("created_by", None)
    stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=set_dict)
    session.execute(stmt)


def _generic_upsert(session, model, values: dict, columns: list[str], pk_cols: list[str]) -> None:
    """通用 fallback：查询后 insert 或 update。"""
    from sqlalchemy import select

    filters = [getattr(model, pk) == values[pk] for pk in pk_cols]
    existing = session.scalar(select(model).where(*filters))
    if existing is None:
        session.add(model(**values))
    else:
        for col in columns:
            if col not in ("created_at", "created_by"):
                setattr(existing, col, values[col])
        for audit_col in ("updated_at", "updated_by"):
            if audit_col in values:
                setattr(existing, audit_col, values[audit_col])


def migrate_table(sqlite_eng: Engine, table_name: str, dry_run: bool = False) -> dict:
    """迁移单张表，返回迁移报告。"""
    rows = read_sqlite_table(sqlite_eng, table_name)
    result = {
        "table": table_name,
        "source_rows": 0 if rows is None else len(rows),
        "migrated_rows": 0,
        "status": "skipped",
    }
    if rows is None:
        result["status"] = "not_found"
        return result
    if not rows:
        result["status"] = "empty"
        return result

    if dry_run:
        result["status"] = "dry_run"
        result["migrated_rows"] = len(rows)
        return result

    result["migrated_rows"] = upsert_rows(table_name, rows, dry_run=False)
    result["status"] = "ok"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SQLite -> MySQL 数据迁移：从本地 SQLite 文件读取历史数据，写入当前配置的目标数据库。"
    )
    parser.add_argument("--sqlite-path", default=None, help="SQLite 文件路径，默认读取 settings.database_path")
    parser.add_argument("--tables", default=None, help="逗号分隔的表名，默认迁移全部表")
    parser.add_argument("--dry-run", action="store_true", help="仅预览迁移行数，不实际写入")
    parser.add_argument("--skip-init", action="store_true", help="跳过目标表自动建表（假设表已存在）")
    args = parser.parse_args()

    # 确定 SQLite 路径
    sqlite_path = detect_sqlite_path(args.sqlite_path)
    if not sqlite_path or not Path(sqlite_path).exists():
        print(f"错误：找不到 SQLite 文件。请通过 --sqlite-path 指定，或确认 settings.database_path ({settings.database_path}) 存在。")
        sys.exit(1)

    # 确定目标数据库
    target_url = mysql_engine.url
    print(f"源数据库 (SQLite): {Path(sqlite_path).resolve()}")
    print(f"目标数据库: {target_url}")
    print(f"模式: {'预览 (dry-run)' if args.dry_run else '实际迁移'}")
    print()

    # 安全检查：禁止从 MySQL 迁移到 SQLite（方向反了）
    if str(target_url).startswith("sqlite"):
        print("错误：目标数据库仍然是 SQLite。请先在 .env 中配置 QUANT_DATABASE_URL 指向 MySQL。")
        sys.exit(1)

    # 初始化目标表
    if not args.skip_init:
        print("正在初始化目标数据库表结构...")
        init_sqlalchemy_tables()
        print("表结构初始化完成。")
        print()

    # 确定要迁移的表
    build_table_config()
    if args.tables:
        table_list = [t.strip() for t in args.tables.split(",") if t.strip()]
        invalid = [t for t in table_list if t not in MIGRATION_TABLES]
        if invalid:
            print(f"错误：未知表名 {invalid}。可选表：{MIGRATION_TABLES}")
            sys.exit(1)
    else:
        table_list = MIGRATION_TABLES

    # 创建 SQLite 引擎
    sqlite_eng = create_sqlite_engine(sqlite_path)

    # 逐表迁移
    results = []
    for table_name in table_list:
        print(f"迁移 {table_name} ...", end=" ")
        result = migrate_table(sqlite_eng, table_name, dry_run=args.dry_run)
        results.append(result)
        status_label = {
            "ok": f"{result['migrated_rows']} 行写入",
            "dry_run": f"{result['migrated_rows']} 行待迁移",
            "empty": "源表为空，跳过",
            "not_found": "源数据库无此表，跳过",
            "skipped": "跳过",
        }.get(result["status"], result["status"])
        print(status_label)

    # 汇总
    print()
    print("=" * 60)
    print(f"{'表名':<25} {'源数据行数':>10} {'迁移行数':>10} {'状态':>10}")
    print("-" * 60)
    total_source = 0
    total_migrated = 0
    for r in results:
        print(f"{r['table']:<25} {r['source_rows']:>10} {r['migrated_rows']:>10} {r['status']:>10}")
        total_source += r["source_rows"]
        total_migrated += r["migrated_rows"]
    print("-" * 60)
    print(f"{'合计':<25} {total_source:>10} {total_migrated:>10}")
    print("=" * 60)

    if args.dry_run:
        print()
        print("预览模式，未实际写入。去掉 --dry-run 执行实际迁移。")

    print()
    print("迁移完成。")


if __name__ == "__main__":
    main()
