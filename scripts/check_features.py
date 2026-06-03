from __future__ import annotations

import argparse

from quant_system.services.feature_service import FeatureService


def main() -> None:
    parser = argparse.ArgumentParser(description="检查单只股票最新特征、分析和预测")
    parser.add_argument("--symbol", default="002709")
    parser.add_argument("--period", default="daily")
    args = parser.parse_args()

    service = FeatureService()
    latest = service.get_latest_feature(args.symbol, period=args.period)
    print("latest_feature:")
    print(latest)
    print("analysis:")
    print(service.analyze_symbol(args.symbol, period=args.period))
    print("prediction:")
    print(service.predict_symbol(args.symbol, period=args.period))


if __name__ == "__main__":
    main()
