from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import StockBasicModel, StockKlineModel
from quant_system.services.kline_service import KlineService


def _load_symbols(limit: int | None = None, start_from: str | None = None) -> list[str]:
    init_sqlalchemy_tables()
    with SessionLocal() as session:
        stmt = (
            select(StockBasicModel.symbol)
            .where(StockBasicModel.is_active == True)  # noqa: E712
            .order_by(StockBasicModel.symbol.asc())
        )
        symbols = [str(item) for item in session.scalars(stmt).all() if item]
    if start_from:
        symbols = [symbol for symbol in symbols if symbol >= start_from]
    if limit is not None:
        symbols = symbols[:limit]
    return symbols


def _slice_batch(symbols: list[str], batch_index: int, batch_size: int) -> list[str]:
    start = max(0, batch_index) * max(1, batch_size)
    end = start + max(1, batch_size)
    return symbols[start:end]


def _total_batches(total_symbols: int, batch_size: int) -> int:
    if total_symbols <= 0:
        return 0
    size = max(1, batch_size)
    return (total_symbols + size - 1) // size


def _has_more_batches(total_symbols: int, batch_index: int, batch_size: int) -> bool:
    return (max(0, batch_index) + 1) < _total_batches(total_symbols, batch_size)


def _write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _read_report(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_progress(path: Path) -> dict:
    data = _read_report(path)
    return data if isinstance(data, dict) else {}


def _write_progress(path: Path, payload: dict) -> None:
    _write_report(path, payload)


def _load_retry_symbols_from_report(path: Path) -> list[str]:
    report = _read_report(path)
    if not report:
        return []
    failed_symbols = report.get("failed_symbols")
    if isinstance(failed_symbols, list):
        return [str(item) for item in failed_symbols if item]
    return []


def _count_ready_daily_symbols(symbols: list[str], min_rows: int) -> set[str]:
    if not symbols:
        return set()
    with SessionLocal() as session:
        rows = session.execute(
            select(StockKlineModel.symbol, func.count(StockKlineModel.id).label("rows_count"))
            .where(
                StockKlineModel.symbol.in_(symbols),
                StockKlineModel.period == "daily",
            )
            .group_by(StockKlineModel.symbol)
        ).all()
    return {str(row.symbol) for row in rows if int(row.rows_count or 0) >= min_rows}


def _run_batch(
    service: KlineService,
    symbols: list[str],
    retry_failed: bool,
) -> tuple[list[dict], list[dict], list[dict]]:
    first_round_results: list[dict] = []
    for index, symbol in enumerate(symbols, start=1):
        result = service.sync_symbol_kline(symbol=symbol, period="daily", tracked=False)
        first_round_results.append(result)
        print(
            f"[round1] {index}/{len(symbols)} symbol={symbol} "
            f"status={result['status']} source={result.get('source')} rows={result.get('rows_count')}"
        )

    retry_results: list[dict] = []
    if retry_failed:
        failed_symbols = [item["symbol"] for item in first_round_results if item.get("status") == "failed"]
        print(f"retry failed symbols={len(failed_symbols)}")
        for index, symbol in enumerate(failed_symbols, start=1):
            result = service.sync_symbol_kline(symbol=symbol, period="daily", tracked=False)
            retry_results.append(result)
            print(
                f"[round2] {index}/{len(failed_symbols)} symbol={symbol} "
                f"status={result['status']} source={result.get('source')} rows={result.get('rows_count')}"
            )

    final_results_by_symbol = {item["symbol"]: item for item in first_round_results}
    for item in retry_results:
        final_results_by_symbol[item["symbol"]] = item
    final_results = list(final_results_by_symbol.values())
    return first_round_results, retry_results, final_results


def _build_report(
    *,
    all_symbols: list[str],
    batch_symbols: list[str],
    batch_index: int,
    batch_size: int,
    retry_failed: bool,
    skip_ready: bool,
    skipped_ready_symbols: list[str],
    final_results: list[dict],
) -> dict:
    status_counts = Counter(item.get("status") or "unknown" for item in final_results)
    source_counts = Counter(item.get("source") or "unknown" for item in final_results)
    failed_items = [item for item in final_results if item.get("status") == "failed"]
    total_symbols = len(all_symbols)
    total_batches = _total_batches(total_symbols, batch_size)
    has_more_batches = _has_more_batches(total_symbols, batch_index, batch_size)
    return {
        "generated_at": datetime.now().isoformat(),
        "batch_index": batch_index,
        "batch_size": batch_size,
        "total_available_symbols": total_symbols,
        "total_batches": total_batches,
        "total_symbols": len(batch_symbols),
        "batch_symbols": batch_symbols,
        "has_more_batches": has_more_batches,
        "completed": not has_more_batches,
        "retry_failed": retry_failed,
        "skip_ready": skip_ready,
        "skipped_ready_symbols": skipped_ready_symbols,
        "status_counts": dict(status_counts),
        "source_counts": dict(source_counts),
        "failed_count": len(failed_items),
        "failed_symbols": [item.get("symbol") for item in failed_items],
        "failed_items": [
            {
                "symbol": item.get("symbol"),
                "message": item.get("message"),
                "provider_errors": item.get("provider_errors"),
            }
            for item in failed_items
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="全市场日线 K 线分批同步")
    parser.add_argument("--limit", type=int, default=None, help="只同步前 N 只股票，便于小批量测试")
    parser.add_argument("--start-from", default=None, help="从某个 symbol 开始，例如 300001")
    parser.add_argument("--batch-size", type=int, default=100, help="每批同步多少只股票")
    parser.add_argument("--batch-index", type=int, default=0, help="从第几批开始，0 表示第一批")
    parser.add_argument("--rounds", type=int, default=1, help="连续执行多少批")
    parser.add_argument("--retry-failed", action="store_true", help="第二轮只重试首轮失败的股票")
    parser.add_argument("--retry-from-report", default=None, help="从历史报告 JSON 中读取 failed_symbols 进行重试")
    parser.add_argument("--skip-ready", action="store_true", help="跳过本地已经具备足够日线的股票")
    parser.add_argument("--min-rows", type=int, default=20, help="skip-ready 时本地最少行数阈值")
    parser.add_argument("--progress-file", default="data/reports/all_daily_kline_sync_progress.json", help="进度文件路径")
    parser.add_argument("--report", default="data/reports/all_daily_kline_sync_report.json", help="同步报告输出路径")
    args = parser.parse_args()

    progress_path = Path(args.progress_file)
    report_base = Path(args.report)
    service = KlineService()

    if args.retry_from_report:
        all_symbols = _load_retry_symbols_from_report(Path(args.retry_from_report))
    else:
        all_symbols = _load_symbols(limit=args.limit, start_from=args.start_from)

    progress = _read_progress(progress_path)
    current_batch_index = int(progress.get("next_batch_index", args.batch_index)) if progress else args.batch_index

    for round_index in range(max(1, args.rounds)):
        batch_symbols = _slice_batch(all_symbols, batch_index=current_batch_index, batch_size=args.batch_size)
        if not batch_symbols:
            print(f"no symbols for batch_index={current_batch_index}, stop")
            break

        skipped_ready_symbols: list[str] = []
        if args.skip_ready:
            ready_symbols = _count_ready_daily_symbols(batch_symbols, min_rows=args.min_rows)
            skipped_ready_symbols = sorted(ready_symbols)
            batch_symbols = [symbol for symbol in batch_symbols if symbol not in ready_symbols]

        print(
            f"loaded total_symbols={len(all_symbols)} "
            f"batch_index={current_batch_index} batch_size={args.batch_size} batch_symbols={len(batch_symbols)} "
            f"skipped_ready={len(skipped_ready_symbols)}"
        )

        if not batch_symbols:
            report = _build_report(
                all_symbols=all_symbols,
                batch_symbols=[],
                batch_index=current_batch_index,
                batch_size=args.batch_size,
                retry_failed=args.retry_failed,
                skip_ready=args.skip_ready,
                skipped_ready_symbols=skipped_ready_symbols,
                final_results=[],
            )
        else:
            _first_round, _retry_round, final_results = _run_batch(
                service=service,
                symbols=batch_symbols,
                retry_failed=args.retry_failed,
            )
            report = _build_report(
                all_symbols=all_symbols,
                batch_symbols=batch_symbols,
                batch_index=current_batch_index,
                batch_size=args.batch_size,
                retry_failed=args.retry_failed,
                skip_ready=args.skip_ready,
                skipped_ready_symbols=skipped_ready_symbols,
                final_results=final_results,
            )

        report_path = report_base
        if args.rounds > 1 or round_index > 0:
            report_path = report_base.with_name(
                f"{report_base.stem}_batch_{current_batch_index:03d}{report_base.suffix}"
            )
        _write_report(report_path, report)
        _write_progress(
            progress_path,
            {
                "updated_at": datetime.now().isoformat(),
                "last_batch_index": current_batch_index,
                "next_batch_index": current_batch_index + 1,
                "batch_size": args.batch_size,
                "report_path": str(report_path),
                "failed_count": report["failed_count"],
                "has_more_batches": report["has_more_batches"],
                "completed": report["completed"],
                "total_batches": report["total_batches"],
            },
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        current_batch_index += 1


if __name__ == "__main__":
    main()
