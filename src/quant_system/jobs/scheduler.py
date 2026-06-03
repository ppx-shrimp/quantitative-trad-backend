from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from quant_system.core.config import settings

if TYPE_CHECKING:
    from quant_system.services.trading_service import TradingService


trading_service: TradingService | None = None
_last_job_results: dict[str, dict[str, Any]] = {}


def set_trading_service(service: TradingService) -> None:
    global trading_service
    trading_service = service


def _get_trading_service() -> TradingService:
    global trading_service
    if trading_service is None:
        from quant_system.services.trading_service import TradingService

        trading_service = TradingService()
    return trading_service


def _record_job_result(job_id: str, status: str, result: Any = None, error: str | None = None) -> None:
    _last_job_results[job_id] = {
        "job_id": job_id,
        "status": status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
        "error": error,
    }


def opening_buy_job() -> None:
    job_id = "opening_buy_job"
    params = {"strategy_mode": "strict"}
    try:
        from quant_system.services.task_execution_service import TaskExecutionService

        result = TaskExecutionService().run_tracked(
            task_name=job_id,
            task_type="auto_trade",
            trigger_type="scheduler",
            params=params,
            fn=lambda: _get_trading_service().run_opening_auto_buy(strategy_mode="strict", tracked=False),
        )
        _record_job_result(job_id, "ok", result=result)
        print(f"[scheduler] {job_id} result={result}")
    except Exception as exc:
        _record_job_result(job_id, "error", error=str(exc))
        print(f"[scheduler] {job_id} error={exc}")
        raise


def scheduled_close_job() -> None:
    job_id = "scheduled_close_job"
    params = {"strategy_mode": "strict", "mode": "force_close_all", "dry_run": False}
    try:
        from quant_system.services.task_execution_service import TaskExecutionService

        result = TaskExecutionService().run_tracked(
            task_name=job_id,
            task_type="auto_trade",
            trigger_type="scheduler",
            params=params,
            fn=lambda: _get_trading_service().run_scheduled_auto_close(strategy_mode="strict", mode="force_close_all", tracked=False),
        )
        _record_job_result(job_id, "ok", result=result)
        print(f"[scheduler] {job_id} result={result}")
    except Exception as exc:
        _record_job_result(job_id, "error", error=str(exc))
        print(f"[scheduler] {job_id} error={exc}")
        raise


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    open_hour, open_minute = settings.market_open_buy_time.split(":")
    close_hour, close_minute = settings.scheduled_close_time.split(":")
    scheduler.add_job(
        opening_buy_job,
        CronTrigger(day_of_week="mon-fri", hour=int(open_hour), minute=int(open_minute)),
        id="opening_buy_job",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_close_job,
        CronTrigger(day_of_week="mon-fri", hour=int(close_hour), minute=int(close_minute)),
        id="scheduled_close_job",
        replace_existing=True,
    )
    return scheduler


def get_scheduler_status(scheduler: BackgroundScheduler | None) -> dict:
    if scheduler is None:
        return {
            "configured": False,
            "running": False,
            "state": None,
            "timezone": settings.timezone,
            "jobs": [],
            "last_job_results": _last_job_results,
        }

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "last_result": _last_job_results.get(job.id),
        })

    return {
        "configured": True,
        "running": scheduler.running,
        "state": scheduler.state,
        "timezone": str(scheduler.timezone),
        "jobs": jobs,
        "last_job_results": _last_job_results,
    }
