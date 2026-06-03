from __future__ import annotations

from quant_system.services.kline_service import KlineService


def main() -> None:
    service = KlineService()
    summary = service.get_kline_summary()
    print(f"kline_symbol_period_count={len(summary)}")
    for item in summary[:20]:
        print(item)


if __name__ == "__main__":
    main()
