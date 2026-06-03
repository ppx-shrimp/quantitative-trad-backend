from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Callable

from quant_system.core.config import settings


@dataclass
class CheckItem:
    name: str
    status: str
    message: str
    duration_ms: float = 0.0


@dataclass
class StartupCheckResult:
    checked_at: str = ""
    status: str = "ok"
    backend: str = ""
    dialect: str = ""
    database_url: str = ""
    trade_mode: str = ""
    paper_broker_backend: str = ""
    data_provider: str = ""
    timezone: str = ""
    cache: dict = field(default_factory=dict)
    checks: list[CheckItem] = field(default_factory=list)
    last_error: str | None = None


def run_startup_checks() -> StartupCheckResult:
    """运行轻量启动自检。

    这里故意避免在模块导入阶段加载数据库 engine/model，防止后端启动前被
    MySQL 或 SQLAlchemy 初始化链路卡住。真正检查只在 startup 阶段执行。
    """
    now = datetime.now(timezone.utc).isoformat()
    result = StartupCheckResult(checked_at=now)
    result.database_url = _mask_database_url(settings.database_url or settings.database_path)
    result.backend = _detect_backend()
    result.dialect = _detect_database_dialect()
    result.trade_mode = settings.trade_mode
    result.paper_broker_backend = settings.paper_broker_backend
    result.data_provider = settings.data_provider
    result.timezone = settings.timezone
    errors: list[str] = []

    _run_check(result, errors, "configuration", _check_configuration)
    _run_check(result, errors, "database_connectivity", _check_database_connectivity)
    _run_check(result, errors, "kline_cache", _check_kline_cache)
    _run_check(result, errors, "default_account", _check_default_account)
    _run_check(result, errors, "stock_pools", _check_stock_pools)
    result.cache = _kline_cache_status()

    result.status = "ok" if not errors else "degraded"
    result.last_error = "; ".join(errors) if errors else None
    return result


def _detect_backend() -> str:
    url = (settings.database_url or "").lower()
    if settings.database_backend in {"mysql", "sqlite"}:
        return settings.database_backend
    if url.startswith("mysql"):
        return "mysql"
    if url.startswith("sqlite"):
        return "sqlite"
    return "sqlite" if not settings.database_url else "unknown"


def _detect_database_dialect() -> str:
    url = (settings.database_url or "").lower()
    if url.startswith("mysql"):
        return "mysql"
    if url.startswith("sqlite") or not settings.database_url:
        return "sqlite"
    return settings.database_backend or "unknown"


def _mask_database_url(database_url: str) -> str:
    if "://" not in database_url or "@" not in database_url:
        return database_url
    scheme, rest = database_url.split("://", 1)
    credentials, host_part = rest.split("@", 1)
    if ":" not in credentials:
        return f"{scheme}://***@{host_part}"
    username, _password = credentials.split(":", 1)
    return f"{scheme}://{username}:***@{host_part}"


def _run_check(result: StartupCheckResult, errors: list[str], name: str, fn: Callable[[], CheckItem]) -> None:
    started_at = perf_counter()
    try:
        item = fn()
        item.duration_ms = round((perf_counter() - started_at) * 1000, 2)
        result.checks.append(item)
        if item.status not in {"ok", "skip"}:
            errors.append(item.message)
    except Exception as exc:
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        message = f"{name} 检查失败：{exc}"
        result.checks.append(CheckItem(name=name, status="error", message=message, duration_ms=duration_ms))
        errors.append(message)


def _check_configuration() -> CheckItem:
    issues: list[str] = []
    if settings.trade_mode not in {"paper", "backtest", "live"}:
        issues.append(f"未知 trade_mode={settings.trade_mode}")
    if settings.trade_mode == "live" and not settings.allow_live_trading:
        issues.append("trade_mode=live 但 QUANT_ALLOW_LIVE_TRADING 未开启")
    if settings.paper_broker_backend not in {"sqlite", "sqlalchemy", "mysql", "memory"}:
        issues.append(f"未知 paper_broker_backend={settings.paper_broker_backend}")
    if settings.database_backend not in {"sqlite", "mysql"}:
        issues.append(f"未知 database_backend={settings.database_backend}")
    if not settings.market_open_buy_time or ":" not in settings.market_open_buy_time:
        issues.append("market_open_buy_time 格式应为 HH:MM")
    if not settings.scheduled_close_time or ":" not in settings.scheduled_close_time:
        issues.append("scheduled_close_time 格式应为 HH:MM")

    if issues:
        return CheckItem(name="configuration", status="warn", message="；".join(issues))
    return CheckItem(
        name="configuration",
        status="ok",
        message=(
            f"配置已加载：environment={settings.environment}, trade_mode={settings.trade_mode}, "
            f"paper_broker_backend={settings.paper_broker_backend}, "
            f"allow_auto_buy={settings.allow_auto_buy}, allow_auto_close={settings.allow_auto_close}, "
            f"data_provider={settings.data_provider}, timezone={settings.timezone}。"
        ),
    )


def _check_database_connectivity() -> CheckItem:
    from sqlalchemy import text

    from quant_system.db.database import engine

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return CheckItem(name="database_connectivity", status="ok", message=f"当前 dialect={engine.dialect.name}，数据库连接成功。")


def _kline_cache_status() -> dict:
    from quant_system.services.kline_cache_service import KlineCacheService

    return KlineCacheService().status()


def _check_kline_cache() -> CheckItem:
    status = _kline_cache_status()
    return CheckItem(
        name="kline_cache",
        status=status.get("status", "warn"),
        message=str(status.get("message") or "Redis K 线缓存状态未知。"),
    )


def _check_default_account() -> CheckItem:
    from quant_system.db.database import SessionLocal
    from quant_system.db.models import PaperAccountModel

    with SessionLocal() as session:
        account = session.get(PaperAccountModel, "default")
        if account is None:
            return CheckItem(name="default_account", status="warn", message="未找到默认模拟账户，请先初始化数据库。")
        return CheckItem(
            name="default_account",
            status="ok",
            message=f"默认模拟账户已就绪，cash={float(account.cash):.2f}。",
        )


def _check_stock_pools() -> CheckItem:
    from sqlalchemy import func, select

    from quant_system.db.database import SessionLocal
    from quant_system.db.models import StockPoolModel

    with SessionLocal() as session:
        pool_count = int(session.scalar(select(func.count(StockPoolModel.id))) or 0)
        if pool_count == 0:
            return CheckItem(name="stock_pools", status="warn", message="未检测到股票池数据，服务可能尚未完成股票池初始化。")
        return CheckItem(name="stock_pools", status="ok", message=f"已检测到 {pool_count} 个股票池。")
