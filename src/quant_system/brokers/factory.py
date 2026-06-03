from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quant_system.brokers.base import TradingBroker
from quant_system.core.config import settings


@dataclass(frozen=True)
class BrokerCapabilities:
    backend: str
    gateway: str
    persistent: bool
    paged_queries: bool
    cash_flows: bool
    account_summary: bool
    daily_report: bool
    pnl_stats: bool
    strategy_evaluation: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "gateway": self.gateway,
            "persistent": self.persistent,
            "paged_queries": self.paged_queries,
            "cash_flows": self.cash_flows,
            "account_summary": self.account_summary,
            "daily_report": self.daily_report,
            "pnl_stats": self.pnl_stats,
            "strategy_evaluation": self.strategy_evaluation,
        }


def create_broker() -> TradingBroker:
    backend = _normalize_backend(settings.paper_broker_backend)
    if backend in {"sqlalchemy", "mysql"}:
        from quant_system.brokers.sqlalchemy_paper_broker import SQLAlchemyPaperBroker

        return SQLAlchemyPaperBroker(initial_cash=settings.default_cash)
    if backend == "sqlite":
        from quant_system.brokers.sqlite_paper_broker import SQLitePaperBroker

        return SQLitePaperBroker(initial_cash=settings.default_cash)
    if backend == "memory":
        from quant_system.brokers.paper_broker import PaperBroker

        return PaperBroker(initial_cash=settings.default_cash)

    raise ValueError(f"不支持的模拟交易通道：{settings.paper_broker_backend}")


def describe_broker(broker: TradingBroker | None = None) -> dict[str, Any]:
    backend = _normalize_backend(settings.paper_broker_backend)
    gateway = broker.__class__.__name__ if broker is not None else _gateway_name_for_backend(backend)
    target = broker
    capabilities = BrokerCapabilities(
        backend=backend,
        gateway=gateway,
        persistent=_has_capability(target, gateway, "list_orders"),
        paged_queries=_has_capability(target, gateway, "list_orders_page"),
        cash_flows=_has_capability(target, gateway, "list_cash_flows"),
        account_summary=_has_capability(target, gateway, "account_summary"),
        daily_report=_has_capability(target, gateway, "get_daily_report"),
        pnl_stats=_has_capability(target, gateway, "get_pnl_stats"),
        strategy_evaluation=_has_capability(target, gateway, "get_strategy_evaluation"),
    )
    return capabilities.to_dict()


def _normalize_backend(backend: str) -> str:
    value = (backend or "memory").strip().lower()
    if value == "sqlalchemy":
        return "sqlalchemy"
    if value in {"mysql", "sqlite", "memory"}:
        return value
    return value


def _gateway_name_for_backend(backend: str) -> str:
    if backend in {"sqlalchemy", "mysql"}:
        return "SQLAlchemyPaperBroker"
    if backend == "sqlite":
        return "SQLitePaperBroker"
    if backend == "memory":
        return "PaperBroker"
    return "UnknownBroker"


def _has_capability(broker: TradingBroker | None, gateway: str, method_name: str) -> bool:
    if broker is not None:
        return hasattr(broker, method_name)
    capability_map = {
        "PaperBroker": set(),
        "SQLitePaperBroker": {"list_orders", "list_cash_flows", "account_summary", "reset"},
        "SQLAlchemyPaperBroker": {
            "list_orders",
            "list_orders_page",
            "list_cash_flows",
            "list_cash_flows_page",
            "account_summary",
            "reset",
            "get_daily_report",
            "get_pnl_stats",
            "get_strategy_evaluation",
        },
    }
    return method_name in capability_map.get(gateway, set())
