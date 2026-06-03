from fastapi import FastAPI

from quant_system.api import routes
from quant_system.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="股票量化系统第一版外壳：分析、预测、热点新闻、模拟交易、定时平仓。",
    )
    app.include_router(routes.router, prefix="/api/v1")
    app.state.scheduler = None

    @app.on_event("startup")
    def startup_event() -> None:
        from quant_system.jobs.scheduler import create_scheduler
        from quant_system.runtime.selfcheck import run_startup_checks

        app.state.startup_check = run_startup_checks()
        if settings.dashboard_cache_enabled and settings.dashboard_cache_prewarm_on_startup:
            from quant_system.services.dashboard_cache_service import DashboardCacheService

            app.state.dashboard_cache_prewarm = DashboardCacheService().prewarm_async(reason="startup")
        app.state.scheduler = create_scheduler()
        app.state.scheduler.start()

    @app.on_event("shutdown")
    def shutdown_event() -> None:
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    def _startup_check() -> object:
        from quant_system.runtime.selfcheck import StartupCheckResult

        return getattr(app.state, "startup_check", StartupCheckResult())

    def _health_payload() -> dict:
        from quant_system.brokers.factory import describe_broker
        from quant_system.jobs.scheduler import get_scheduler_status
        from quant_system.services.risk_service import RiskService
        from quant_system.services.task_execution_service import TaskExecutionService

        startup = _startup_check()
        scheduler = getattr(app.state, "scheduler", None)
        risk = RiskService()
        try:
            task_execution_summary = TaskExecutionService().recent_summary(limit=5)
        except Exception as exc:
            task_execution_summary = {"available": False, "error": str(exc), "recent": []}
        return {
            "status": startup.status or "ok",
            "mode": settings.trade_mode,
            "app_version": app.version,
            "backend": startup.backend,
            "dialect": startup.dialect,
            "database_url": startup.database_url,
            "checked_at": startup.checked_at,
            "last_error": startup.last_error,
            "runtime": {
                "environment": settings.environment,
                "trade_mode": settings.trade_mode,
                "paper_broker_backend": settings.paper_broker_backend,
                "data_provider": settings.data_provider,
                "timezone": settings.timezone,
                "market_open_buy_time": settings.market_open_buy_time,
                "scheduled_close_time": settings.scheduled_close_time,
            },
            "trade_safety": {
                "allow_live_trading": settings.allow_live_trading,
                "allow_manual_open": settings.allow_manual_open,
                "allow_manual_close": settings.allow_manual_close,
                "allow_auto_buy": settings.allow_auto_buy,
                "allow_auto_close": settings.allow_auto_close,
                "max_order_amount": settings.max_order_amount,
                "max_daily_buy_amount": settings.max_daily_buy_amount,
                "max_position_amount": settings.max_position_amount,
                "max_positions": settings.auto_trade_max_positions,
            },
            "risk": risk.risk_status(),
            "broker": describe_broker(),
            "scheduler": get_scheduler_status(scheduler),
            "task_executions": task_execution_summary,
            "cache": {
                **getattr(startup, "cache", {}),
                "dashboard": getattr(app.state, "dashboard_cache_prewarm", None),
            },
            "checks": [
                {
                    "name": item.name,
                    "status": item.status,
                    "message": item.message,
                    "duration_ms": getattr(item, "duration_ms", 0.0),
                }
                for item in startup.checks
            ],
        }

    @app.get("/health")
    def health() -> dict:
        return _health_payload()

    @app.get("/gstack-health")
    def gstack_health() -> dict:
        return _health_payload()

    @app.get("/api/v1/system/status")
    def system_status() -> dict:
        return _health_payload()

    @app.get("/api/v1/system/health")
    def system_health() -> dict:
        return _health_payload()

    return app


app = create_app()
