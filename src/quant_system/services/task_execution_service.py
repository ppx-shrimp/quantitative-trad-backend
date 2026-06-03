from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from quant_system.api.pagination import PageParams, PageResult, paginate
from quant_system.db.database import SessionLocal, init_sqlalchemy_tables
from quant_system.db.models import TaskExecutionRecordModel


class TaskExecutionService:
    """任务执行记录服务。

    用于记录定时任务、手动触发的数据任务、自动交易任务的每次运行结果。
    """

    def __init__(self) -> None:
        self.initialize()

    def initialize(self) -> None:
        init_sqlalchemy_tables()

    def start_task(
        self,
        task_name: str,
        task_type: str,
        trigger_type: str = "manual",
        params: dict[str, Any] | None = None,
    ) -> str:
        now = _now()
        execution_id = f"{task_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        with SessionLocal() as session:
            session.add(
                TaskExecutionRecordModel(
                    execution_id=execution_id,
                    task_name=task_name,
                    task_type=task_type,
                    trigger_type=trigger_type,
                    status="running",
                    started_at=now,
                    finished_at=None,
                    duration_ms=None,
                    params_json=_to_json(params or {}),
                    result_summary_json=None,
                    error_message=None,
                    candidate_count=None,
                    success_count=None,
                    failed_count=None,
                    accepted_count=None,
                    rejected_count=None,
                    order_count=None,
                    created_at=now,
                    updated_at=now,
                    created_by="system",
                    updated_by="system",
                )
            )
            session.commit()
        return execution_id

    def finish_success(self, execution_id: str, result: dict[str, Any] | None = None, *, status: str = "success") -> dict[str, Any]:
        result = result or {}
        now = _now()
        with SessionLocal() as session:
            record = self._get_record(session, execution_id)
            record.status = status
            record.finished_at = now
            record.duration_ms = _duration_ms(record.started_at, now)
            record.result_summary_json = _to_json(_summarize_result(result))
            record.error_message = None
            _apply_metrics(record, result)
            record.updated_at = now
            record.updated_by = "system"
            session.commit()
            return self._record_to_dict(record)

    def finish_failure(self, execution_id: str, error: Exception | str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        now = _now()
        error_message = str(error)
        if isinstance(error, Exception):
            error_message = f"{error_message}\n{traceback.format_exc()}"
        with SessionLocal() as session:
            record = self._get_record(session, execution_id)
            record.status = "failed"
            record.finished_at = now
            record.duration_ms = _duration_ms(record.started_at, now)
            record.result_summary_json = _to_json(_summarize_result(result or {})) if result else None
            record.error_message = error_message[:8000]
            if result:
                _apply_metrics(record, result)
            record.updated_at = now
            record.updated_by = "system"
            session.commit()
            return self._record_to_dict(record)

    def run_tracked(
        self,
        task_name: str,
        task_type: str,
        trigger_type: str,
        params: dict[str, Any] | None,
        fn,
    ) -> dict[str, Any]:
        execution_id = self.start_task(
            task_name=task_name,
            task_type=task_type,
            trigger_type=trigger_type,
            params=params,
        )
        try:
            result = fn()
            self.finish_success(execution_id, result if isinstance(result, dict) else {"result": result})
            if isinstance(result, dict):
                result["task_execution_id"] = execution_id
            return result
        except Exception as exc:
            self.finish_failure(execution_id, exc)
            raise

    def list_executions_page(
        self,
        page_params: PageParams,
        task_name: str | None = None,
        task_type: str | None = None,
        status: str | None = None,
        trigger_type: str | None = None,
    ) -> PageResult:
        stmt = select(TaskExecutionRecordModel)
        if task_name:
            stmt = stmt.where(TaskExecutionRecordModel.task_name == task_name)
        if task_type:
            stmt = stmt.where(TaskExecutionRecordModel.task_type == task_type)
        if status:
            stmt = stmt.where(TaskExecutionRecordModel.status == status)
        if trigger_type:
            stmt = stmt.where(TaskExecutionRecordModel.trigger_type == trigger_type)
        stmt = stmt.order_by(TaskExecutionRecordModel.started_at.desc())
        with SessionLocal() as session:
            return paginate(session, stmt, None, page_params, to_dict_fn=self._record_to_dict)

    def latest_executions(self, limit_per_task: int = 1) -> dict[str, Any]:
        with SessionLocal() as session:
            task_names = session.scalars(
                select(TaskExecutionRecordModel.task_name)
                .group_by(TaskExecutionRecordModel.task_name)
                .order_by(TaskExecutionRecordModel.task_name)
            ).all()
            items: dict[str, list[dict[str, Any]]] = {}
            for task_name in task_names:
                rows = session.scalars(
                    select(TaskExecutionRecordModel)
                    .where(TaskExecutionRecordModel.task_name == task_name)
                    .order_by(TaskExecutionRecordModel.started_at.desc())
                    .limit(limit_per_task)
                ).all()
                items[task_name] = [self._record_to_dict(row) for row in rows]
            return {"count": len(items), "items": items}

    def recent_summary(self, limit: int = 10) -> dict[str, Any]:
        with SessionLocal() as session:
            recent_rows = session.scalars(
                select(TaskExecutionRecordModel)
                .order_by(TaskExecutionRecordModel.started_at.desc())
                .limit(limit)
            ).all()
            status_rows = session.execute(
                select(TaskExecutionRecordModel.status, func.count(TaskExecutionRecordModel.id))
                .group_by(TaskExecutionRecordModel.status)
            ).all()
            latest_failed = session.scalar(
                select(TaskExecutionRecordModel)
                .where(TaskExecutionRecordModel.status == "failed")
                .order_by(TaskExecutionRecordModel.started_at.desc())
                .limit(1)
            )
            return {
                "recent_count": len(recent_rows),
                "status_counts": {status: int(count) for status, count in status_rows},
                "latest_failed": self._record_to_dict(latest_failed) if latest_failed else None,
                "recent": [self._record_to_dict(row) for row in recent_rows],
            }

    def get_execution(self, execution_id: str) -> dict[str, Any]:
        with SessionLocal() as session:
            return self._record_to_dict(self._get_record(session, execution_id))

    def _get_record(self, session, execution_id: str) -> TaskExecutionRecordModel:
        record = session.scalar(
            select(TaskExecutionRecordModel).where(TaskExecutionRecordModel.execution_id == execution_id)
        )
        if record is None:
            raise ValueError(f"任务执行记录不存在：{execution_id}")
        return record

    def _record_to_dict(self, row: TaskExecutionRecordModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "execution_id": row.execution_id,
            "task_name": row.task_name,
            "task_type": row.task_type,
            "trigger_type": row.trigger_type,
            "status": row.status,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "duration_ms": row.duration_ms,
            "params": _from_json(row.params_json),
            "result_summary": _from_json(row.result_summary_json),
            "error_message": row.error_message,
            "candidate_count": row.candidate_count,
            "success_count": row.success_count,
            "failed_count": row.failed_count,
            "accepted_count": row.accepted_count,
            "rejected_count": row.rejected_count,
            "order_count": row.order_count,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(started_at: str, finished_at: str) -> float:
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
        return round((finish - start).total_seconds() * 1000, 2)
    except Exception:
        return 0.0


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _from_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "mode",
        "pools",
        "pool_code",
        "symbol",
        "period",
        "periods",
        "strategy_mode",
        "candidate_count",
        "symbol_count",
        "position_count",
        "accepted_count",
        "rejected_count",
        "success_count",
        "failed_count",
        "closed_count",
        "kept_count",
        "rows_count",
        "message",
        "status",
        "selected_count",
        "embedded_count",
        "upserted_count",
        "failed_batch_count",
    ]
    summary = {key: result.get(key) for key in keys if key in result}
    if "results" in result and isinstance(result["results"], list):
        summary["results_count"] = len(result["results"])
    return summary


def _apply_metrics(record: TaskExecutionRecordModel, result: dict[str, Any]) -> None:
    record.candidate_count = _first_int(result, ["candidate_count", "symbol_count", "position_count", "selected_count"])
    record.success_count = _first_int(result, ["success_count", "closed_count", "upserted_count", "embedded_count"])
    record.failed_count = _first_int(result, ["failed_count", "kept_count", "failed_batch_count"])
    record.accepted_count = _first_int(result, ["accepted_count", "closed_count"])
    record.rejected_count = _first_int(result, ["rejected_count", "kept_count"])
    record.order_count = _count_orders(result)


def _first_int(result: dict[str, Any], keys: list[str]) -> int | None:
    for key in keys:
        if key in result and result[key] is not None:
            try:
                return int(result[key])
            except (TypeError, ValueError):
                continue
    return None


def _count_orders(result: dict[str, Any]) -> int | None:
    items = result.get("results")
    if not isinstance(items, list):
        return None
    return sum(1 for item in items if isinstance(item, dict) and item.get("accepted"))
