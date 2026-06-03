from __future__ import annotations

import argparse
import json

from quant_system.services.trading_service import TradingService


def main() -> None:
    parser = argparse.ArgumentParser(description="手动测试自动模拟买入和平仓策略")
    parser.add_argument("--mode", choices=["strict", "normal", "loose"], default="strict", help="策略模式")
    parser.add_argument("--action", choices=["buy", "close", "both"], default="buy", help="执行动作")
    parser.add_argument("--close-mode", choices=["risk_scan", "force_close_all"], default="risk_scan", help="平仓模式：risk_scan=风控扫描；force_close_all=强制清仓")
    parser.add_argument("--dry-run", action="store_true", help="平仓预演：只展示将要卖出的持仓，不实际下单")
    parser.add_argument("--pools", default="favorites,candidates", help="逗号分隔的股票池 code")
    parser.add_argument("--limit", type=int, default=10, help="限制扫描股票数量")
    args = parser.parse_args()

    service = TradingService()
    pools = [item.strip() for item in args.pools.split(",") if item.strip()]

    if args.action in {"buy", "both"}:
        result = service.run_opening_auto_buy(pools=pools, limit_symbols=args.limit, strategy_mode=args.mode)
        print("auto_buy:")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    if args.action in {"close", "both"}:
        result = service.run_scheduled_auto_close(strategy_mode=args.mode, mode=args.close_mode, dry_run=args.dry_run)
        print("auto_close:")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
