from __future__ import annotations

from quant_system.services.stock_pool_service import StockPoolService


def main() -> None:
    service = StockPoolService()
    pools = service.list_pools()
    print("pools:")
    for pool in pools:
        print(f"- {pool['code']} {pool['name']} count={pool['member_count']}")
    favorites = service.list_members("favorites")
    print(f"favorites_count={len(favorites)}")
    for item in favorites[:10]:
        print(item)


if __name__ == "__main__":
    main()
