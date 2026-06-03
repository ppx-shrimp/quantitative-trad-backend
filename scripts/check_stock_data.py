from __future__ import annotations

from quant_system.data.akshare_provider import AkShareMarketDataProvider


def main() -> None:
    provider = AkShareMarketDataProvider()
    rows = provider.get_stock_list()
    print(f"stock_count={len(rows)}")
    for row in rows[:10]:
        print(row)


if __name__ == "__main__":
    main()
