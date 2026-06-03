from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _read_report(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _classify_failure(message: str, provider_errors: list[str] | None = None) -> str:
    text = " ".join([message or "", *(provider_errors or [])]).lower()
    if "proxyerror" in text:
        return "network_proxy"
    if any(token in text for token in ["10013", "failed to establish a new connection", "unable to connect"]):
        return "network_connect"
    if any(token in text for token in ["timeout", "timed out"]):
        return "network_timeout"
    if any(token in text for token in ["remote disconnected", "remotedisconnected", "connection aborted", "max retries exceeded"]):
        return "network_remote_disconnect"
    if any(token in text for token in ["空数据", "未返回", "no data", "return empty", "返回空"]):
        return "empty_data"
    if any(token in text for token in ["不支持", "unsupported", "valueerror", "参数"]):
        return "parameter"
    return "other"


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总多份日线同步报告中的 failed symbols")
    parser.add_argument("--reports-dir", default="data/reports", help="报告目录")
    parser.add_argument("--pattern", default="all_daily_kline_sync_report*.json", help="报告文件匹配模式")
    parser.add_argument("--output", default="data/reports/all_daily_failed_symbols.json", help="汇总输出文件")
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    failed_map: dict[str, dict] = {}
    source_reports: list[str] = []
    category_counts: Counter[str] = Counter()

    for report_path in sorted(reports_dir.glob(args.pattern)):
        report = _read_report(report_path)
        if not isinstance(report, dict):
            continue
        failed_items = report.get("failed_items")
        if not isinstance(failed_items, list):
            continue
        source_reports.append(str(report_path))
        for item in failed_items:
            symbol = str(item.get("symbol") or "").strip()
            if not symbol:
                continue
            message = str(item.get("message") or "")
            provider_errors = item.get("provider_errors")
            provider_errors = provider_errors if isinstance(provider_errors, list) else []
            category = _classify_failure(message, provider_errors)
            failed_map[symbol] = {
                "symbol": symbol,
                "category": category,
                "message": message,
                "provider_errors": provider_errors,
                "source_report": str(report_path),
            }

    for item in failed_map.values():
        category_counts[item["category"]] += 1

    by_category: dict[str, list[str]] = {}
    for category in sorted(category_counts.keys()):
        by_category[category] = sorted(item["symbol"] for item in failed_map.values() if item["category"] == category)

    payload = {
        "reports_dir": str(reports_dir),
        "pattern": args.pattern,
        "failed_count": len(failed_map),
        "failed_symbols": sorted(failed_map.keys()),
        "category_counts": dict(category_counts),
        "by_category": by_category,
        "failed_items": list(sorted(failed_map.values(), key=lambda item: (item["category"], item["symbol"]))),
        "source_reports": sorted(set(source_reports)),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
