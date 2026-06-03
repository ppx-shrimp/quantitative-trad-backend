from __future__ import annotations

import argparse

from quant_system.services.feature_service import FeatureService


def main() -> None:
    parser = argparse.ArgumentParser(description="计算股票池基础特征并保存到当前数据库")
    parser.add_argument("--pool", default="favorites", help="股票池 code，例如 favorites/candidates/watchlist")
    parser.add_argument("--period", default="daily", help="周期，例如 daily")
    parser.add_argument("--limit", type=int, default=None, help="限制计算股票数量，便于先小批量测试")
    args = parser.parse_args()

    service = FeatureService()
    result = service.compute_pool_features(pool_code=args.pool, period=args.period, limit_symbols=args.limit)
    print(result)


if __name__ == "__main__":
    main()
