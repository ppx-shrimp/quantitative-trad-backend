from __future__ import annotations

from typing import Protocol, runtime_checkable

from quant_system.api.pagination import PageParams, PageResult
from quant_system.domain.models import Position


@runtime_checkable
class TradingBroker(Protocol):
    """统一交易通道协议。

    当前实现可以是内存模拟、SQLite/MySQL 模拟账户；未来接券商仿真盘或实盘时，
    新通道也应实现这组方法，避免 TradingService 绑定具体 broker 类。
    """

    @property
    def cash(self) -> float:
        ...

    def buy(self, symbol: str, quantity: int, price: float, decision: dict | None = None) -> dict:
        ...

    def sell(self, symbol: str, quantity: int | None, price: float, decision: dict | None = None) -> dict:
        ...

    def get_position(self, symbol: str) -> Position | None:
        ...

    def has_position(self, symbol: str) -> bool:
        ...

    def position_count(self) -> int:
        ...

    def list_positions(self) -> list[dict]:
        ...


@runtime_checkable
class PersistentTradingBroker(TradingBroker, Protocol):
    def list_orders(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
        ...

    def list_cash_flows(self, limit: int = 100) -> list[dict]:
        ...

    def get_daily_buy_amount(self, date: str | None = None) -> float:
        ...

    def account_summary(self) -> dict:
        ...

    def reset(self) -> dict:
        ...


@runtime_checkable
class PagedTradingBroker(PersistentTradingBroker, Protocol):
    def list_orders_page(
        self,
        page_params: PageParams,
        symbol: str | None = None,
        side: str | None = None,
        status: str | None = None,
        strategy_mode: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> PageResult:
        ...

    def list_cash_flows_page(self, page_params: PageParams) -> PageResult:
        ...


@runtime_checkable
class ReportingTradingBroker(PagedTradingBroker, Protocol):
    def get_daily_report(self, date: str | None = None) -> dict:
        ...

    def get_pnl_stats(
        self,
        strategy_mode: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        ...

    def get_strategy_evaluation(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        ...
