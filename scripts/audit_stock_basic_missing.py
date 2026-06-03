from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import StockBasicModel


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper().split(".")[0]
    return symbol if symbol.isdigit() and len(symbol) == 6 else ""


def _exchange_for_symbol(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "SH"
    if symbol.startswith(("0", "2", "3")):
        return "SZ"
    if symbol.startswith(("4", "8")):
        return "BJ"
    return ""


def _market_for_symbol(symbol: str) -> str:
    if symbol.startswith("688"):
        return "科创板"
    if symbol.startswith("300"):
        return "创业板"
    if symbol.startswith(("4", "8")):
        return "北交所"
    if symbol.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "主板"
    return "-"


def _row(symbol: str, name: str, source: str) -> dict:
    exchange = _exchange_for_symbol(symbol)
    return {
        "symbol": symbol,
        "code": symbol,
        "ts_code": f"{symbol}.{exchange}" if exchange else symbol,
        "name": name.strip(),
        "exchange": exchange,
        "market": _market_for_symbol(symbol),
        "area": "",
        "industry": "",
        "list_date": "",
        "is_active": True,
        "source": source,
    }


def _append_akshare_df(rows_by_symbol: dict[str, dict], df, source: str) -> None:
    for raw in df.astype(object).where(df.notna(), None).to_dict(orient="records"):
        symbol = _normalize_symbol(
            raw.get("code")
            or raw.get("代码")
            or raw.get("symbol")
            or raw.get("证券代码")
            or raw.get("A股代码")
        )
        name = str(
            raw.get("name")
            or raw.get("名称")
            or raw.get("证券简称")
            or raw.get("A股简称")
            or raw.get("股票简称")
            or ""
        ).strip()
        if symbol and name:
            rows_by_symbol[symbol] = _row(symbol, name, source)


def _call_with_retry(fn, label: str, retries: int = 3):
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:  # AkShare wraps requests errors inconsistently across endpoints.
            last_error = exc
            if attempt < retries:
                wait_seconds = attempt * 1.5
                print(f"{label} 拉取失败，第 {attempt}/{retries} 次：{exc}；{wait_seconds:.1f}s 后重试...")
                time.sleep(wait_seconds)
    raise RuntimeError(f"{label} 拉取失败，已重试 {retries} 次：{last_error}") from last_error


def fetch_akshare_reference_rows() -> list[dict]:
    """Fetch an A-share code/name reference list from AkShare.

    AkShare's aggregate stock_info_a_code_name may fail when the SSE endpoint has
    transient SSL issues. This function first tries that aggregate endpoint, then
    falls back to exchange-specific endpoints so one failed source does not abort
    the whole audit.
    """
    import akshare as ak

    rows_by_symbol: dict[str, dict] = {}
    errors: list[str] = []

    try:
        df = _call_with_retry(ak.stock_info_a_code_name, "AkShare A股代码总表")
        _append_akshare_df(rows_by_symbol, df, "akshare_stock_info_a_code_name")
    except Exception as exc:
        errors.append(str(exc))
        print(f"AkShare A股代码总表不可用，切换到分交易所接口：{exc}")

    fallback_calls = [
        ("上交所主板A股", lambda: ak.stock_info_sh_name_code(symbol="主板A股"), "akshare_stock_info_sh_name_code"),
        ("上交所科创板", lambda: ak.stock_info_sh_name_code(symbol="科创板"), "akshare_stock_info_sh_name_code"),
        ("深交所A股列表", ak.stock_info_sz_name_code, "akshare_stock_info_sz_name_code"),
        ("北交所列表", ak.stock_info_bj_name_code, "akshare_stock_info_bj_name_code"),
    ]
    for label, fn, source in fallback_calls:
        try:
            df = _call_with_retry(fn, label)
            before = len(rows_by_symbol)
            _append_akshare_df(rows_by_symbol, df, source)
            print(f"{label} 拉取成功，新增/覆盖 {len(rows_by_symbol) - before} 条，累计 {len(rows_by_symbol)} 条。")
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            print(f"{label} 拉取失败，跳过：{exc}")

    if not rows_by_symbol:
        raise RuntimeError("未能拉取任何 AkShare A 股参考数据：" + "; ".join(errors))

    return sorted(rows_by_symbol.values(), key=lambda item: item["symbol"])


def load_db_symbols() -> set[str]:
    init_sqlalchemy_tables()
    with SessionLocal() as session:
        rows = session.execute(select(StockBasicModel.symbol)).all()
    return {_normalize_symbol(row[0]) for row in rows if _normalize_symbol(row[0])}


def upsert_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    with SessionLocal() as session:
        for row in rows:
            item = session.scalar(select(StockBasicModel).where(StockBasicModel.ts_code == row["ts_code"]))
            if item is None:
                item = StockBasicModel(
                    ts_code=row["ts_code"],
                    symbol=row["symbol"],
                    name=row["name"],
                    created_at=now,
                    updated_at=now,
                    created_by="audit_stock_basic_missing",
                    updated_by="audit_stock_basic_missing",
                )
                session.add(item)
            item.symbol = row["symbol"]
            item.name = row["name"]
            item.area = row["area"]
            item.industry = row["industry"]
            item.market = row["market"]
            item.exchange = row["exchange"]
            item.list_date = row["list_date"]
            item.is_active = bool(row["is_active"])
            item.source = row["source"]
            item.updated_at = now
            item.updated_by = "audit_stock_basic_missing"
            count += 1
        session.commit()
    return count


def write_csv(rows: list[dict], output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["ts_code", "symbol", "name", "exchange", "market", "source"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser(description="对比 stock_basic 与 AkShare A 股基础代码表，列出缺失股票。")
    parser.add_argument("--output", default="data/missing_stock_basic.csv", help="缺失股票 CSV 输出路径")
    parser.add_argument("--upsert", action="store_true", help="将缺失股票补写入 stock_basic")
    args = parser.parse_args()

    db_symbols = load_db_symbols()
    reference_rows = fetch_akshare_reference_rows()
    missing_rows = [row for row in reference_rows if row["symbol"] not in db_symbols]

    write_csv(missing_rows, args.output)

    print(f"stock_basic current symbols: {len(db_symbols)}")
    print(f"akshare reference symbols: {len(reference_rows)}")
    print(f"missing symbols: {len(missing_rows)}")
    print(f"missing csv: {Path(args.output).resolve()}")

    for row in missing_rows[:200]:
        print(f"{row['ts_code']}\t{row['name']}\t{row['market']}")
    if len(missing_rows) > 200:
        print(f"... omitted {len(missing_rows) - 200} rows; see CSV for full list")

    if args.upsert:
        inserted = upsert_rows(missing_rows)
        print(f"upserted missing rows: {inserted}")


if __name__ == "__main__":
    main()
