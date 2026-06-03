from __future__ import annotations

import argparse

from quant_system.services.kline_service import KlineService


def main() -> None:
    parser = argparse.ArgumentParser(description="同步股票池 K 线到当前数据库")
    parser.add_argument("--pool", default="favorites", help="股票池 code，例如 favorites/candidates/watchlist")
    parser.add_argument("--periods", default="daily,minute", help="逗号分隔周期，例如 daily 或 daily,minute")
    parser.add_argument("--limit", type=int, default=None, help="限制同步股票数量，便于先小批量测试")
    parser.add_argument("--inspect-only", action="store_true", help="仅检查本地缺口，不执行外部同步")
    args = parser.parse_args()

    periods = [item.strip() for item in args.periods.split(",") if item.strip()]
    service = KlineService()
    if args.inspect_only:
        result = service.inspect_pool_klines(pool_code=args.pool, periods=periods, limit_symbols=args.limit)
    else:
        result = service.sync_pool_klines(pool_code=args.pool, periods=periods, limit_symbols=args.limit)
    print(result)


if __name__ == "__main__":
    main()
